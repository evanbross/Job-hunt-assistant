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

import json
from typing import Any

from backend.db import (
    PostgresJobRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user
from backend.scanning.import_repair import (
    plan_duplicate_recheck,
    post_repair_duplicate_check,
)
from backend.scoring.duplicate_matching import auto_duplicate_skip_feedback

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


# ai: claude-opus-5-5 | 2026-11-13
def _insert(repo: PostgresJobRepository, user_id: int, **fields: Any) -> int:
    return repo.insert(user_id, {"company": _COMPANY, "job_title": _TITLE, **fields})


# ai: claude-opus-5-5 | 2026-11-13
def test_a_pair_the_user_dismissed_is_not_reflagged(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        candidate_id = _insert(repo, user_id, status="pending_review")
        fixed_job_id = _insert(
            repo, user_id, status="triage", dismissed_duplicate_ids=json.dumps([candidate_id])
        )
        conn.commit()

        found = post_repair_duplicate_check(user_id, repo, repo.get_by_id(user_id, fixed_job_id))

        assert found is False
        assert repo.get_by_id(user_id, fixed_job_id)["status"] == "triage"
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-13
def test_an_auto_skip_whose_match_no_longer_holds_returns_to_triage(pg_dsn: str) -> None:
    """Coast was auto-skipped as a near-certain duplicate of Toast under the
    old company matcher; a re-check must undo the app's own skip."""
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        toast_id = _insert(repo, user_id, company="Toast", status="applied")
        coast_id = _insert(
            repo, user_id, company="Coast", status="skipped", skip_reason="duplicate",
            skip_feedback=auto_duplicate_skip_feedback(toast_id, "Possible duplicate"),
            duplicate_of_id=toast_id, duplicate_hint="Possible duplicate",
        )
        conn.commit()

        found = post_repair_duplicate_check(user_id, repo, repo.get_by_id(user_id, coast_id))

        assert found is True
        coast = repo.get_by_id(user_id, coast_id)
        assert (coast["status"], coast["skip_reason"], coast["skip_feedback"]) == ("triage", None, None)
        assert coast["duplicate_of_id"] is None
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-13
def test_a_skip_the_user_made_is_kept_even_when_the_match_is_gone(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        toast_id = _insert(repo, user_id, company="Toast", status="applied")
        coast_id = _insert(
            repo, user_id, company="Coast", status="skipped", skip_reason="duplicate",
            skip_feedback="Same role I saw last week", duplicate_of_id=toast_id,
        )
        conn.commit()

        post_repair_duplicate_check(user_id, repo, repo.get_by_id(user_id, coast_id))

        coast = repo.get_by_id(user_id, coast_id)
        assert (coast["status"], coast["skip_feedback"]) == ("skipped", "Same role I saw last week")
        assert coast["duplicate_of_id"] is None
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-13
def test_an_unchanged_link_plans_nothing(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        candidate_id = _insert(repo, user_id, status="pending_review")
        job_id = _insert(repo, user_id, status="possible_duplicate", duplicate_of_id=candidate_id)
        conn.commit()

        plan = plan_duplicate_recheck(repo.get_by_id(user_id, job_id), repo.get_all_ordered(user_id))

        assert plan is None
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-13
def test_a_link_to_one_copy_of_a_reposted_job_is_kept(pg_dsn: str) -> None:
    """Three copies of one posting all match each other; a job already linked
    to a valid copy must not be re-pointed to whichever copy scores best
    (the first dry run re-pointed 772 jobs this way)."""
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        first_id = _insert(repo, user_id, status="applied")
        _insert(repo, user_id, status="pending_review")  # a later copy
        job_id = _insert(repo, user_id, status="possible_duplicate", duplicate_of_id=first_id)
        conn.commit()

        plan = plan_duplicate_recheck(repo.get_by_id(user_id, job_id), repo.get_all_ordered(user_id))

        assert plan is None
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-13
def test_an_auto_skip_with_a_new_near_certain_match_stays_skipped(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        toast_id = _insert(repo, user_id, company="Toast", status="applied")
        coast_original_id = _insert(repo, user_id, company="Coast", status="applied")
        coast_id = _insert(
            repo, user_id, company="Coast", status="skipped", skip_reason="duplicate",
            skip_feedback=auto_duplicate_skip_feedback(toast_id, "Possible duplicate"),
            duplicate_of_id=toast_id,
        )
        conn.commit()

        post_repair_duplicate_check(user_id, repo, repo.get_by_id(user_id, coast_id))

        coast = repo.get_by_id(user_id, coast_id)
        assert (coast["status"], coast["duplicate_of_id"]) == ("skipped", coast_original_id)
        assert f"job #{coast_original_id}" in coast["skip_feedback"]
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-13
def test_a_link_to_a_since_closed_original_is_kept(pg_dsn: str) -> None:
    """The closed-req rule decides whether a NEW posting is a duplicate; a
    re-check must not use it to resurrect an old auto-skipped repost."""
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        original_id = _insert(repo, user_id, status="skipped", skip_reason="req_closed")
        repost_id = _insert(
            repo, user_id, status="skipped", skip_reason="duplicate",
            skip_feedback=auto_duplicate_skip_feedback(original_id, "Possible duplicate"),
            duplicate_of_id=original_id,
        )
        conn.commit()

        plan = plan_duplicate_recheck(repo.get_by_id(user_id, repost_id), repo.get_all_ordered(user_id))

        assert plan is None
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-14
def test_only_the_newer_of_a_pair_is_flagged(pg_dsn: str) -> None:
    """Re-checking both halves of a pair must not flag each as a duplicate of
    the other (Lance #2079/#2080): the older job stays the original."""
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        older_id = _insert(repo, user_id, status="triage")
        newer_id = _insert(repo, user_id, status="triage")
        conn.commit()

        all_jobs = repo.get_all_ordered(user_id)
        older_plan = plan_duplicate_recheck(repo.get_by_id(user_id, older_id), all_jobs)
        newer_plan = plan_duplicate_recheck(repo.get_by_id(user_id, newer_id), all_jobs)

        assert older_plan is None
        assert newer_plan is not None
        assert newer_plan.duplicate_of_id == older_id
        assert newer_plan.status == "possible_duplicate"
    finally:
        conn.close()
