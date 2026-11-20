# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================
# ai: claude-opus-5-5 | 2026-11-13
"""refresh_job_description_from_url against the real schema: the fetch and the
AI scorer are faked, every read and write goes through Postgres."""

from __future__ import annotations

import json
from typing import Any

import pytest
from werkzeug.security import generate_password_hash

import config
from backend.scanning.career_site_scanner import CareerSiteJob
from backend.scoring import jd_enrichment
from backend.scoring.fit_scorer import ScoreResult, ScoringInputs
from backend.scoring.jd_enrichment import (
    JobHasNoUrlError,
    JobNotFoundError,
    refresh_job_description_from_url,
    rescore_description,
    status_after_rescore,
)
from backend.utils.utils import get_repos
from tests.db_roles import runtime_database_url

_URL = "https://www.workatastartup.com/jobs/121134"
_JD = "Site Reliability Engineer. Remote (US). " + "Keep ClickHouse fast and reliable. " * 20


@pytest.fixture(autouse=True)
def _test_db(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))


@pytest.fixture
def user_id() -> int:
    with get_repos() as repos:
        repos["user_accounts"].create_pending("owner@example.com", "seed-owner", "2099-01-01")
        account = repos["user_accounts"].complete_registration(
            "seed-owner", generate_password_hash("correct horse battery staple")
        )
        repos["conn"].commit()
    return account["id"]


def _insert_job(user_id: int, **fields: Any) -> int:
    row = {"company": "PostHog", "job_title": "Site Reliability Engineer", "url": _URL,
           "status": "triage", "raw_jd_snippet": "Digest blurb", **fields}
    with get_repos(user_id) as repos:
        job_id = repos["jobs"].insert(user_id, row)
        repos["conn"].commit()
    return job_id


def _job(user_id: int, job_id: int) -> dict[str, Any]:
    with get_repos(user_id) as repos:
        job = repos["jobs"].get_by_id(user_id, job_id)
    assert job is not None
    return job


def _fake_fetch(monkeypatch: pytest.MonkeyPatch, job_text: str = _JD) -> None:
    def scan_single_job_url(self: object, site_name: str, job_url: str) -> CareerSiteJob:
        return CareerSiteJob(site_name=site_name, site_url=job_url, job_title="SRE", job_url=job_url,
                             job_text=job_text, posted_at=None, confidence_level="high",
                             confidence_score=100)

    monkeypatch.setattr(jd_enrichment.CareerSiteScanner, "scan_single_job_url", scan_single_job_url)


def _fake_score(monkeypatch: pytest.MonkeyPatch, score: int, during: Any = None) -> list[str]:
    seen: list[str] = []

    def fake(self: object, **kwargs: Any) -> ScoreResult:
        seen.append(kwargs["email_body"])
        if during is not None:
            during()
        return ScoreResult(score=score, reasoning="fits", salary_min=150_000, salary_max=200_000)

    monkeypatch.setattr(jd_enrichment.FitScorer, "score", fake)
    return seen


def test_saves_the_fetched_jd_rescores_and_moves_a_good_fit_to_the_queue(
    user_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _insert_job(
        user_id, fit_score=20, certainty_level="high", certainty_score=90,
        import_warnings=json.dumps(["jd_body_is_tracking_link", "scoring_failed", "homepage_url"]),
    )
    _fake_fetch(monkeypatch)
    scored_text = _fake_score(monkeypatch, 82)

    updated = refresh_job_description_from_url(user_id, job_id)

    assert updated is not None
    assert scored_text == [_JD]
    stored = _job(user_id, job_id)
    assert stored["job_description"] == _JD
    assert stored["raw_jd_snippet"] == _JD
    assert (stored["fit_score"], stored["salary_min"], stored["status"]) == (82, 150_000, "pending_review")
    assert stored["certainty_level"] is None
    assert json.loads(stored["import_warnings"]) == ["homepage_url"]


def test_replaces_a_stale_stored_job_description(user_id: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """The route used to write only raw_jd_snippet, so an older job_description
    (which every reader prefers) kept winning over the fresh fetch."""
    job_id = _insert_job(user_id, job_description="Old, stale description text.")
    _fake_fetch(monkeypatch)
    _fake_score(monkeypatch, 40)

    refresh_job_description_from_url(user_id, job_id)

    stored = _job(user_id, job_id)
    assert stored["job_description"] == _JD
    assert stored["status"] == "triage"


@pytest.mark.parametrize("status", ["possible_duplicate", "skipped", "applied"])
def test_keeps_a_status_a_rescore_must_not_override(
    user_id: int, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    job_id = _insert_job(user_id, status=status)
    _fake_fetch(monkeypatch)
    _fake_score(monkeypatch, 95)

    refresh_job_description_from_url(user_id, job_id)

    stored = _job(user_id, job_id)
    assert (stored["status"], stored["fit_score"]) == (status, 95)


def test_an_apply_landing_during_scoring_is_not_overwritten(
    user_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _insert_job(user_id)

    def user_applies() -> None:
        with get_repos(user_id) as repos:
            repos["jobs"].update(user_id, job_id, {"status": "applied"})
            repos["conn"].commit()

    _fake_fetch(monkeypatch)
    _fake_score(monkeypatch, 95, during=user_applies)

    refresh_job_description_from_url(user_id, job_id)

    assert _job(user_id, job_id)["status"] == "applied"


def test_too_little_fetched_text_writes_nothing(user_id: int, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = _insert_job(user_id, fit_score=20)
    _fake_fetch(monkeypatch, job_text="Sign in to continue")
    scored_text = _fake_score(monkeypatch, 95)

    assert refresh_job_description_from_url(user_id, job_id) is None

    stored = _job(user_id, job_id)
    assert scored_text == []
    assert (stored["job_description"], stored["fit_score"], stored["status"]) == (None, 20, "triage")


def test_a_fetch_error_writes_nothing(user_id: int, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = _insert_job(user_id)

    def failing_fetch(self: object, site_name: str, job_url: str) -> CareerSiteJob:
        raise ValueError("No job text found on page")

    monkeypatch.setattr(jd_enrichment.CareerSiteScanner, "scan_single_job_url", failing_fetch)

    assert refresh_job_description_from_url(user_id, job_id) is None
    assert _job(user_id, job_id)["job_description"] is None


def test_missing_job_and_missing_url_raise(user_id: int) -> None:
    job_id = _insert_job(user_id, url="")

    with pytest.raises(JobHasNoUrlError):
        refresh_job_description_from_url(user_id, job_id)
    with pytest.raises(JobNotFoundError):
        refresh_job_description_from_url(user_id, job_id + 1000)


@pytest.mark.parametrize(
    ("current", "score", "expected"),
    [("triage", 70, "pending_review"), ("pending_review", 69, "triage"),
     ("possible_duplicate", 90, None), ("applied", 90, None), ("skipped", 10, None)],
)
def test_status_after_rescore(current: str, score: int, expected: str | None) -> None:
    assert status_after_rescore(current, score) == expected


# ai: claude-opus-5-5 | 2026-11-19
_INPUTS = ScoringInputs(resume_text="Backend engineer", penalty_rules=[], fit_feedback=[])


# ai: claude-opus-5-5 | 2026-11-19
def test_rescore_then_apply_saves_the_description_score_and_queue_status(
    user_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _insert_job(user_id, fit_score=20)
    _fake_score(monkeypatch, 82)

    rescore = rescore_description(user_id, _job(user_id, job_id), _JD, _INPUTS)
    with get_repos(user_id) as repos:
        rescore.apply(user_id, repos["jobs"])
        repos["conn"].commit()

    stored = _job(user_id, job_id)
    assert (stored["job_description"], stored["fit_score"], stored["status"]) == (_JD, 82, "pending_review")


# ai: claude-opus-5-5 | 2026-11-19
def test_apply_keeps_an_apply_that_landed_during_the_score_call(
    user_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The score runs with no connection held, so the user can act on the job
    meanwhile; the rescore's queue status must not undo that."""
    job_id = _insert_job(user_id, fit_score=20)
    job = _job(user_id, job_id)

    def user_applies() -> None:
        with get_repos(user_id) as repos:
            repos["jobs"].update(user_id, job_id, {"status": "applied"})
            repos["conn"].commit()

    _fake_score(monkeypatch, 82, during=user_applies)

    rescore = rescore_description(user_id, job, _JD, _INPUTS)
    with get_repos(user_id) as repos:
        rescore.apply(user_id, repos["jobs"])
        repos["conn"].commit()

    stored = _job(user_id, job_id)
    assert (stored["job_description"], stored["fit_score"], stored["status"]) == (_JD, 82, "applied")


# ai: claude-opus-5-5 | 2026-11-19
def test_rescore_keeps_the_richer_description_when_scoring_fails(
    user_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _insert_job(user_id, fit_score=20)

    def scorer_down(self: object, **kwargs: Any) -> ScoreResult:
        raise ValueError("Scorer returned invalid JSON")

    monkeypatch.setattr(jd_enrichment.FitScorer, "score", scorer_down)

    rescore = rescore_description(user_id, _job(user_id, job_id), _JD, _INPUTS)
    with get_repos(user_id) as repos:
        rescore.apply(user_id, repos["jobs"])
        repos["conn"].commit()

    stored = _job(user_id, job_id)
    assert (stored["job_description"], stored["fit_score"], stored["status"]) == (_JD, 20, "triage")
