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

"""Tests for GET /jobs/unreachable and POST /jobs/<id>/clear-unreachable — the
manual-review surface for jobs the closed-req scan couldn't get a response from
(see routes/applications.py's _persist_unreachable_flag)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

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


class FakeJobsRepo:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows: dict[int, dict[str, Any]] = {row["id"]: dict(row) for row in rows}

    def get_by_id(self, job_id: int) -> dict[str, Any] | None:
        row = self.rows.get(job_id)
        return dict(row) if row else None

    def update(self, job_id: int, fields: dict[str, Any]) -> None:
        self.rows[job_id].update(fields)

    def get_unreachable(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.rows.values()
            if row.get("unreachable_since") is not None
        ]


def _make_client(jobs_repo: FakeJobsRepo, monkeypatch) -> Any:
    repos = {"conn": FakeConnection(), "jobs": jobs_repo}
    monkeypatch.setattr("routes.jobs.get_repos", make_fake_get_repos(repos))
    app = Flask(__name__)
    app.register_blueprint(jobs_bp, url_prefix="/api")
    return app.test_client()


def test_get_unreachable_jobs_returns_only_flagged_jobs_across_statuses(monkeypatch) -> None:
    """The Unreachable tab must surface jobs regardless of their pipeline status —
    that's the whole point (queue/triage each only show one status slice)."""
    jobs_repo = FakeJobsRepo(
        [
            {"id": 1, "company": "Acme", "job_title": "A", "status": "triage", "unreachable_since": "2026-08-01"},
            {"id": 2, "company": "Beta", "job_title": "B", "status": "possible_duplicate", "unreachable_since": "2026-08-02"},
            {"id": 3, "company": "Gamma", "job_title": "C", "status": "pending_review", "unreachable_since": None},
        ]
    )
    client = _make_client(jobs_repo, monkeypatch)

    res = client.get("/api/jobs/unreachable")

    assert res.status_code == 200
    ids = {row["id"] for row in res.get_json()}
    assert ids == {1, 2}


def test_clear_unreachable_dismisses_flag_without_touching_status(monkeypatch) -> None:
    jobs_repo = FakeJobsRepo(
        [
            {"id": 1, "company": "Acme", "job_title": "A", "status": "triage", "unreachable_since": "2026-08-01"},
        ]
    )
    client = _make_client(jobs_repo, monkeypatch)

    res = client.post("/api/jobs/1/clear-unreachable")

    assert res.status_code == 200
    data = res.get_json()
    assert data["unreachable_since"] is None
    assert data["status"] == "triage"


def test_clear_unreachable_404s_for_missing_job(monkeypatch) -> None:
    client = _make_client(FakeJobsRepo([]), monkeypatch)

    res = client.post("/api/jobs/999/clear-unreachable")

    assert res.status_code == 404
