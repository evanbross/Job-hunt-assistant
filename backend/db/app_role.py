# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""The runtime database role: the login the app serves requests with.

Row-level security (backend/db/row_level_security.py) only protects anything if the
role it is checked against is subject to it. A table's owner, a superuser, and a
BYPASSRLS role all skip it. So the app does not serve with the role that owns the
schema; the owner (config.DATABASE_OWNER_URL) runs migrations and cross-account
maintenance, and the role named in config.DATABASE_URL gets plain data access only.

`ensure_app_role` runs inside migrate_database, as the owner. `assert_subject_to_rls`
runs before an app serves traffic, as the runtime role, and refuses to start if the
role would silently skip RLS -- the misconfiguration this whole split exists to rule
out would otherwise look exactly like a working app.

Added 2026-11-06 (docs/cloud-deployment-readiness-plan.md 1.6).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

from psycopg2 import sql

logger = logging.getLogger(__name__)

# Tables the runtime role may read but never write.
_READ_ONLY_FOR_APP: tuple[str, ...] = ("schema_state",)


class AppRoleConfigError(RuntimeError):
    """The runtime role is missing, misconfigured, or would bypass row-level security."""


@dataclass(frozen=True)
class RoleCredentials:
    user: str
    password: str


def credentials_from_url(dsn: str) -> RoleCredentials:
    """The user and password a Postgres URL logs in with."""
    parsed = urlparse(dsn)
    user = unquote(parsed.username or "")
    if not user:
        raise AppRoleConfigError("The app database URL (DATABASE_URL) names no user.")
    return RoleCredentials(user=user, password=unquote(parsed.password or ""))


def ensure_app_role(conn: Any, app_dsn: str) -> None:
    """Create or update the runtime role named in `app_dsn` and grant it data access.

    Runs as the schema owner, after migrations, on every migrate: re-granting covers
    tables a migration just added, and re-setting the password keeps it equal to the
    deployment's DATABASE_URL (the one place it is configured). Raises
    AppRoleConfigError instead of logging, unlike the optional voice-query role: an app
    that cannot serve as a non-owner must not start at all.
    """
    creds = credentials_from_url(app_dsn)
    if not creds.password:
        raise AppRoleConfigError("The app database URL (DATABASE_URL) has no password.")
    cur = conn.cursor()
    cur.execute("SELECT current_user AS owner, current_database() AS database")
    session = cur.fetchone()
    owner, database = str(session["owner"]), str(session["database"])
    if creds.user == owner:
        raise AppRoleConfigError(
            f"DATABASE_URL logs in as {owner!r}, the role that owns the schema. The owner is"
            " exempt from row-level security, so the app must serve with a different role:"
            " set DATABASE_URL (or POSTGRES_APP_USER/POSTGRES_APP_PASSWORD) to a separate"
            " user. migrate_database creates it."
        )

    role = sql.Identifier(creds.user)
    cur.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (creds.user,)
    )
    existing = cur.fetchone()
    if existing is None:
        cur.execute(
            sql.SQL(
                "CREATE ROLE {} WITH LOGIN PASSWORD %s"
                " NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB"
            ).format(role),
            (creds.password,),
        )
        logger.info("Created the app's runtime database role %s.", creds.user)
    else:
        if existing["rolsuper"] or existing["rolbypassrls"]:
            raise AppRoleConfigError(
                f"The app's database role {creds.user!r} is a superuser or has BYPASSRLS, so"
                " row-level security would not apply to it. Use an ordinary role."
            )
        cur.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD %s").format(role), (creds.password,)
        )

    cur.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database), role
        )
    )
    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
    cur.execute(
        sql.SQL(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"
        ).format(role)
    )
    cur.execute(
        sql.SQL("GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {}").format(role)
    )
    for table in _READ_ONLY_FOR_APP:
        cur.execute(
            sql.SQL("REVOKE INSERT, UPDATE, DELETE ON {} FROM {}").format(
                sql.Identifier(table), role
            )
        )
    conn.commit()


# ai: claude-opus-5-5 | 2026-11-06 -- one query behind both directions of the check.
def _rls_exemption(conn: Any) -> tuple[str, str | None]:
    """The connected role's name, and why row-level security skips it (None if it
    doesn't). Read from the live connection, not configuration, so it holds no matter
    how the URL was assembled."""
    cur = conn.cursor()
    cur.execute(
        "SELECT r.rolname, r.rolsuper, r.rolbypassrls,"
        " EXISTS (SELECT 1 FROM pg_tables t WHERE t.schemaname = 'public'"
        "         AND t.tableowner = r.rolname) AS owns_tables"
        " FROM pg_roles r WHERE r.rolname = current_user"
    )
    row = cur.fetchone()
    conn.rollback()
    reason = (
        "is a superuser" if row["rolsuper"]
        else "has BYPASSRLS" if row["rolbypassrls"]
        else "owns the schema's tables" if row["owns_tables"]
        else None
    )
    return str(row["rolname"]), reason


def assert_subject_to_rls(conn: Any) -> None:
    """Refuse to serve with a role that row-level security would not apply to: a
    DATABASE_URL that points at the owner, a superuser, or a BYPASSRLS role."""
    name, reason = _rls_exemption(conn)
    if reason is not None:
        raise AppRoleConfigError(
            f"Refusing to serve: the app's database role {name!r} {reason}, so row-level"
            " security would not apply to it. Point DATABASE_URL at the runtime role that"
            " migrate_database creates, and keep the owner in DATABASE_OWNER_URL."
        )


# ai: claude-opus-5-5 | 2026-11-06
def assert_exempt_from_rls(conn: Any) -> None:
    """Refuse cross-account maintenance on a role row-level security applies to.

    The inverse mistake of the one above, and a quieter one: run as the runtime role,
    the account-deletion and retention sweep would see no account's rows and delete
    nothing, every time, without an error."""
    name, reason = _rls_exemption(conn)
    if reason is None:
        raise AppRoleConfigError(
            f"Refusing cross-account maintenance as {name!r}: row-level security applies"
            " to it, so it would only see rows no account owns. Give this process the"
            " schema owner's URL (DATABASE_OWNER_URL)."
        )
