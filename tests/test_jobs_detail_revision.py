# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""jobs.detail_revision, kept by the jobs_detail_revision trigger.

Job 8083 (2026-11-19): "Save & Re-score" rewrote fit_reasoning, but the Job Queue
card kept showing the reasoning it had lazy-loaded before, because nothing told it
the row's detail had changed. These tests run the trigger on real Postgres, the
layer that owns the contract the frontend's useLazyJobDetail() relies on.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.db import (
    PostgresJobRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.job_columns import JobColumns
from backend.db.job_queue_query import JobQueueQuery, JobQueueView
from backend.db.row_level_security import set_current_user

# ai: claude-opus-5-5 | 2026-11-19


def _revision(jobs: PostgresJobRepository, user_id: int, job_id: int) -> int:
    row = jobs.get_by_id(user_id, job_id)
    assert row is not None
    return int(row[JobColumns.DETAIL_REVISION])


@pytest.fixture
def seeded_job(pg_dsn: str) -> Any:
    conn = get_connection(pg_dsn)
    try:
        account = PostgresUserAccountRepository(conn).create_pending(
            "owner@example.com", "seed-owner@example.com", "2099-01-01"
        )
        user_id = account["id"]
        set_current_user(conn.cursor(), user_id)
        jobs = PostgresJobRepository(conn)
        job_id = jobs.insert(
            user_id,
            {
                "dedup_key": "detail-revision-1",
                "company": "Lockheed Martin",
                "job_title": "Software Engineer",
                "url": "https://example.com/jobs/1",
                "source": "manual",
                "status": "pending_review",
                "raw_jd_snippet": "short snippet",
                "fit_score": 60,
                "fit_reasoning": "No real description to judge from.",
            },
        )
        conn.commit()
        yield conn, jobs, user_id, job_id
    finally:
        conn.close()


def test_new_job_starts_at_revision_zero(seeded_job: Any) -> None:
    _conn, jobs, user_id, job_id = seeded_job
    assert _revision(jobs, user_id, job_id) == 0


@pytest.mark.parametrize("column", sorted(JobColumns.lazy_detail_columns()))
def test_changing_any_lazy_column_bumps_the_revision(seeded_job: Any, column: str) -> None:
    conn, jobs, user_id, job_id = seeded_job

    jobs.update(user_id, job_id, {column: f"new {column} text"})
    conn.commit()

    assert _revision(jobs, user_id, job_id) == 1


def test_rescore_with_new_description_bumps_once_per_update(seeded_job: Any) -> None:
    conn, jobs, user_id, job_id = seeded_job

    jobs.update(
        user_id,
        job_id,
        {"job_description": "Full Lockheed JD", "fit_score": 25, "fit_reasoning": "Clearance required."},
    )
    conn.commit()
    jobs.update(user_id, job_id, {"fit_score": 40, "fit_reasoning": "Re-evaluated again."})
    conn.commit()

    assert _revision(jobs, user_id, job_id) == 2


def test_list_only_and_unchanged_writes_leave_the_revision_alone(seeded_job: Any) -> None:
    conn, jobs, user_id, job_id = seeded_job

    jobs.update(user_id, job_id, {"status": "triage", "fit_score": 61})
    jobs.update(user_id, job_id, {"fit_reasoning": "No real description to judge from."})
    conn.commit()

    assert _revision(jobs, user_id, job_id) == 0


def test_the_revision_cannot_be_written_directly(seeded_job: Any) -> None:
    conn, jobs, user_id, job_id = seeded_job

    jobs.update(user_id, job_id, {JobColumns.DETAIL_REVISION: 99})
    conn.commit()

    assert _revision(jobs, user_id, job_id) == 0


def test_list_views_ship_the_revision_but_not_the_lazy_text(seeded_job: Any) -> None:
    conn, jobs, user_id, job_id = seeded_job
    jobs.update(user_id, job_id, {"fit_reasoning": "Clearance required."})
    conn.commit()

    # The query behind both the Job Queue and Triage cards (GET /jobs/queue).
    page = jobs.list_queue_page(user_id, JobQueueQuery(view=JobQueueView.BOTH), 10)

    row = next(r for r in page.rows if r["id"] == job_id)
    assert row[JobColumns.DETAIL_REVISION] == 1
    assert JobColumns.FIT_REASONING not in row
