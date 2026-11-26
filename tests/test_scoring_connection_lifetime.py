# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================
"""No pooled DB connection may be held across a slow AI scoring step.

Until 2026-11-19 run_import_task held one connection for its whole batch, every
parser and fit-scorer call included, and the triage Approve and career-result
re-evaluate routes scored inside theirs (PYTHON_STANDARDS.md, connection
lifetime). These run against real Postgres and record how many get_repos()
connections are open whenever a slow step runs; the AI work itself is stubbed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from flask import Flask

import config
import routes.career_sites as career_sites_routes
import routes.jobs as jobs_routes
from backend import tasks
from backend.db import open_repositories
from backend.db.row_level_security import set_current_user
from backend.scanning.manual_job_ingest_service import (
    ManualJobIngestService,
    PreparedJob,
)
from backend.scoring.fit_scorer import FitScorer, ScoreResult
from backend.scoring.jd_enrichment import DescriptionRescore
from routes.career_sites import career_sites_bp
from routes.jobs import jobs_bp
from tests.db_roles import runtime_database_url
from tests.login_session import sign_in

_TRACKED_URL = "https://job-boards.greenhouse.io/acme/jobs/7777777"
_NEW_URL = "https://job-boards.greenhouse.io/acme/jobs/8888888"
_STORED_JD = "Stored description. " * 20
_RICHER_JD = "A much fuller captured description of the role. " * 40


class _NoFetch:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def scan_single_job_url(self, site_name: str, url: str) -> dict[str, str]:
        return {"job_text": ""}


# ai: claude-opus-5-5 | 2026-11-19
@pytest.fixture
def open_connections(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """A one-element list holding how many get_repos() blocks are open in the
    modules under test."""
    depth = [0]
    real_get_repos = tasks.get_repos

    @contextmanager
    def counting_get_repos(*args: Any, **kwargs: Any) -> Iterator[Any]:
        with real_get_repos(*args, **kwargs) as repos:
            depth[0] += 1
            try:
                yield repos
            finally:
                depth[0] -= 1

    for module in (tasks, jobs_routes, career_sites_routes):
        monkeypatch.setattr(module, "get_repos", counting_get_repos)
    return depth


# ai: claude-opus-5-5 | 2026-11-19
@pytest.fixture
def tracked(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    repos = open_repositories(pg_dsn)
    try:
        user_id = int(
            repos["user_accounts"].create_pending("conn-lifetime@example.com", "seed", "2099-01-01")["id"]
        )
        set_current_user(repos["conn"].cursor(), user_id)
        job_id = repos["jobs"].insert(
            user_id,
            {
                "dedup_key": "tracked", "company": "Acme", "job_title": "Staff Engineer",
                "url": _TRACKED_URL, "source": "Greenhouse", "status": "pending_review",
                "date_found": "2026-11-01", "job_description": _STORED_JD,
            },
        )
        repos["conn"].commit()
    finally:
        repos["conn"].close()
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    monkeypatch.setattr(tasks, "CareerSiteScanner", _NoFetch)
    return user_id, int(job_id)


def _jobs(pg_dsn: str, user_id: int) -> list[dict[str, Any]]:
    repos = open_repositories(pg_dsn)
    try:
        set_current_user(repos["conn"].cursor(), user_id)
        return repos["jobs"].get_all_ordered(user_id)
    finally:
        repos["conn"].close()


def _run(user_id: int, url: str, listing_text: str) -> dict[str, Any]:
    capture = [
        {"url": url, "platform": "Greenhouse", "company": "Acme",
         "job_title": "Staff Engineer", "listing_text": listing_text}
    ]
    return tasks.run_import_task.apply(
        args=(user_id, capture, None, {"import_trigger": "listener"})
    ).get()


def _stub_prepare(monkeypatch: pytest.MonkeyPatch, seen: list[int], depth: list[int], url: str) -> None:
    def prepare(self: ManualJobIngestService, listing_text: str, **_kwargs: Any) -> PreparedJob:
        seen.append(depth[0])
        return PreparedJob(
            fields={
                "dedup_key": "browser_ext:new", "company": "Acme", "job_title": "Staff Engineer",
                "url": url, "source": "Browser Extension - Greenhouse", "raw_jd_snippet": listing_text,
                "fit_score": 80, "fit_reasoning": "Good fit.", "date_found": "2026-11-19",
            },
            fit_score=80,
            parsed_job_count=1,
        )

    monkeypatch.setattr(ManualJobIngestService, "prepare", prepare)


def _stub_rescore(monkeypatch: pytest.MonkeyPatch, seen: list[int], depth: list[int]) -> None:
    def rescore(user_id: int, job: dict[str, Any], description: str, inputs: Any) -> DescriptionRescore:
        seen.append(depth[0])
        return DescriptionRescore(
            job_id=int(job["id"]),
            fields={"job_description": description, "fit_score": 91},
            new_status=None,
        )

    monkeypatch.setattr(tasks, "_rescore_description", rescore)


def test_a_new_job_is_prepared_with_no_connection_open(
    tracked: tuple[int, int], pg_dsn: str, open_connections: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id, _ = tracked
    seen: list[int] = []
    # A different company, so the new job is not a possible duplicate of the tracked one.
    _stub_prepare(monkeypatch, seen, open_connections, "https://boards.example/other/1")

    result = _run(user_id, "https://boards.example/other/1", "Listing text")

    assert seen == [0]
    assert result["queued"] == 1
    assert "https://boards.example/other/1" in {j["url"] for j in _jobs(pg_dsn, user_id)}


def test_a_richer_capture_of_a_tracked_job_is_rescored_with_no_connection_open(
    tracked: tuple[int, int], pg_dsn: str, open_connections: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id, job_id = tracked
    seen: list[int] = []
    _stub_rescore(monkeypatch, seen, open_connections)

    result = _run(user_id, _TRACKED_URL, _RICHER_JD)

    assert seen == [0]
    assert result["enriched"] == 1
    [job] = [j for j in _jobs(pg_dsn, user_id) if j["id"] == job_id]
    assert (job["job_description"], job["fit_score"]) == (_RICHER_JD.strip(), 91)


def test_a_richer_possible_duplicate_enriches_the_original_and_is_never_saved(
    tracked: tuple[int, int], pg_dsn: str, open_connections: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Used to insert the duplicate, enrich the original (an AI call) and delete
    the duplicate, all on one held connection; now the uncommitted insert is
    rolled back before the original is rescored."""
    user_id, job_id = tracked
    prepare_seen: list[int] = []
    rescore_seen: list[int] = []
    _stub_prepare(monkeypatch, prepare_seen, open_connections, _NEW_URL)
    _stub_rescore(monkeypatch, rescore_seen, open_connections)

    result = _run(user_id, _NEW_URL, _RICHER_JD)

    assert (prepare_seen, rescore_seen) == ([0], [0])
    assert (result["enriched"], result["queued"]) == (1, 0)
    jobs = _jobs(pg_dsn, user_id)
    assert [j["id"] for j in jobs] == [job_id]
    assert jobs[0]["job_description"] == _RICHER_JD.strip()


# ai: claude-opus-5-5 | 2026-11-19
def _fake_score(monkeypatch: pytest.MonkeyPatch, seen: list[int], depth: list[int]) -> None:
    def score(self: FitScorer, **_kwargs: Any) -> ScoreResult:
        seen.append(depth[0])
        return ScoreResult(
            score=77, reasoning="Good fit.", salary_min=None, salary_max=None, industry_summary=None
        )

    monkeypatch.setattr(FitScorer, "score", score)


def _client(user_id: int) -> Any:
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(jobs_bp, url_prefix="/api")
    app.register_blueprint(career_sites_bp, url_prefix="/api")
    client = app.test_client()
    with client.session_transaction() as flask_session:
        sign_in(flask_session, user_id)
    return client


def test_approving_an_unscored_job_scores_it_with_no_connection_open(
    tracked: tuple[int, int], pg_dsn: str, open_connections: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id, _ = tracked
    repos = open_repositories(pg_dsn)
    try:
        set_current_user(repos["conn"].cursor(), user_id)
        job_id = repos["jobs"].insert(
            user_id,
            {"dedup_key": "unscored", "company": "Acme", "job_title": "Backend Engineer",
             "url": "https://boards.example/acme/2", "source": "Greenhouse", "status": "triage",
             "raw_jd_snippet": None},
        )
        repos["conn"].commit()
    finally:
        repos["conn"].close()
    seen: list[int] = []
    _fake_score(monkeypatch, seen, open_connections)

    response = _client(user_id).post(f"/api/jobs/{job_id}/approve", json={"subject_pattern": ""})

    assert response.status_code == 200
    assert seen == [0]
    [job] = [j for j in _jobs(pg_dsn, user_id) if j["id"] == job_id]
    assert (job["status"], job["fit_score"]) == ("pending_review", 77)


def test_re_evaluating_career_results_scores_with_no_connection_open(
    tracked: tuple[int, int], pg_dsn: str, open_connections: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id, _ = tracked
    repos = open_repositories(pg_dsn)
    try:
        set_current_user(repos["conn"].cursor(), user_id)
        result_id = repos["career_scan_results"].insert(
            user_id,
            {"site_name": "Globex", "site_url": "https://globex.example/careers",
             "job_title": "Platform Engineer", "job_url": "https://globex.example/careers/9",
             "job_text": "Build the platform. " * 20, "confidence_level": "high",
             "confidence_score": 90, "scanned_at": "2026-11-19T12:00:00"},
        )
        repos["conn"].commit()
    finally:
        repos["conn"].close()
    seen: list[int] = []
    _fake_score(monkeypatch, seen, open_connections)

    response = _client(user_id).post("/api/career-scan/results/re-evaluate")

    assert response.status_code == 200
    assert response.get_json()["rescored"] == 1
    assert seen == [0]
    repos = open_repositories(pg_dsn)
    try:
        set_current_user(repos["conn"].cursor(), user_id)
        [row] = [r for r in repos["career_scan_results"].get_all(user_id) if r["id"] == result_id]
    finally:
        repos["conn"].close()
    assert (row["match_score"], row["match_reasoning"]) == (77, "Good fit.")
