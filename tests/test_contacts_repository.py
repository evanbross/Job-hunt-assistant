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

from backend.db import (
    PostgresAppAccountRepository,
    PostgresContactRepository,
    get_connection,
)

# ai: claude-sonnet-5 | 2026-07-25


def _contact_row(**overrides: object) -> dict[str, object]:
    row = {
        "full_name": "Jane Doe",
        "company_raw": "Acme Inc",
        "company_normalized": "acme",
        "position": "Engineer",
        "profile_url": "https://linkedin.com/in/janedoe",
        "connected_on": "2024-01-15",
        "imported_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    row.update(overrides)
    return row


def test_replace_all_round_trips_and_fully_replaces(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        account = PostgresAppAccountRepository(conn).create("owner@example.com", "hash")
        user_id = account["id"]
        repo = PostgresContactRepository(conn)

        repo.replace_all(
            user_id, [_contact_row(), _contact_row(full_name="John Smith", profile_url=None)]
        )
        conn.commit()

        all_contacts = repo.get_all(user_id)
        assert repo.count(user_id) == 2
        assert {c["full_name"] for c in all_contacts} == {"Jane Doe", "John Smith"}

        repo.replace_all(user_id, [_contact_row(full_name="Only Contact")])
        conn.commit()

        remaining = repo.get_all(user_id)
        assert repo.count(user_id) == 1
        assert remaining[0]["full_name"] == "Only Contact"
    finally:
        conn.close()


def test_replace_all_with_empty_list_clears_table(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        account = PostgresAppAccountRepository(conn).create("owner@example.com", "hash")
        user_id = account["id"]
        repo = PostgresContactRepository(conn)
        repo.replace_all(user_id, [_contact_row()])
        conn.commit()
        assert repo.count(user_id) == 1

        repo.replace_all(user_id, [])
        conn.commit()
        assert repo.count(user_id) == 0
    finally:
        conn.close()
