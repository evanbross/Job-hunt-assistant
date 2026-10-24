# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
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


_TEST_USER_ID = 1


class FakeJobsRepo:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows: dict[int, dict[str, Any]] = {row["id"]: dict(row) for row in rows}

    def get_by_id(self, user_id: int, job_id: int) -> dict[str, Any] | None:
        assert user_id == _TEST_USER_ID
        row = self.rows.get(job_id)
        return dict(row) if row else None

    def update(self, user_id: int, job_id: int, fields: dict[str, Any]) -> None:
        assert user_id == _TEST_USER_ID
        self.rows[job_id].update(fields)


def _make_client(jobs_repo: FakeJobsRepo, monkeypatch) -> Any:
    repos = {"conn": FakeConnection(), "jobs": jobs_repo}
    monkeypatch.setattr("routes.jobs.get_repos", make_fake_get_repos(repos))
    monkeypatch.setattr("routes.jobs.current_user_id", lambda: _TEST_USER_ID)
    app = Flask(__name__)
    app.register_blueprint(jobs_bp, url_prefix="/api")
    return app.test_client()


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
