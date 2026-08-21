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

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.db import (
    PostgresAppAccountRepository,
    PostgresJdHealthCheckResultRepository,
    PostgresJdHealthCheckTargetRepository,
    get_connection,
)

# ai: claude-sonnet-5 | 2026-08-21 — every repo call below now takes a
# leading user_id (multi-tenancy migration); each test creates its own account.


def _make_user_id(conn) -> int:
    account = PostgresAppAccountRepository(conn).create_pending("owner@example.com", "seed-owner@example.com", "2099-01-01")
    conn.commit()
    return account["id"]


def _result_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "target_id": None,
        "platform": "greenhouse",
        "url": "https://job-boards.greenhouse.io/testco",
        "label": "Test Co (Greenhouse board)",
        "success": 1,
        "error_message": None,
        "extracted_title": "Senior Engineer",
        "extracted_company": "Test Co",
        "text_length": 1200,
        "confidence_level": "high",
        "triggered_by": "manual",
    }
    row.update(overrides)
    return row


def test_get_last_run_at_returns_none_on_empty_table(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresJdHealthCheckResultRepository(conn)
        assert repo.get_last_run_at(user_id) is None
    finally:
        conn.close()


def test_insert_and_get_recent_and_last_run_at(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresJdHealthCheckResultRepository(conn)
        repo.insert(user_id, _result_row(run_at="2026-08-01 03:00:00"))
        repo.insert(
            user_id,
            _result_row(run_at="2026-08-02 03:00:00", success=0, error_message="boom"),
        )
        conn.commit()

        assert repo.get_last_run_at(user_id) == "2026-08-02 03:00:00"

        recent = repo.get_recent(user_id, limit=10)
        assert len(recent) == 2
        assert recent[0]["run_at"] == "2026-08-02 03:00:00"  # most recent first
        assert recent[0]["success"] == 0
        assert recent[0]["error_message"] == "boom"
    finally:
        conn.close()


def test_get_by_target_scopes_to_one_target_and_sets_null_on_target_delete(
    pg_dsn: str,
) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        target_repo = PostgresJdHealthCheckTargetRepository(conn)
        result_repo = PostgresJdHealthCheckResultRepository(conn)

        target_id = target_repo.insert(
            user_id,
            {
                "platform": "ashby",
                "label": "Test Co (Ashby board)",
                "url": "https://jobs.ashbyhq.com/testco",
            },
        )
        other_target_id = target_repo.insert(
            user_id,
            {
                "platform": "ashby",
                "label": "Other Co (Ashby board)",
                "url": "https://jobs.ashbyhq.com/otherco",
            },
        )
        conn.commit()

        result_repo.insert(
            user_id, _result_row(target_id=target_id, run_at="2026-08-01 03:00:00")
        )
        result_repo.insert(
            user_id, _result_row(target_id=other_target_id, run_at="2026-08-01 03:00:00")
        )
        conn.commit()

        scoped = result_repo.get_by_target(user_id, target_id)
        assert len(scoped) == 1
        assert scoped[0]["target_id"] == target_id

        target_repo.delete(user_id, target_id)
        conn.commit()

        # ON DELETE SET NULL: the result row survives with target_id cleared,
        # not cascaded away — history should outlive a removed target.
        surviving = result_repo.get_recent(user_id, limit=10)
        assert any(row["target_id"] is None for row in surviving)
    finally:
        conn.close()


def test_insert_rejects_unknown_column(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresJdHealthCheckResultRepository(conn)
        with pytest.raises(ValueError):
            repo.insert(user_id, _result_row(not_a_real_column="oops"))
    finally:
        conn.close()
