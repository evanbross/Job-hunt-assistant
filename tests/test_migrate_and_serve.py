# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Migrations as a deploy step (backend/db/migrate.py) and what a serving process checks
before it serves (backend.db.assert_ready_to_serve, web/serving.py, wsgi.py), against the
real test database. docs/cloud-deployment-readiness-plan.md items 1.6 and 1.2."""

from __future__ import annotations

import importlib
import sys

import psycopg2
import pytest
from flask import Flask

import config
from backend.db import (
    AppRoleConfigError,
    SchemaOutOfDateError,
    assert_ready_to_serve,
    migrate_database,
)
from backend.db import migrate as migrate_command
from backend.db.schema_version import migration_source_hash
from tests.db_roles import runtime_database_url
from web import create_app
from web.serving import prepare_for_serving


def _set_recorded_hash(owner_dsn: str, value: str) -> None:
    conn = psycopg2.connect(owner_dsn)
    try:
        conn.cursor().execute("UPDATE schema_state SET migration_hash = %s", (value,))
        conn.commit()
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-06
def _recorded_hash(owner_dsn: str) -> str:
    conn = psycopg2.connect(owner_dsn)
    try:
        cur = conn.cursor()
        cur.execute("SELECT migration_hash FROM schema_state")
        return str(cur.fetchone()[0])
    finally:
        conn.close()


# ─── the readiness check ────────────────────────────────────────────────────────


def test_a_freshly_migrated_database_is_ready_for_the_runtime_role(pg_dsn: str) -> None:
    assert_ready_to_serve(runtime_database_url(pg_dsn))  # does not raise


def test_the_migration_record_matches_this_code(pg_dsn: str) -> None:
    assert _recorded_hash(pg_dsn) == migration_source_hash()


def test_a_database_migrated_by_other_code_is_not_ready(pg_dsn: str) -> None:
    _set_recorded_hash(pg_dsn, "migrated-by-an-older-release")

    with pytest.raises(SchemaOutOfDateError, match="different version"):
        assert_ready_to_serve(runtime_database_url(pg_dsn))


def test_a_database_never_migrated_with_a_record_is_not_ready(pg_dsn: str) -> None:
    conn = psycopg2.connect(pg_dsn)
    try:
        conn.cursor().execute("DROP TABLE schema_state")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(SchemaOutOfDateError, match="never been migrated"):
        assert_ready_to_serve(runtime_database_url(pg_dsn))


def test_serving_as_the_owner_is_refused(pg_dsn: str) -> None:
    with pytest.raises(AppRoleConfigError, match="owns the schema"):
        assert_ready_to_serve(pg_dsn)


def test_no_app_database_url_is_refused() -> None:
    with pytest.raises(AppRoleConfigError, match="POSTGRES_APP_USER and POSTGRES_APP_PASSWORD"):
        assert_ready_to_serve("")


# ai: claude-opus-5-5 | 2026-11-06 -- the desktop's real failure: with no POSTGRES_APP_*
# settings, migration ran to completion as the owner and only then failed, naming
# DATABASE_URL (a setting the desktop .env doesn't use).
def test_migrating_with_no_app_login_names_the_settings_and_changes_nothing(
    pg_dsn: str,
) -> None:
    _set_recorded_hash(pg_dsn, "stale")

    with pytest.raises(AppRoleConfigError, match="POSTGRES_APP_USER and POSTGRES_APP_PASSWORD"):
        migrate_database(pg_dsn, "")

    assert _recorded_hash(pg_dsn) == "stale"


# ─── the migrate command ────────────────────────────────────────────────────────


def test_the_migrate_command_migrates_and_records(pg_dsn: str) -> None:
    _set_recorded_hash(pg_dsn, "stale")

    assert migrate_command.main() == 0

    assert_ready_to_serve(config.DATABASE_URL)


def test_the_migrate_command_fails_when_the_app_url_is_the_owner(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", pg_dsn)

    assert migrate_command.main() == 1


def test_the_migrate_command_fails_without_an_owner_url(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DATABASE_OWNER_URL", "")

    assert migrate_command.main() == 1


# ─── a serving process ──────────────────────────────────────────────────────────


def test_prepare_for_serving_accepts_a_migrated_database(pg_dsn: str) -> None:
    app = create_app()
    prepare_for_serving(app)
    assert app.secret_key  # the validated key was installed


def test_prepare_for_serving_refuses_an_unmigrated_database(pg_dsn: str) -> None:
    _set_recorded_hash(pg_dsn, "migrated-by-an-older-release")

    with pytest.raises(SystemExit) as excinfo:
        prepare_for_serving(create_app())
    assert excinfo.value.code == 1


def test_prepare_for_serving_refuses_the_owner_role(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", pg_dsn)

    with pytest.raises(SystemExit) as excinfo:
        prepare_for_serving(create_app())
    assert excinfo.value.code == 1


def test_the_wsgi_module_builds_a_serving_app(pg_dsn: str) -> None:
    """What gunicorn does: import wsgi and take `app`. Each import is a fresh module,
    as in each gunicorn worker."""
    sys.modules.pop("wsgi", None)
    try:
        wsgi = importlib.import_module("wsgi")
        assert isinstance(wsgi.app, Flask)
        response = wsgi.app.test_client().get("/api/system/health")
        assert response.status_code == 200
    finally:
        sys.modules.pop("wsgi", None)
