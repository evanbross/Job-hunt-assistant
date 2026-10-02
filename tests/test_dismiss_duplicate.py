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

"""Regression test for the dismiss-duplicate ping-pong bug: a job that fuzzy-matches
two *different* already-tracked jobs at the same company must reach a non-duplicate
status after being dismissed once per match, not loop between them forever."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from flask import Flask

from routes.jobs import jobs_bp


def make_fake_get_repos(repos: dict) -> Any:
    @contextmanager
    def fake() -> Iterator[dict[str, Any]]:
        try:
            yield repos
        finally:
            repos["conn"].close()

    return fake


class FakeConnection:
    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


_TEST_USER_ID = 1


class FakeMultiJobsRepo:
    """Keyed by id, unlike FakeJobsRepo in test_jobs_description_route.py, so the
    real db_match_for_job / DuplicateChecker can run against multiple known jobs."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows: dict[int, dict[str, Any]] = {row["id"]: dict(row) for row in rows}

    def get_by_id(self, user_id: int, job_id: int) -> dict[str, Any] | None:
        assert user_id == _TEST_USER_ID
        row = self.rows.get(job_id)
        return dict(row) if row else None

    def update(self, user_id: int, job_id: int, fields: dict[str, Any]) -> None:
        assert user_id == _TEST_USER_ID
        self.rows[job_id].update(fields)

    def get_all_ordered(self, user_id: int) -> list[dict[str, Any]]:
        assert user_id == _TEST_USER_ID
        return [dict(row) for row in self.rows.values()]


def test_dismiss_duplicate_resolves_when_two_jobs_fuzzy_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Job 1 fuzzy-matches both job 2 and job 3 (two Workiva "Principal Software
    Engineer" postings). Dismissing must exclude every previously-dismissed match,
    not just the latest one, so the second dismiss reaches a clean, non-duplicate
    status instead of re-matching the job excluded by the first dismiss."""
    jobs_repo = FakeMultiJobsRepo(
        [
            {
                "id": 1,
                "company": "Workiva",
                # Abbreviated form of "Principal Software Engineer" so it fuzzy-matches
                # jobs 2 and 3 — same seniority level, or the seniority gate would reject.
                "job_title": "Principal SWE",
                "url": "https://www.linkedin.com/jobs/view/1111111111/",
                "status": "possible_duplicate",
                "duplicate_of_id": 3,
                "duplicate_hint": 'Possible duplicate of "Principal Software Engineer" at Workiva.',
                "fit_score": 78,
                "dismissed_duplicate_ids": None,
            },
            {
                "id": 2,
                "company": "Workiva",
                "job_title": "Principal Software Engineer",
                "url": "https://www.linkedin.com/jobs/view/2222222222/",
                "status": "applied",
                "duplicate_of_id": None,
                "duplicate_hint": None,
                "fit_score": 80,
                "dismissed_duplicate_ids": None,
            },
            {
                "id": 3,
                "company": "Workiva",
                "job_title": "Principal Software Engineer",
                "url": "https://www.linkedin.com/jobs/view/3333333333/",
                "status": "skipped",
                "duplicate_of_id": 2,
                "duplicate_hint": 'Already tracked as "Principal Software Engineer" at Workiva.',
                "fit_score": None,
                "dismissed_duplicate_ids": None,
            },
        ]
    )
    repos = {"conn": FakeConnection(), "jobs": jobs_repo}
    monkeypatch.setattr("routes.jobs.get_repos", make_fake_get_repos(repos))
    monkeypatch.setattr("routes.jobs.current_user_id", lambda: _TEST_USER_ID)

    app = Flask(__name__)
    app.register_blueprint(jobs_bp, url_prefix="/api")
    client = app.test_client()

    first = client.post("/api/jobs/1/dismiss-duplicate")
    assert first.status_code == 200
    first_data = first.get_json()
    assert first_data["status"] == "possible_duplicate"
    assert first_data["duplicate_of_id"] == 2
    assert json.loads(first_data["dismissed_duplicate_ids"]) == [3]

    second = client.post("/api/jobs/1/dismiss-duplicate")
    assert second.status_code == 200
    second_data = second.get_json()
    assert second_data["status"] == "pending_review"
    assert second_data["duplicate_of_id"] is None
    assert second_data["duplicate_hint"] is None
    assert json.loads(second_data["dismissed_duplicate_ids"]) == [2, 3]


# ai: claude-sonnet-5 | 2026-10-01
class FakeRaceJobsRepo(FakeMultiJobsRepo):
    """Simulates a concurrent /apply landing between dismiss_duplicate's initial
    read and its pre-write re-check: the second get_by_id call (the guard) sees
    status="applied" even though the first call (read at the top) still saw the
    pre-race status."""

    def __init__(self, rows: list[dict[str, Any]], race_job_id: int) -> None:
        super().__init__(rows)
        self._race_job_id = race_job_id
        self._get_by_id_calls = 0

    def get_by_id(self, user_id: int, job_id: int) -> dict[str, Any] | None:
        if job_id == self._race_job_id:
            self._get_by_id_calls += 1
            if self._get_by_id_calls == 2 and job_id in self.rows:
                # Mutate the backing row itself (not a returned copy) so the
                # "concurrent apply" is visible both to this call's return
                # value and to the test's later assertion on jobs_repo.rows.
                self.rows[job_id]["status"] = "applied"
        return super().get_by_id(user_id, job_id)


def test_dismiss_duplicate_does_not_clobber_concurrent_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for the job 4676/5376 incident: dismiss_duplicate must not
    overwrite a status set by a concurrent /apply that lands mid-request. The
    stored `status` must stay untouched -- the route should short-circuit on its
    pre-write re-check and return the already-applied job as-is."""
    jobs_repo = FakeRaceJobsRepo(
        [
            {
                "id": 1,
                "company": "Hatch",
                "job_title": "Staff Backend Engineer",
                "url": "https://www.linkedin.com/jobs/view/4437928307/",
                "status": "possible_duplicate",
                "duplicate_of_id": 2,
                "duplicate_hint": 'Already tracked as "Staff Backend Engineer" at Hatch.',
                "fit_score": 85,
                "dismissed_duplicate_ids": None,
            },
            {
                "id": 2,
                "company": "Hatch",
                "job_title": "Staff Backend Engineer",
                "url": "https://www.linkedin.com/jobs/view/4437928307/?variant=2",
                "status": "skipped",
                "duplicate_of_id": 1,
                "duplicate_hint": None,
                "fit_score": None,
                "dismissed_duplicate_ids": None,
            },
        ],
        race_job_id=1,
    )
    repos = {"conn": FakeConnection(), "jobs": jobs_repo}
    monkeypatch.setattr("routes.jobs.get_repos", make_fake_get_repos(repos))
    monkeypatch.setattr("routes.jobs.current_user_id", lambda: _TEST_USER_ID)

    app = Flask(__name__)
    app.register_blueprint(jobs_bp, url_prefix="/api")
    client = app.test_client()

    response = client.post("/api/jobs/1/dismiss-duplicate")

    assert response.status_code == 200
    assert response.get_json()["status"] == "applied"
    assert jobs_repo.rows[1]["status"] == "applied"
