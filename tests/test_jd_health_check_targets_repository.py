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

import pytest

from backend.db import (
    PostgresJdHealthCheckTargetRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user

# ai: claude-sonnet-5 | 2026-08-21 — every repo call below now takes a
# leading user_id (multi-tenancy migration); each test creates its own account.


def _make_user_id(conn) -> int:
    account = PostgresUserAccountRepository(conn).create_pending("owner@example.com", "seed-owner@example.com", "2099-01-01")
    conn.commit()
    set_current_user(conn.cursor(), account["id"])
    return account["id"]


def _target_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "platform": "greenhouse",
        "label": "Test Co (Greenhouse board)",
        "url": "https://job-boards.greenhouse.io/testco",
        "enabled": 1,
    }
    row.update(overrides)
    return row


def test_insert_get_update_delete_round_trip(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresJdHealthCheckTargetRepository(conn)
        seeded_count = len(repo.get_all(user_id))  # 4 canary rows from _seed_data

        target_id = repo.insert(user_id, _target_row())
        conn.commit()

        assert len(repo.get_all(user_id)) == seeded_count + 1
        fetched = repo.get_by_id(user_id, target_id)
        assert fetched is not None
        assert fetched["label"] == "Test Co (Greenhouse board)"
        assert fetched["enabled"] == 1

        repo.update(user_id, target_id, {"enabled": 0, "label": "Renamed"})
        conn.commit()
        updated = repo.get_by_id(user_id, target_id)
        assert updated is not None
        assert updated["enabled"] == 0
        assert updated["label"] == "Renamed"
        assert all(row["id"] != target_id for row in repo.get_enabled(user_id))

        repo.delete(user_id, target_id)
        conn.commit()
        assert repo.get_by_id(user_id, target_id) is None
        assert len(repo.get_all(user_id)) == seeded_count
    finally:
        conn.close()


def test_get_enabled_excludes_disabled_targets(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresJdHealthCheckTargetRepository(conn)
        enabled_id = repo.insert(user_id, _target_row(label="Enabled", enabled=1))
        disabled_id = repo.insert(user_id, _target_row(label="Disabled", enabled=0))
        conn.commit()

        enabled_ids = {row["id"] for row in repo.get_enabled(user_id)}
        assert enabled_id in enabled_ids
        assert disabled_id not in enabled_ids
    finally:
        conn.close()


def test_insert_rejects_unknown_column(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresJdHealthCheckTargetRepository(conn)
        with pytest.raises(ValueError):
            repo.insert(user_id, _target_row(not_a_real_column="oops"))
    finally:
        conn.close()


def test_update_rejects_unknown_column(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresJdHealthCheckTargetRepository(conn)
        target_id = repo.insert(user_id, _target_row())
        conn.commit()
        with pytest.raises(ValueError):
            repo.update(user_id, target_id, {"not_a_real_column": "oops"})
    finally:
        conn.close()
