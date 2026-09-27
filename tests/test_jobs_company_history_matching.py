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

# ai: claude-sonnet-5 | 2026-09-19
"""Company History must find jobs at the same employer even when the company
name was captured with a different corporate suffix (e.g. "Seeq" vs.
"Seeq Corporation") — see get_by_company()/get_company_counts() in
backend/db/repositories/jobs.py, which normalize through
normalize_company_name() instead of a plain LOWER(TRIM()) comparison."""

from __future__ import annotations

from backend.db import (
    PostgresJobRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user


def _make_user(conn) -> int:
    account = PostgresUserAccountRepository(conn).create_pending(
        "owner@example.com", "seed-owner@example.com", "2099-01-01"
    )
    set_current_user(conn.cursor(), account["id"])
    return account["id"]


def test_get_by_company_matches_across_corporate_suffix(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        repo.insert(user_id, {"company": "Seeq", "job_title": "Backend Engineer", "status": "applied"})
        repo.insert(user_id, {"company": "Seeq", "job_title": "Frontend Engineer", "status": "triage"})
        repo.insert(
            user_id,
            {"company": "Seeq Corporation", "job_title": "Staff Engineer", "status": "pending_review"},
        )
        conn.commit()

        rows = repo.get_by_company(user_id, "Seeq Corporation")

        assert {r["job_title"] for r in rows} == {
            "Backend Engineer",
            "Frontend Engineer",
            "Staff Engineer",
        }
    finally:
        conn.close()


def test_get_company_counts_groups_across_corporate_suffix(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        repo.insert(user_id, {"company": "Seeq", "job_title": "Backend Engineer", "status": "applied"})
        repo.insert(
            user_id,
            {"company": "Seeq Corporation", "job_title": "Staff Engineer", "status": "pending_review"},
        )
        repo.insert(user_id, {"company": "Acme Inc", "job_title": "Solo Role", "status": "triage"})
        conn.commit()

        counts = repo.get_company_counts(user_id)

        assert counts["seeq"] == 2
        assert counts["acme"] == 1
    finally:
        conn.close()
