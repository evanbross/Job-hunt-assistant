# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-03
"""A snapshot of the test database right after migrate_database(), so the pg_dsn
fixture can put every test back in that state without re-running migrate_database().

migrate_database() runs ~1,900 lines of idempotent migrations plus seeding, and pg_dsn
used to run it twice per test (before and after a TRUNCATE) -- most of a
19-minute suite. Now it runs once per session; each test instead truncates
every table and reloads the seed rows and sequence positions captured here.

That is only equivalent if the schema itself is untouched, and some tests do
change it: migration tests turn row-level security off (disable_for_migrations)
or revert a column so they can watch migrate_database() fix it, and the per-test
migrate_database() used to repair whatever they left behind. So each reset first
compares a fingerprint of the schema (columns, defaults, indexes, constraints,
RLS flags, policies, grants, sequences) with the one captured here, and runs
migrate_database() again only when it differs.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from graphlib import TopologicalSorter
from typing import Any

import psycopg2
from psycopg2 import sql

# One row per schema fact; hashed so a reset compares a single value.
_SCHEMA_FINGERPRINT_SQL = """
SELECT md5(string_agg(item, E'\\n' ORDER BY item)) FROM (
    SELECT format('column %s.%s %s notnull=%s default=%s', c.relname, a.attname,
                  format_type(a.atttypid, a.atttypmod), a.attnotnull,
                  pg_get_expr(d.adbin, d.adrelid)) AS item
      FROM pg_attribute a
      JOIN pg_class c ON c.oid = a.attrelid
      LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
     WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r'
       AND a.attnum > 0 AND NOT a.attisdropped
    UNION ALL
    SELECT format('table %s rls=%s force=%s acl=%s', relname, relrowsecurity,
                  relforcerowsecurity, relacl)
      FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r', 'S')
    UNION ALL
    SELECT 'index ' || indexdef FROM pg_indexes WHERE schemaname = 'public'
    UNION ALL
    SELECT format('constraint %s %s %s', conrelid::regclass, conname, pg_get_constraintdef(oid))
      FROM pg_constraint WHERE connamespace = 'public'::regnamespace
    UNION ALL
    SELECT format('policy %s %s %s %s %s', tablename, policyname, cmd, qual, with_check)
      FROM pg_policies WHERE schemaname = 'public'
) schema_items
"""


def describe_other_sessions(conn: Any) -> str:
    """Every other session on this database, for an error message: the usual
    reason a reset can't get its locks is an earlier test that checked a
    connection out, left a transaction open, and never closed it (the pool
    keeps a checked-out connection, open transaction and all, until close())."""
    cur = conn.cursor()
    cur.execute(
        "SELECT pid, state, now() - xact_start, left(regexp_replace(query, '\\s+', ' ', 'g'), 200)"
        " FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()"
        " ORDER BY xact_start NULLS LAST"
    )
    return "\n".join(
        f"  pid {pid} [{state}] transaction open for {age}: {query}" for pid, state, age, query in cur.fetchall()
    ) or "  (none)"


def truncate_every_table(conn: Any) -> None:
    """Empty every table in the public schema (uncommitted). The table list
    comes from the catalog, not a hand-kept copy that can miss new tables."""
    cur = conn.cursor()
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
    tables = [row[0] for row in cur.fetchall()]
    cur.execute(
        sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
            sql.SQL(", ").join(sql.Identifier(table) for table in tables)
        )
    )


def schema_fingerprint(conn: Any) -> str:
    cur = conn.cursor()
    cur.execute(_SCHEMA_FINGERPRINT_SQL)
    return str(cur.fetchone()[0])


@dataclass(frozen=True)
class _TableSeed:
    table: str
    columns: tuple[str, ...]
    copy_data: str
    # FORCE ROW LEVEL SECURITY makes even the owning role subject to the
    # policies, so with no app.current_user_id set every tenant row would be
    # invisible to COPY TO and rejected by COPY FROM. The snapshot and restore
    # lift it inside their own transaction only.
    forced_rls: bool


def _copy_sql(template: str, table: str, columns: tuple[str, ...]) -> sql.Composed:
    """*template* is "COPY {} ({}) TO STDOUT" or "COPY {} ({}) FROM STDIN"."""
    return sql.SQL(template).format(
        sql.Identifier(table), sql.SQL(", ").join(sql.Identifier(column) for column in columns)
    )


@dataclass(frozen=True)
class _SequenceState:
    name: str  # schema-qualified, quoted -- castable to regclass
    last_value: int
    is_called: bool


def _set_forced_rls(cur: Any, table: str, forced: bool) -> None:
    clause = "FORCE" if forced else "NO FORCE"
    cur.execute(
        sql.SQL("ALTER TABLE {} {} ROW LEVEL SECURITY").format(sql.Identifier(table), sql.SQL(clause))
    )


class SeededDatabaseSnapshot:
    """What migrate_database() left in the database: its schema fingerprint, every
    table's seed rows (in foreign-key order), and every sequence's position."""

    def __init__(
        self,
        fingerprint: str,
        seeds: tuple[_TableSeed, ...],
        sequences: tuple[_SequenceState, ...],
    ) -> None:
        self.fingerprint = fingerprint
        self._seeds = seeds
        self._sequences = sequences

    @classmethod
    def capture(cls, conn: Any) -> SeededDatabaseSnapshot:
        """Read the current state. Leaves the database exactly as it found it:
        everything runs in one transaction that is rolled back."""
        cur = conn.cursor()
        try:
            fingerprint = schema_fingerprint(conn)
            cur.execute(
                "SELECT relname, relforcerowsecurity FROM pg_class"
                " WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
            )
            forced = dict(cur.fetchall())
            seeds = tuple(
                seed for table in cls._parents_first(cur, list(forced))
                if (seed := cls._capture_table(cur, table, forced[table])) is not None
            )
            cur.execute(
                "SELECT format('%I.%I', schemaname, sequencename) FROM pg_sequences"
                " WHERE schemaname = 'public' ORDER BY 1"
            )
            sequences = []
            for (name,) in cur.fetchall():
                cur.execute(sql.SQL("SELECT last_value, is_called FROM {}").format(sql.SQL(name)))
                last_value, is_called = cur.fetchone()
                sequences.append(_SequenceState(name, int(last_value), bool(is_called)))
        finally:
            conn.rollback()  # undoes the NO FORCE lifts _capture_table made
        return cls(fingerprint, seeds, tuple(sequences))

    @staticmethod
    def _parents_first(cur: Any, tables: list[str]) -> list[str]:
        """Tables ordered so every foreign key's target is loaded before the
        table that references it (self-references are checked per statement,
        so they don't constrain the order)."""
        cur.execute(
            "SELECT child.relname, parent.relname FROM pg_constraint con"
            " JOIN pg_class child ON child.oid = con.conrelid"
            " JOIN pg_class parent ON parent.oid = con.confrelid"
            " WHERE con.contype = 'f' AND child.relnamespace = 'public'::regnamespace"
            " AND child.oid <> parent.oid"
        )
        graph: dict[str, set[str]] = {table: set() for table in tables}
        for child, parent in cur.fetchall():
            graph[child].add(parent)
        return list(TopologicalSorter(graph).static_order())

    @staticmethod
    def _capture_table(cur: Any, table: str, forced_rls: bool) -> _TableSeed | None:
        if forced_rls:
            _set_forced_rls(cur, table, forced=False)
        cur.execute(sql.SQL("SELECT EXISTS (SELECT 1 FROM {})").format(sql.Identifier(table)))
        if not cur.fetchone()[0]:
            return None
        # Generated columns can't be loaded back with COPY FROM; there are none
        # today, but leaving them out keeps the round trip valid if one appears.
        cur.execute(
            "SELECT attname FROM pg_attribute"
            " WHERE attrelid = (SELECT oid FROM pg_class"
            "                   WHERE relname = %s AND relnamespace = 'public'::regnamespace)"
            " AND attnum > 0 AND NOT attisdropped AND attgenerated = '' ORDER BY attnum",
            (table,),
        )
        columns = tuple(row[0] for row in cur.fetchall())
        buffer = io.StringIO()
        copy_out = _copy_sql("COPY {} ({}) TO STDOUT", table, columns)
        cur.copy_expert(copy_out.as_string(cur.connection), buffer)
        return _TableSeed(table, columns, buffer.getvalue(), forced_rls)

    def restore(self, conn: Any) -> None:
        """Empty every table and reload the captured rows and sequence
        positions, as one committed transaction."""
        cur = conn.cursor()
        try:
            truncate_every_table(conn)
            for seed in self._seeds:
                if seed.forced_rls:
                    _set_forced_rls(cur, seed.table, forced=False)
                # COPY FROM writes identity columns as given (like OVERRIDING
                # SYSTEM VALUE), so seeded ids come back unchanged.
                copy_in = _copy_sql("COPY {} ({}) FROM STDIN", seed.table, seed.columns)
                cur.copy_expert(copy_in.as_string(conn), io.StringIO(seed.copy_data))
                if seed.forced_rls:
                    _set_forced_rls(cur, seed.table, forced=True)
            for sequence in self._sequences:
                cur.execute(
                    "SELECT setval(%s::regclass, %s, %s)",
                    (sequence.name, sequence.last_value, sequence.is_called),
                )
            conn.commit()
        except psycopg2.Error:
            conn.rollback()
            raise
