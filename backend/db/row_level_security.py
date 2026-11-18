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

**Why table ownership doesn't quietly defeat this (since 2026-11-06):** a table's owner
is exempt from its own RLS policies, so the app does not serve as the owner. Two roles
(config.py, backend/db/app_role.py): the owner runs migrations and cross-account
maintenance -- work that legitimately needs every account's rows and has no single
"current user" -- and is exempt by design; the runtime role the app serves with owns
nothing, so every policy applies to it, and the app refuses to start if it doesn't.

This replaced FORCE ROW LEVEL SECURITY plus a disable/re-enable bracket around migrations
and maintenance. That bracket was global DDL: while it was open, *every* connection --
including the app serving other requests -- saw every account's rows. RLS is now never
switched off; it is only ever armed."""

from __future__ import annotations

import logging
from enum import StrEnum
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


# ai: claude-opus-5-5 | 2026-11-06
def release_owner_from_rls(conn: Any) -> None:
    """Undo FORCE ROW LEVEL SECURITY on every tenant table, the first step of every migrate.

    Databases migrated before 2026-11-06 have FORCE on, which would subject the owner --
    and so migrate_database's own backfills -- to the policies. NO FORCE only changes how
    the *owner* is treated; RLS stays enabled for every other role, the runtime role
    included. Idempotent."""
    cur = conn.cursor()
    for table in tenant_tables(cur):
        cur.execute(
            sql.SQL("ALTER TABLE {} NO FORCE ROW LEVEL SECURITY").format(sql.Identifier(table))
        )
    conn.commit()


# ai: claude-opus-5-5 | 2026-11-06 -- was enable_and_force(); FORCE is now NO FORCE (see
# the module docstring) and the name says what it does.
def arm_row_level_security(conn: Any) -> None:
    """Enable RLS with the tenant policy on every tenant table, after migrations ran.

    Applies to every role except the owner (and superusers / BYPASSRLS roles, which the
    runtime role is checked never to be). Only ever restricts DML, so later schema
    changes by the owner don't need special-casing here."""
    cur = conn.cursor()
    condition = "user_id = NULLIF(current_setting('app.current_user_id', true), '')::integer"
    tables = tenant_tables(cur)
    for table in tables:
        identifier = sql.Identifier(table)
        cur.execute(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(identifier))
        cur.execute(sql.SQL("ALTER TABLE {} NO FORCE ROW LEVEL SECURITY").format(identifier))
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
    logger.info("Row-level security armed on %d tenant tables.", len(tables))


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


# ai: claude-opus-5-5 | 2026-11-18 -- also checks the session's credential generation
class SessionIdentity(StrEnum):
    """What set_current_user_if_active found for a login session's identity."""

    ACTIVE = "active"
    ACCOUNT_UNAVAILABLE = "account_unavailable"  # disabled by an admin, or deleted
    SESSION_REVOKED = "session_revoked"  # the password changed after this session began


def set_current_user_if_active(
    cur: Any, user_id: int, session_version: int | None
) -> SessionIdentity:
    """set_current_user for an identity taken from a login session: only if that account
    still exists, isn't disabled (plan 4.5), and its session_version still equals the one
    the session was issued with (a password change or reset increments it, which ends
    every session issued before). Otherwise the identity is cleared (no tenant rows) and
    the result says why. One statement, so checking costs no extra round trip on every
    request; the same connection-session scope as set_current_user."""
    cur.execute(
        "SELECT set_config('app.current_user_id',"
        " CASE WHEN a.session_version = %s THEN %s ELSE '' END, false) <> '' AS valid,"
        " a.id IS NOT NULL AS account_active"
        " FROM (SELECT 1) AS one"
        " LEFT JOIN user_accounts a ON a.id = %s AND a.disabled_at IS NULL",
        (session_version, str(user_id), user_id),
    )
    row = cur.fetchone()
    if row["valid"]:
        return SessionIdentity.ACTIVE
    if row["account_active"]:
        return SessionIdentity.SESSION_REVOKED
    return SessionIdentity.ACCOUNT_UNAVAILABLE
