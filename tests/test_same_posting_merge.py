# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-15
"""The one-time same-posting merge (backend/db/same_posting_merge.py) against the
real schema, on a database shaped like the live one before 2026-11-15: no jobs.url
unique index, second rows for one posting, rows sharing another job's or a
sign-in page's URL, and stale company_normalized values."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.db import open_repositories
from backend.db.repositories.jobs import JOBS_URL_UNIQUE_INDEX
from backend.db.row_level_security import set_current_user
from backend.db.same_posting_merge import (
    JOB_ID_REFERENCES,
    JOB_ID_REFERENCES_MERGED_SEPARATELY,
    merge_same_posting_job_rows,
)
from backend.db.schema import migrate_schema
from backend.scoring.duplicate_matching import auto_duplicate_skip_feedback
from backend.scoring.import_warning_codes import (
    NON_POSTING_URL_WARNING,
    URL_OF_OTHER_JOB_WARNING,
)

_POSTING = "https://www.linkedin.com/jobs/view/4411111111/"
_TRACKING_COPY = "https://www.linkedin.com/comm/jobs/view/4411111111/?trackingId=abc"
_ANOTHER_COPY = "https://www.linkedin.com/jobs/view/4411111111/?refId=xyz"


def test_job_id_references_cover_every_job_id_column(pg_dsn: str) -> None:
    """A table added later with a job_id column must be handled by the merge, or
    its rows would be orphaned (no FK) or cascade-deleted with the merged row."""
    repos = open_repositories(pg_dsn)
    try:
        cur = repos["conn"].cursor()
        cur.execute(
            """
            SELECT c.table_name, c.column_name
            FROM information_schema.columns c
            JOIN information_schema.tables t
              ON t.table_schema = c.table_schema AND t.table_name = c.table_name
            WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
              AND (c.column_name = 'job_id' OR c.column_name = 'duplicate_of_id')
            """
        )
        found = {(row["table_name"], row["column_name"]) for row in cur.fetchall()}
    finally:
        repos["conn"].close()
    assert found == set(JOB_ID_REFERENCES) | JOB_ID_REFERENCES_MERGED_SEPARATELY


class _Seeder:
    def __init__(self, repos: dict[str, Any], user_id: int) -> None:
        self.repos = repos
        self.user_id = user_id
        self._n = 0

    def job(self, url: str | None, **fields: Any) -> int:
        self._n += 1
        row = {
            "dedup_key": f"merge-{self.user_id}-{self._n}",
            "company": "Acme",
            "job_title": "Staff Engineer",
            "url": url,
            "source": "LinkedIn",
            "status": "skipped",
            "date_found": f"2026-10-{self._n:02d}",
            **fields,
        }
        return self.repos["jobs"].insert(self.user_id, row)

    def stage(self, job_id: int, status: str, date: str) -> None:
        self.repos["conn"].cursor().execute(
            "INSERT INTO application_stages (user_id, job_id, status, date) VALUES (%s, %s, %s, %s)",
            (self.user_id, job_id, status, date),
        )


def _auto_skip(duplicate_of_id: int) -> dict[str, Any]:
    return {
        "status": "skipped",
        "skip_reason": "duplicate",
        "skip_feedback": auto_duplicate_skip_feedback(duplicate_of_id, "Already tracked"),
        "duplicate_of_id": duplicate_of_id,
    }


@pytest.fixture
def dirty_db(pg_dsn: str) -> Any:
    repos = open_repositories(pg_dsn)
    conn = repos["conn"]
    cur = conn.cursor()
    cur.execute(f"DROP INDEX IF EXISTS {JOBS_URL_UNIQUE_INDEX}")
    conn.commit()
    user_id = int(repos["user_accounts"].create_pending("merge-a@example.com", "seed-merge-a", "2099-01-01")["id"])
    other_user = int(repos["user_accounts"].create_pending("merge-b@example.com", "seed-merge-b", "2099-01-01")["id"])
    set_current_user(cur, user_id)
    s = _Seeder(repos, user_id)
    ids: dict[str, int] = {}

    # Posting 4411111111: the applied sheet row, and the copies digests made of it.
    ids["kept"] = s.job(_POSTING, status="applied", on_sheet=1)
    s.stage(ids["kept"], "applied", "2026-10-02")
    ids["tracking_copy"] = s.job(_TRACKING_COPY, source="LinkedIn digest", capture_label="Jobs for you", **_auto_skip(ids["kept"]))
    s.stage(ids["tracking_copy"], "req_closed", "2026-10-20")  # would outrank "applied" if moved
    cur.execute(
        "INSERT INTO job_status_history (user_id, job_id, from_status, to_status, changed_at, trigger)"
        " VALUES (%s, %s, 'pending_review', 'skipped', '2026-10-03T00:00:00+00:00', 'auto')",
        (user_id, ids["tracking_copy"]),
    )
    repos["job_sightings"].record(  # recorded on the copy before the merge ran
        user_id, ids["tracking_copy"], source="Glassdoor", channel="email", seen_at="2026-11-01T00:00:00+00:00"
    )
    ids["exact_copy"] = s.job(_POSTING, status="possible_duplicate", duplicate_of_id=ids["kept"], source="Wellfound")
    cur.execute(
        "INSERT INTO browser_capture_queue (user_id, url, site_name, status, created_at, updated_at, job_id)"
        " VALUES (%s, %s, 'LinkedIn', 'done', '2026-10-04', '2026-10-04', %s)",
        (user_id, _POSTING, ids["exact_copy"]),
    )
    ids["points_at_copy"] = s.job("https://example.com/jobs/unrelated", company="Other", duplicate_of_id=ids["exact_copy"])
    ids["other_company"] = s.job(_POSTING, company="Roebling")
    ids["worked_on"] = s.job(_ANOTHER_COPY, status="applied", on_sheet=1)

    # Stale company_normalized (the 2026-10-12 backfill): still the same company.
    ids["github_kept"] = s.job("https://www.linkedin.com/jobs/view/4422222222/", company="GitHub", status="applied", on_sheet=1)
    ids["github_copy"] = s.job("https://www.linkedin.com/comm/jobs/view/4422222222/", company="GitHub", **_auto_skip(ids["github_kept"]))
    cur.execute("UPDATE jobs SET company_normalized = 'unitedstatesremote' WHERE id = %s", (ids["github_kept"],))

    # A kept row with no stages takes the copy's.
    ids["stageless_kept"] = s.job("https://jobs.ashbyhq.com/acme?jobId=abc-123", status="applied")
    ids["staged_copy"] = s.job("https://jobs.ashbyhq.com/acme/apply?jobId=abc-123", status="skipped", skip_reason="req_closed")
    s.stage(ids["staged_copy"], "req_closed", "2026-10-21")

    # URLs that never named a posting.
    ids["login_a"] = s.job("https://wellfound.com/login", company="rustLabs")
    ids["login_b"] = s.job("https://wellfound.com/login", company="Splash")
    ids["shared_search_a"] = s.job("https://my.greenhouse.io/jobs?date_posted=past_week", company="Leap")
    ids["shared_search_b"] = s.job("https://my.greenhouse.io/jobs?date_posted=past_week", company="Corelight")
    ids["lone_careers"] = s.job("https://acme.example/careers", status="applied", on_sheet=1, company="Acme Robotics")

    # Another account's row for the same posting is never touched.
    set_current_user(cur, other_user)
    ids["other_user"] = _Seeder(repos, other_user).job(_POSTING, status="triage")
    conn.commit()
    try:
        yield repos, user_id, ids
    finally:
        conn.close()


def _job(repos: dict[str, Any], job_id: int) -> dict[str, Any] | None:
    cur = repos["conn"].cursor()
    cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def _scalar(repos: dict[str, Any], query: str, params: tuple) -> Any:
    cur = repos["conn"].cursor()
    cur.execute(query, params)
    return next(iter(cur.fetchone().values()))


def test_merge_folds_copies_into_sightings_and_leaves_user_work_alone(dirty_db: Any) -> None:
    repos, user_id, ids = dirty_db
    report = merge_same_posting_job_rows(repos["conn"])

    assert report.company_normalized_repaired == 1
    assert report.merged_rows == 4  # tracking_copy, exact_copy, github_copy, staged_copy
    assert report.other_job_urls_dropped == 1
    assert report.non_posting_urls_dropped == 4
    assert report.left_for_review == [(ids["kept"], ids["worked_on"])]

    for merged in ("tracking_copy", "exact_copy", "github_copy", "staged_copy"):
        assert _job(repos, ids[merged]) is None, merged
    for kept in ("kept", "worked_on", "other_company", "github_kept", "stageless_kept", "other_user", "points_at_copy"):
        assert _job(repos, ids[kept]) is not None, kept

    # Each merged copy is a sighting on the kept row, plus the one the copy already had.
    sightings = repos["job_sightings"].list_for_job(user_id, ids["kept"])
    assert sorted((s["source"], s["channel"]) for s in sightings) == [
        ("Glassdoor", "email"),
        ("LinkedIn digest", "merged_duplicate"),
        ("Wellfound", "merged_duplicate"),
    ]
    assert {s["url"] for s in sightings if s["channel"] == "merged_duplicate"} == {_TRACKING_COPY, _POSTING}

    # The kept row's own stages decide its status: the copy's req_closed is gone, not moved or orphaned.
    stages = repos["conn"].cursor()
    stages.execute("SELECT job_id, status FROM application_stages WHERE job_id = ANY(%s) ORDER BY job_id",
                   ([ids["kept"], ids["tracking_copy"], ids["stageless_kept"], ids["staged_copy"]],))
    assert [(r["job_id"], r["status"]) for r in stages.fetchall()] == [
        (ids["kept"], "applied"),
        (ids["stageless_kept"], "req_closed"),
    ]
    assert _scalar(repos, "SELECT COUNT(*) FROM job_status_history WHERE job_id = %s", (ids["kept"],)) == 1
    assert _scalar(repos, "SELECT job_id FROM browser_capture_queue WHERE url = %s", (_POSTING,)) == ids["kept"]
    assert _job(repos, ids["points_at_copy"])["duplicate_of_id"] == ids["kept"]

    # Another company's row loses the URL it never owned, and says why.
    other = _job(repos, ids["other_company"])
    assert other["url"] is None
    assert json.loads(other["import_warnings"]) == [URL_OF_OTHER_JOB_WARNING]

    # Stale company_normalized was repaired, which is what let GitHub's copy merge.
    assert _job(repos, ids["github_kept"])["company_normalized"] == "github"

    # Sign-in walls and shared search pages are dropped; a lone careers page stays.
    for dropped in ("login_a", "login_b", "shared_search_a", "shared_search_b"):
        row = _job(repos, ids[dropped])
        assert row["url"] is None, dropped
        assert json.loads(row["import_warnings"]) == [NON_POSTING_URL_WARNING]
    assert _job(repos, ids["lone_careers"])["url"] == "https://acme.example/careers"

    assert _job(repos, ids["other_user"])["url"] == _POSTING

    # Nothing left to do on a second run.
    again = merge_same_posting_job_rows(repos["conn"])
    assert (again.merged_rows, again.other_job_urls_dropped, again.non_posting_urls_dropped) == (0, 0, 0)


def test_migrate_merges_then_creates_the_url_index(dirty_db: Any) -> None:
    repos, _, _ = dirty_db
    migrate_schema(repos["conn"])
    assert _scalar(
        repos,
        "SELECT COUNT(*) FROM pg_indexes WHERE schemaname = 'public' AND indexname = %s",
        (JOBS_URL_UNIQUE_INDEX,),
    ) == 1
