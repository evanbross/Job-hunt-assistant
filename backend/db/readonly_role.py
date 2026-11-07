# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Provisions and connects to a dedicated SELECT-only Postgres role for the
voice-query feature (routes/voice.py). This app otherwise has a single
privileged DB role -- this is the first read-only credential in the project,
so LLM-generated SQL never runs with write access even if backend/sql_guard.py
has a bug."""

from __future__ import annotations

import logging
import secrets
from typing import Any

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import make_dsn

import config

logger = logging.getLogger(__name__)

READONLY_ROLE_NAME = "jobhunt_readonly"

# The exact set of objects backend/sql_guard.py's table allowlist permits.
# Kept here (not imported from sql_guard) since this module runs at DB
# bootstrap, before the rest of the voice-query feature is wired up.
READONLY_TABLES: tuple[str, ...] = (
    "jobs",
    "application_stages",
    "application_stage_statuses",
    "latest_application_stage",
)

_READONLY_PASSWORD_FILE = config.DATA_DIR / ".readonly_db_password"


# ai: claude-opus-5-5 | 2026-11-06 -- READONLY_DB_PASSWORD first (plan 2.1)
def get_or_create_readonly_password() -> str:
    """The jobhunt_readonly role's password.

    A deployment gives it as READONLY_DB_PASSWORD, so the migrate step that sets it
    and every app container that connects with it agree. (A container's own disk is
    read-only and thrown away, so a generated file would differ per container.) The
    desktop app, with neither, generates one into data/ once and reuses it -- no
    manual setup step.
    """
    if config.READONLY_DB_PASSWORD:
        return config.READONLY_DB_PASSWORD
    if _READONLY_PASSWORD_FILE.exists():
        password = _READONLY_PASSWORD_FILE.read_text(encoding="utf-8").strip()
        if password:
            return password
    password = secrets.token_urlsafe(24)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _READONLY_PASSWORD_FILE.write_text(password, encoding="utf-8")
    return password


# ai: claude-opus-5-5 | 2026-11-06 -- derived from DATABASE_URL, not POSTGRES_* parts
def get_readonly_database_url() -> str:
    """DSN for the voice-query feature: the database config.DATABASE_URL serves from
    (host, port, name, sslmode), as the SELECT-only jobhunt_readonly role.

    Derived from DATABASE_URL rather than the POSTGRES_HOST/... parts because a
    deployment gives DATABASE_URL whole and leaves the parts empty."""
    return make_dsn(
        config.DATABASE_URL, user=READONLY_ROLE_NAME, password=get_or_create_readonly_password()
    )


def ensure_readonly_role(conn: Any) -> None:
    """Idempotently create/update the jobhunt_readonly role and grant it
    SELECT on exactly READONLY_TABLES. Runs once at startup with the app's
    normal privileged connection, alongside create_tables/migrate_schema/seed_data.

    Role/table names are fixed constants, never user input -- built via
    sql.Identifier, matching this project's SQL-identifier-allowlisting
    convention (backend/db/repositories/*.py's _ALLOWED_COLUMNS pattern)
    rather than string interpolation. Failure here is logged and swallowed,
    not raised: the voice-query feature is optional, and a role-provisioning
    problem (e.g. the connected user lacks CREATEROLE) shouldn't block the
    rest of the app from starting.
    """
    password = get_or_create_readonly_password()
    role = sql.Identifier(READONLY_ROLE_NAME)
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READONLY_ROLE_NAME,))
        role_exists = cur.fetchone() is not None
        cur.execute(
            sql.SQL("{verb} ROLE {role} WITH LOGIN PASSWORD %s").format(
                verb=sql.SQL("ALTER" if role_exists else "CREATE"), role=role
            ),
            (password,),
        )
        # ai: claude-opus-5-5 | 2026-11-06 -- the database migrate is connected to;
        # POSTGRES_DB is empty when the deployment gives whole URLs.
        cur.execute("SELECT current_database() AS name")
        cur.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(cur.fetchone()["name"]), role
            )
        )
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        cur.execute(
            sql.SQL("GRANT SELECT ON {} TO {}").format(
                sql.SQL(", ").join(sql.Identifier(t) for t in READONLY_TABLES), role
            )
        )
        conn.commit()
        logger.info("jobhunt_readonly role provisioned (voice-query feature ready).")
    except psycopg2.Error as exc:
        conn.rollback()
        logger.warning(
            "Could not provision the jobhunt_readonly role -- the voice-query "
            "feature will be unavailable until this succeeds: %s",
            exc,
        )
