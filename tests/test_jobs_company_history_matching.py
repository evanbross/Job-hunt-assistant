# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-09-19
"""Company History must find jobs at the same employer even when the company
name was captured with a different corporate suffix (e.g. "Seeq" vs.
"Seeq Corporation") — see get_by_company()/count_by_company() in
backend/db/repositories/jobs.py, which normalize through
normalize_company_name() instead of a plain LOWER(TRIM()) comparison."""

from __future__ import annotations

from backend.db import (
    PostgresApplicationStageRepository,
    PostgresJobRepository,
    PostgresUserAccountRepository,
    get_connection,
    migrate_database,
)
from backend.db.repositories.company_aliases import PostgresCompanyAliasRepository
from backend.db.row_level_security import set_current_user
from backend.scoring.company_matching import normalize_company_name
from tests.db_roles import runtime_database_url


def _make_user(conn) -> int:
    account = PostgresUserAccountRepository(conn).create_pending(
        "owner@example.com", "seed-owner@example.com", "2099-01-01"
    )
    set_current_user(conn.cursor(), account["id"])
    conn.commit()
    return account["id"]


# ai: claude-sonnet-5 | 2026-10-05
def _new_other_user(conn) -> int:
    account = PostgresUserAccountRepository(conn).create_pending(
        "other@example.com", "seed-other@example.com", "2099-01-01"
    )
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


# ai: claude-sonnet-5 | 2026-10-16
# get_by_company()'s alias resolution used to be a separate round trip
# (_canonical_company_normalized) before this SELECT; it's now a correlated
# subquery of the same statement (CANONICAL_ALIAS_SUBQUERY, shared with
# resolve_company_alias in company_aliases.py). Nothing exercised the alias
# path through get_by_company itself before this -- the corporate-suffix
# test above only covers normalize_company_name(), not company_aliases.
def test_get_by_company_matches_through_a_user_declared_alias(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        aliases = PostgresCompanyAliasRepository(conn)
        repo.insert(user_id, {"company": "Block", "job_title": "Payments Engineer", "status": "applied"})
        conn.commit()
        aliases.upsert(user_id, alias_company="Cash App", canonical_company="Block")
        conn.commit()

        rows_by_alias = repo.get_by_company(user_id, "Cash App")
        rows_by_canonical = repo.get_by_company(user_id, "Block")

        assert {r["job_title"] for r in rows_by_alias} == {"Payments Engineer"}
        assert {r["job_title"] for r in rows_by_canonical} == {"Payments Engineer"}
    finally:
        conn.close()


def test_count_by_company_groups_across_corporate_suffix(pg_dsn: str) -> None:
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

        counts = repo.count_by_company(user_id, ["seeq", "acme", "nobody"])  # ai: claude-opus-5-5 | 2026-10-24

        assert counts == {"seeq": 2, "acme": 1}
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


# ai: claude-sonnet-5 | 2026-10-05
def test_get_outreach_company_relevance_through_the_latest_stage_view_and_tenants(
    pg_dsn: str,
) -> None:
    """Regression coverage for the 2026-10-05 review finding "new job-company
    SQL lacks a real-schema contract test" -- the test above never actually
    joins a real application_stages row through the latest_application_stage
    view (its one non-active job has no stage history at all), so a broken
    join, a wrong column, or a cross-tenant leak through the view could pass
    every existing test. This drives get_outreach_company_relevance() itself
    through four distinct cases plus a second tenant, against the real schema."""
    active_statuses = frozenset({"pending_review", "triage", "possible_duplicate"})
    terminal_stage_statuses = {"rejected", "job_filled", "req_closed", "ghosted"}

    conn = get_connection(pg_dsn)
    try:
        owner_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        stages_repo = PostgresApplicationStageRepository(conn)

        # Active via job.status alone -- no stage history needed.
        repo.insert(owner_id, {"company": "Delta", "job_title": "Via Status", "status": "triage"})

        # Active via a NON-terminal stage, even though job.status itself
        # isn't in active_statuses -- this is the join the narrower test
        # above never exercises (its one non-active job has no stages at all).
        active_via_stage_id = repo.insert(
            owner_id, {"company": "Acme", "job_title": "Via Stage", "status": "applied"}
        )
        stages_repo.insert(
            owner_id, {"job_id": active_via_stage_id, "status": "hr_screen_done", "date": "2026-10-01"}
        )

        # NOT active: job.status isn't active AND its latest stage is terminal.
        terminal_id = repo.insert(
            owner_id, {"company": "Beta", "job_title": "Terminal Stage", "status": "applied"}
        )
        stages_repo.insert(
            owner_id, {"job_id": terminal_id, "status": "rejected", "date": "2026-10-01"}
        )

        # NOT active: job.status isn't active and there is no stage history
        # at all (latest.latest_status IS NULL) -- the "missing-stage" case.
        repo.insert(owner_id, {"company": "Gamma", "job_title": "No Stage", "status": "applied"})
        conn.commit()

        relevance = repo.get_outreach_company_relevance(
            owner_id, active_statuses, terminal_stage_statuses
        )

        assert relevance == {
            "delta": 10.0,
            "acme": 10.0,
            "beta": 5.0,
            "gamma": 5.0,
        }

        # Cross-tenant: a second account with a job at a company of the SAME
        # name ("Acme") and no stage history must get its own 5.0 -- never
        # blended with or leaked from owner_id's 10.0 "Acme" row above,
        # whether through the jobs table filter or through the view join.
        other_id = _new_other_user(conn)
        set_current_user(conn.cursor(), other_id)
        conn.commit()
        other_repo = PostgresJobRepository(conn)
        other_repo.insert(other_id, {"company": "Acme", "job_title": "Other Tenant", "status": "applied"})
        conn.commit()

        other_relevance = other_repo.get_outreach_company_relevance(
            other_id, active_statuses, terminal_stage_statuses
        )

        assert other_relevance == {"acme": 5.0}
    finally:
        conn.close()


# ai: claude-sonnet-5 | 2026-10-01
# The one-time backfill in backend/db/schema.py's migrate_schema() calls
# normalize_company_name() directly (no second, SQL-regex reimplementation of
# the same logic -- an earlier version of this migration did exactly that and
# was replaced). This exercises the real migration path end-to-end: a row
# written before company_normalized existed (simulated here by inserting
# around the repository, which would otherwise derive it itself) must get
# backfilled correctly the next time migrate_database() runs.
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

        migrate_database(pg_dsn, runtime_database_url(pg_dsn))

        cur.execute("SELECT company_normalized FROM jobs WHERE id = %s", (job_id,))
        row = cur.fetchone()
        assert row["company_normalized"] == normalize_company_name("Seeq Corporation")
    finally:
        conn.close()
