# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-10-09
"""post_repair_duplicate_check's match branch (backend/scanning/import_repair.py)
used to write status="possible_duplicate" through a plain, unconditional
jobs_repo.update() call alongside the duplicate_hint/duplicate_of_id pointer
fields. Closing the High finding from the 2026-10-09 review: a job the user
already applied to or skipped (USER_COMMITTED_JOB_STATUSES) must keep that
real status no matter what a re-run duplicate check concludes, and the write
must be an atomic conditional update (update_if_status_not_in) so a
concurrent /apply or /skip can't be clobbered either -- the same race class
update_if_status_not_in was added to close elsewhere in this codebase."""

from __future__ import annotations

from typing import Any

from backend.db import (
    PostgresJobRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user
from backend.scanning.import_repair import post_repair_duplicate_check

_COMPANY = "Acme Corp"
_TITLE = "Senior Software Engineer"


def _make_user(conn: Any) -> int:
    account = PostgresUserAccountRepository(conn).create_pending(
        "owner@example.com", "seed-owner@example.com", "2099-01-01"
    )
    set_current_user(conn.cursor(), account["id"])
    conn.commit()
    return account["id"]


def test_match_does_not_overwrite_applied_status(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        candidate_id = repo.insert(
            user_id, {"company": _COMPANY, "job_title": _TITLE, "status": "pending_review"}
        )
        fixed_job_id = repo.insert(
            user_id, {"company": _COMPANY, "job_title": _TITLE, "status": "applied"}
        )
        conn.commit()

        # A stale snapshot (as if read before the user applied) still says
        # status="applied" here too -- either way, the real row must win.
        fixed_job = repo.get_by_id(user_id, fixed_job_id)

        found = post_repair_duplicate_check(user_id, repo, fixed_job)

        assert found is True
        updated = repo.get_by_id(user_id, fixed_job_id)
        assert updated["status"] == "applied"
        # Pointer fields are still written -- they're descriptive, not progress.
        assert updated["duplicate_of_id"] == candidate_id
        assert updated["duplicate_hint"]
    finally:
        conn.close()


def test_match_sets_possible_duplicate_when_unacted(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        candidate_id = repo.insert(
            user_id, {"company": _COMPANY, "job_title": _TITLE, "status": "pending_review"}
        )
        fixed_job_id = repo.insert(
            user_id, {"company": _COMPANY, "job_title": _TITLE, "status": "triage"}
        )
        conn.commit()

        fixed_job = repo.get_by_id(user_id, fixed_job_id)

        found = post_repair_duplicate_check(user_id, repo, fixed_job)

        assert found is True
        updated = repo.get_by_id(user_id, fixed_job_id)
        assert updated["status"] == "possible_duplicate"
        assert updated["duplicate_of_id"] == candidate_id
    finally:
        conn.close()


def test_match_does_not_overwrite_a_concurrent_apply(pg_dsn: str) -> None:
    """The caller's `fixed_job` snapshot can be stale by the time this writes
    -- simulate a concurrent /apply landing between the snapshot read and
    this call, and assert the write is rejected rather than clobbering it."""
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        candidate_id = repo.insert(
            user_id, {"company": _COMPANY, "job_title": _TITLE, "status": "pending_review"}
        )
        fixed_job_id = repo.insert(
            user_id, {"company": _COMPANY, "job_title": _TITLE, "status": "possible_duplicate"}
        )
        conn.commit()

        # Stale snapshot taken before the concurrent apply below.
        fixed_job = repo.get_by_id(user_id, fixed_job_id)

        repo.update(user_id, fixed_job_id, {"status": "applied"})
        conn.commit()

        found = post_repair_duplicate_check(user_id, repo, fixed_job)

        assert found is True
        updated = repo.get_by_id(user_id, fixed_job_id)
        assert updated["status"] == "applied"
        assert updated["duplicate_of_id"] == candidate_id
    finally:
        conn.close()
