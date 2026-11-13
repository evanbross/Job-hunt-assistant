# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""The voice-query read-only role as a container deployment configures it
(docs/cloud-deployment-readiness-plan.md 2.1): whole database URLs with no
POSTGRES_* parts, and the password from READONLY_DB_PASSWORD, which the migrate step
sets and every app container connects with. Against the real test database."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import psycopg2
import pytest

import config
from backend.db import get_connection
from backend.db.readonly_role import (
    READONLY_ROLE_NAME,
    ensure_readonly_role,
    get_readonly_database_url,
)
from tests.db_roles import runtime_database_url

_SECRET = "deployment-secret-readonly-pw"


@pytest.fixture
def deployment_settings(
    pg_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Container-style config. Afterwards the role's password goes back to what the
    rest of the suite connects with (the role is shared by the whole test database)."""
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    for part in ("POSTGRES_HOST", "POSTGRES_DB"):
        monkeypatch.setattr(config, part, "")
    monkeypatch.setattr(config, "READONLY_DB_PASSWORD", _SECRET)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    yield tmp_path
    monkeypatch.undo()
    _provision(pg_dsn)


def _provision(owner_dsn: str) -> None:
    conn = get_connection(owner_dsn)
    try:
        ensure_readonly_role(conn)
    finally:
        conn.close()


def test_migrate_and_the_app_agree_on_the_role_with_only_whole_urls(
    pg_dsn: str, deployment_settings: Path
) -> None:
    _provision(pg_dsn)  # what the migrate container does

    with psycopg2.connect(get_readonly_database_url()) as conn:  # what an app container does
        cur = conn.cursor()
        cur.execute("SELECT current_user, count(*) FROM jobs")
        user, _count = cur.fetchone()

    assert user == READONLY_ROLE_NAME


def test_the_password_comes_from_the_secret_and_nothing_is_written_to_disk(
    deployment_settings: Path,
) -> None:
    dsn = get_readonly_database_url()

    assert f"password={_SECRET}" in dsn
    assert list(deployment_settings.iterdir()) == []  # a read-only container disk
