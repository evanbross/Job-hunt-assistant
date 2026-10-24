# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Postgres row-level security: a database-enforced backstop behind every repository's
own `WHERE user_id = %s` filtering.

Every repository already filters by `user_id`. RLS exists for what that layer cannot
catch: a query that forgets the filter (routes/voice.py's LLM-generated SQL never
mentioned `user_id` at all -- see the fix alongside this module), a future repository
method that copies an existing one without editing the WHERE clause, or a join that
filters one side but not the other. Policies fail closed: no session variable means no
rows, not all rows.

**How a request becomes "the current user" to Postgres:** `backend/utils/utils.py`'s
`get_repos()` sets the session-local GUC `app.current_user_id` right after opening each
connection (from an explicit `user_id` argument, or from the Flask session in request
context). Every policy checks that GUC against the table's own `user_id` column.

**Why table ownership doesn't quietly defeat this:** by default a table's owner is exempt
from its own RLS policies -- and the app's one DB role owns every table. `FORCE ROW LEVEL
SECURITY` removes that exemption, so the app's *normal* runtime connections are bound by
the same policies as anyone else. That would also block `backend/db/schema.py`'s own
migrations, which legitimately need to see every account's rows (backfills, one-time
seeding) and have no single "current user" to set. `disable_for_migrations()` /
`enable_and_force()` bracket that unrestricted work: schema.py disables RLS before running
migrations and seed data, then this module re-arms it as the last step of `init_db()` --
every startup, not just the first, since a fresh connection has no session state carried
over from a previous run."""

from __future__ import annotations

import logging
from typing import Any

from psycopg2 import sql

logger = logging.getLogger(__name__)

# Views whose underlying tables are RLS-protected, made to evaluate those policies as the
# querying role (not the view owner) -- see PostgreSQL's CREATE VIEW ... security_invoker.
# Without this a view owned by the (table-owning) app role would apply RLS using the
# owner's exemption rather than the actual connected session, silently defeating it.
_SECURITY_INVOKER_VIEWS: tuple[str, ...] = ("latest_application_stage",)

_POLICY_NAME = "tenant_isolation"

# Tenant-scoped (has a NOT NULL user_id, is covered by
# tests/test_tenant_isolation_matrix.py's schema check) but excluded from RLS: its defining
# operation is looking a row up by a high-entropy secret *before* any tenant identity is
# known -- extension_auth.token_service.ExtensionTokenService.authenticate() must find
# whichever account a presented bearer token belongs to, the same way routes/session_auth.py
# looks up user_accounts by email/reset-token with no session yet. RLS would make that
# lookup return nothing, since app.current_user_id can't be set to an identity that is the
# very thing being determined. The table's own repository-level `WHERE user_id = %s`
# filtering (verified in test_tenant_isolation_matrix.py) is what protects its ordinary
# per-account CRUD routes (list/create/revoke your own tokens).
_EXCLUDED_FROM_RLS: frozenset[str] = frozenset({"extension_tokens"})


def tenant_tables(cur: Any) -> list[str]:
    """Every table with a NOT NULL `user_id` column, other than `_EXCLUDED_FROM_RLS` --
    the same test a new table has to pass in tests/test_tenant_isolation_matrix.py, read
    from the schema rather than duplicated as a hardcoded list here (a table added there
    and forgotten here would silently ship without this backstop)."""
    cur.execute(
        "SELECT table_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND column_name = 'user_id' AND is_nullable = 'NO'"
        " ORDER BY table_name"
    )
    return [row["table_name"] for row in cur.fetchall() if row["table_name"] not in _EXCLUDED_FROM_RLS]


def disable_for_migrations(conn: Any) -> None:
    """Turn RLS off on every tenant table before schema.py's migrations/seed data run.

    Idempotent and safe on a fresh install too: DISABLE on a table that never had RLS
    enabled is a no-op. Must run after tables exist but before any migration that reads or
    writes across more than one account."""
    cur = conn.cursor()
    for table in tenant_tables(cur):
        cur.execute(
            sql.SQL("ALTER TABLE {} DISABLE ROW LEVEL SECURITY").format(sql.Identifier(table))
        )
    conn.commit()


def enable_and_force(conn: Any) -> None:
    """(Re-)arm RLS on every tenant table: the last step of init_db(), after migrations
    and seeding have run with it off. Only ever restricts DML (SELECT/INSERT/UPDATE/
    DELETE) -- a later ALTER TABLE by this same owning role is unaffected by its own
    policies, so future schema changes don't need special-casing here."""
    cur = conn.cursor()
    condition = "user_id = NULLIF(current_setting('app.current_user_id', true), '')::integer"
    for table in tenant_tables(cur):
        identifier = sql.Identifier(table)
        cur.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(identifier))
        cur.execute(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(identifier))
        cur.execute(
            "SELECT 1 FROM pg_policies WHERE schemaname = 'public'"
            " AND tablename = %s AND policyname = %s",
            (table, _POLICY_NAME),
        )
        if cur.fetchone() is None:
            cur.execute(
                sql.SQL("CREATE POLICY {} ON {} USING ({cond}) WITH CHECK ({cond})").format(
                    sql.Identifier(_POLICY_NAME), identifier, cond=sql.SQL(condition)
                )
            )
    for view in _SECURITY_INVOKER_VIEWS:
        cur.execute(
            sql.SQL("ALTER VIEW {} SET (security_invoker = true)").format(sql.Identifier(view))
        )
    conn.commit()
    logger.info("Row-level security armed on %d tenant tables.", len(tenant_tables(cur)))


def set_current_user(cur: Any, user_id: int | None) -> None:
    """Set (or clear) the session GUC a connection's RLS policies read. `is_local=false`
    (the third argument) makes it last for the connection's session rather than just the
    current transaction -- get_repos() runs this outside any transaction (autocommit,
    see _ReconnectingPgConn.outside_transaction) specifically so a later
    `conn.rollback()` elsewhere in the request can't silently undo it (a rollback only
    undoes a SET that is still part of the same, uncommitted transaction)."""
    cur.execute(
        "SELECT set_config('app.current_user_id', %s, false)",
        (str(user_id) if user_id is not None else "",),
    )
