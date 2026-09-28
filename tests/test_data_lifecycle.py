# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# LICENSE: Personal Use Only
# This source code is made available for personal, non-commercial use only.
# You may read, study, and run this code for personal purposes.
# You may NOT:
#   - Use this code, in whole or in part, in any commercial product or service
#   - Sell, sublicense, or otherwise monetize this code or derivatives of it
#   - Redistribute this code without explicit written permission from ZombieTech LLC
#
# No warranty is provided, express or implied. Use at your own risk.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""backend/security/data_lifecycle.py: the deletion-grace-period sweep and
log-table retention pruning. Both are cross-tenant admin operations (like a
migration), so these tests go through raw connections rather than get_repos(),
matching how the module itself works."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg2.extras
import pytest

import config
from backend.db import get_connection
from backend.db.repositories.user_accounts import PostgresUserAccountRepository
from backend.db.row_level_security import set_current_user
from backend.security.data_lifecycle import (
    prune_retention_tables,
    run_data_lifecycle_sweep,
    sweep_scheduled_deletions,
)

# ai: claude-sonnet-5 | 2026-09-28


@pytest.fixture(autouse=True)
def _test_db(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", pg_dsn)


def _raw_connection(dsn: str):
    return psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)


def _new_user(conn, email: str = "owner@example.com") -> int:
    account = PostgresUserAccountRepository(conn).create_pending(email, f"seed-{email}", "2099-01-01")
    conn.commit()
    return account["id"]


def _set_deletion_requested_at(dsn: str, user_id: int, when: datetime) -> None:
    conn = _raw_connection(dsn)
    conn.autocommit = True
    try:
        conn.cursor().execute(
            "UPDATE user_accounts SET deletion_requested_at = %s WHERE id = %s", (when, user_id)
        )
    finally:
        conn.close()


# ─── account deletion sweep ──────────────────────────────────────────────────────


def test_an_account_past_its_grace_period_is_deleted(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        _set_deletion_requested_at(
            pg_dsn, user_id, datetime.now(UTC) - timedelta(days=config.ACCOUNT_DELETION_GRACE_DAYS + 1)
        )
        deleted = sweep_scheduled_deletions(conn)
        conn.commit()
        assert deleted == 1
        assert PostgresUserAccountRepository(conn).get(user_id) is None
    finally:
        conn.close()


def test_an_account_still_within_its_grace_period_is_untouched(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        _set_deletion_requested_at(pg_dsn, user_id, datetime.now(UTC) - timedelta(days=1))
        assert sweep_scheduled_deletions(conn) == 0
        assert PostgresUserAccountRepository(conn).get(user_id) is not None
    finally:
        conn.close()


def test_an_account_never_scheduled_for_deletion_is_untouched(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        assert sweep_scheduled_deletions(conn) == 0
        assert PostgresUserAccountRepository(conn).get(user_id) is not None
    finally:
        conn.close()


def test_deleting_the_account_cascades_to_its_data(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        set_current_user(conn.cursor(), user_id)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO jobs (user_id, dedup_key, company, job_title, url, source, status, date_found)"
            " VALUES (%s, 'k', 'Acme', 'Eng', 'https://example.com/j', 'email', 'pending_review', '2026-01-01')",
            (user_id,),
        )
        conn.commit()
        _set_deletion_requested_at(
            pg_dsn, user_id, datetime.now(UTC) - timedelta(days=config.ACCOUNT_DELETION_GRACE_DAYS + 1)
        )

        sweep_scheduled_deletions(conn)
        conn.commit()

        set_current_user(cur, user_id)  # moot once the account is gone, but harmless
        cur.execute("SELECT COUNT(*) AS n FROM jobs WHERE dedup_key = 'k'")
        assert cur.fetchone()["n"] == 0
    finally:
        conn.close()


# ─── retention pruning ────────────────────────────────────────────────────────────


def test_prune_removes_only_rows_older_than_the_configured_window(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "IMPORT_REPAIR_HISTORY_RETENTION_DAYS", 30)
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        set_current_user(conn.cursor(), user_id)
        cur = conn.cursor()
        old = (datetime.now(UTC) - timedelta(days=31)).isoformat(timespec="seconds")
        recent = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
        for run_at, code in ((old, "old"), (recent, "recent")):
            cur.execute(
                "INSERT INTO import_repair_history (run_at, source, warning_code, count, total, percent, user_id)"
                " VALUES (%s, 's', %s, 1, 1, 100.0, %s)",
                (run_at, code, user_id),
            )
        conn.commit()

        removed = prune_retention_tables(conn)
        conn.commit()

        assert removed["import_repair_history"] == 1
        cur.execute("SELECT warning_code FROM import_repair_history")
        assert [r["warning_code"] for r in cur.fetchall()] == ["recent"]
    finally:
        conn.close()


def test_a_timestamptz_retention_column_is_pruned_correctly(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """audit_log.created_at is a real TIMESTAMPTZ, unlike the other three (TEXT) --
    proves the cutoff is bound with the right type for both."""
    monkeypatch.setattr(config, "AUDIT_LOG_RETENTION_DAYS", 30)
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        set_current_user(conn.cursor(), user_id)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO audit_log (user_id, event_type, created_at) VALUES (%s, 'login_success', %s)",
            (user_id, datetime.now(UTC) - timedelta(days=31)),
        )
        cur.execute(
            "INSERT INTO audit_log (user_id, event_type, created_at) VALUES (%s, 'login_success', %s)",
            (user_id, datetime.now(UTC) - timedelta(days=1)),
        )
        conn.commit()

        removed = prune_retention_tables(conn)
        conn.commit()

        assert removed["audit_log"] == 1
        cur.execute("SELECT COUNT(*) AS n FROM audit_log")
        assert cur.fetchone()["n"] == 1
    finally:
        conn.close()


def test_a_non_positive_retention_disables_pruning_for_that_table(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "AUDIT_LOG_RETENTION_DAYS", 0)
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        set_current_user(conn.cursor(), user_id)
        conn.cursor().execute(
            "INSERT INTO audit_log (user_id, event_type, created_at) VALUES (%s, 'login_success', %s)",
            (user_id, datetime.now(UTC) - timedelta(days=3650)),
        )
        conn.commit()

        removed = prune_retention_tables(conn)

        assert removed["audit_log"] == 0
    finally:
        conn.close()


# ─── the full sweep, end to end ──────────────────────────────────────────────────


def test_run_data_lifecycle_sweep_leaves_rls_armed_afterward(pg_dsn: str) -> None:
    """The sweep disables RLS to do cross-tenant work; it must re-arm it before
    returning, or a normal per-user connection opened right after would leak."""
    from backend.utils.utils import get_repos

    conn = get_connection(pg_dsn)
    try:
        user_a = _new_user(conn)
        set_current_user(conn.cursor(), user_a)
        conn.cursor().execute(
            "INSERT INTO jobs (user_id, dedup_key, company, job_title, url, source, status, date_found)"
            " VALUES (%s, 'k2', 'Acme', 'Eng', 'https://example.com/j2', 'email', 'pending_review', '2026-01-01')",
            (user_a,),
        )
        conn.commit()
    finally:
        conn.close()

    run_data_lifecycle_sweep(pg_dsn)

    with get_repos() as repos:  # no identity in this plain test function: must see nothing
        cur = repos["conn"].cursor()
        cur.execute("SELECT COUNT(*) AS n FROM jobs")
        assert cur.fetchone()["n"] == 0  # RLS still armed: sees no rows, not an error
    with get_repos(user_a) as repos:
        cur = repos["conn"].cursor()
        cur.execute("SELECT COUNT(*) AS n FROM jobs WHERE user_id = %s", (user_a,))
        assert cur.fetchone()["n"] == 1
