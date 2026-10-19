# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-10-12
"""Company aliases (e.g. "Block" / "Cash App") must make the "hide companies
I've applied to" filter, Company History, and per-company job counts all
agree the two names are the same employer -- see
backend/db/repositories/company_aliases.py and the
_canonical_company_normalized() helper it feeds in
backend/db/repositories/jobs.py."""

from __future__ import annotations

from backend.db import (
    PostgresCompanyAliasRepository,
    PostgresJobRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user
from backend.scoring.company_matching import normalize_company_name


def _make_user(conn) -> int:
    account = PostgresUserAccountRepository(conn).create_pending(
        "owner@example.com", "seed-owner@example.com", "2099-01-01"
    )
    set_current_user(conn.cursor(), account["id"])
    conn.commit()
    return account["id"]


def test_new_job_resolves_through_alias_at_insert_time(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        aliases = PostgresCompanyAliasRepository(conn)
        jobs = PostgresJobRepository(conn)

        aliases.upsert(user_id, alias_company="Block", canonical_company="Cash App")
        conn.commit()

        job_id = jobs.insert(user_id, {"company": "Block", "job_title": "Engineer", "status": "triage"})
        conn.commit()

        job = jobs.get_by_id(user_id, job_id)
        assert job["company_normalized"] == normalize_company_name("Cash App")
    finally:
        conn.close()


def test_upsert_backfills_existing_jobs_retroactively(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        aliases = PostgresCompanyAliasRepository(conn)
        jobs = PostgresJobRepository(conn)

        # Job 6072's actual scenario: the job already exists under "Block"
        # before the user ever declares the alias.
        job_id = jobs.insert(user_id, {"company": "Block", "job_title": "Engineer", "status": "triage"})
        conn.commit()
        assert jobs.get_by_id(user_id, job_id)["company_normalized"] == normalize_company_name("Block")

        aliases.upsert(user_id, alias_company="Block", canonical_company="Cash App")
        conn.commit()

        job = jobs.get_by_id(user_id, job_id)
        assert job["company_normalized"] == normalize_company_name("Cash App")
    finally:
        conn.close()


def test_get_by_company_matches_aliased_jobs_either_direction(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        aliases = PostgresCompanyAliasRepository(conn)
        jobs = PostgresJobRepository(conn)

        jobs.insert(user_id, {"company": "Cash App", "job_title": "Applied Role", "status": "applied"})
        jobs.insert(user_id, {"company": "Block", "job_title": "Triage Role", "status": "triage"})
        conn.commit()
        aliases.upsert(user_id, alias_company="Block", canonical_company="Cash App")
        conn.commit()

        by_alias = jobs.get_by_company(user_id, "Block")
        by_canonical = jobs.get_by_company(user_id, "Cash App")
        assert {r["job_title"] for r in by_alias} == {"Applied Role", "Triage Role"}
        assert {r["job_title"] for r in by_canonical} == {"Applied Role", "Triage Role"}
    finally:
        conn.close()


def test_get_applied_companies_reflects_alias(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        aliases = PostgresCompanyAliasRepository(conn)
        jobs = PostgresJobRepository(conn)

        jobs.insert(user_id, {"company": "Cash App", "job_title": "Applied Role", "status": "applied"})
        conn.commit()
        aliases.upsert(user_id, alias_company="Block", canonical_company="Cash App")
        conn.commit()

        block_job_id = jobs.insert(user_id, {"company": "Block", "job_title": "New Role", "status": "triage"})
        conn.commit()

        applied = jobs.get_applied_companies(user_id)
        assert applied == {normalize_company_name("Cash App")}
        assert jobs.get_by_id(user_id, block_job_id)["company_normalized"] in applied
    finally:
        conn.close()


def test_delete_alias_reverts_backfilled_jobs(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        aliases = PostgresCompanyAliasRepository(conn)
        jobs = PostgresJobRepository(conn)

        job_id = jobs.insert(user_id, {"company": "Block", "job_title": "Engineer", "status": "triage"})
        conn.commit()
        alias = aliases.upsert(user_id, alias_company="Block", canonical_company="Cash App")
        conn.commit()
        assert jobs.get_by_id(user_id, job_id)["company_normalized"] == normalize_company_name("Cash App")

        aliases.delete(user_id, alias["id"])
        conn.commit()

        job = jobs.get_by_id(user_id, job_id)
        assert job["company_normalized"] == normalize_company_name("Block")
        assert aliases.list(user_id) == []
    finally:
        conn.close()


def test_upsert_rejects_names_that_already_normalize_the_same(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        aliases = PostgresCompanyAliasRepository(conn)

        try:
            aliases.upsert(user_id, alias_company="Seeq", canonical_company="Seeq Corporation")
            raised = False
        except ValueError:
            raised = True
        assert raised
    finally:
        conn.close()


def test_migration_creates_company_aliases_table(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'company_aliases' ORDER BY column_name"
        )
        columns = {row["column_name"] for row in cur.fetchall()}
        assert {
            "id",
            "user_id",
            "alias_company",
            "alias_normalized",
            "canonical_company",
            "canonical_normalized",
            "created_at",
        } <= columns
    finally:
        conn.close()
