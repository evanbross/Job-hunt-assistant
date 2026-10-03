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
    init_db,
)
from backend.db.row_level_security import set_current_user
from backend.scoring.company_matching import normalize_company_name


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


# ai: gpt-5 | 2026-10-03
def test_get_outreach_company_relevance_uses_latest_stage_view_schema(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        repo.insert(user_id, {"company": "Acme", "job_title": "Active", "status": "triage"})
        repo.insert(user_id, {"company": "Seeq", "job_title": "Past", "status": "applied"})
        conn.commit()

        relevance = repo.get_outreach_company_relevance(
            user_id,
            frozenset({"pending_review", "triage", "possible_duplicate"}),
            {"rejected", "job_filled", "req_closed", "ghosted"},
        )

        assert relevance == {"acme": 10.0, "seeq": 5.0}
    finally:
        conn.close()


# ai: claude-sonnet-5 | 2026-10-01
# The one-time backfill in backend/db/schema.py's _migrate() calls
# normalize_company_name() directly (no second, SQL-regex reimplementation of
# the same logic -- an earlier version of this migration did exactly that and
# was replaced). This exercises the real migration path end-to-end: a row
# written before company_normalized existed (simulated here by inserting
# around the repository, which would otherwise derive it itself) must get
# backfilled correctly the next time init_db() runs.
def test_migration_backfills_company_normalized_for_legacy_rows(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO jobs (user_id, company, job_title, status)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (user_id, "Seeq Corporation", "Legacy Row", "triage"),
        )
        job_id = cur.fetchone()["id"]
        conn.commit()

        init_db(pg_dsn)

        cur.execute("SELECT company_normalized FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        assert row["company_normalized"] == normalize_company_name("Seeq Corporation")
    finally:
        conn.close()
