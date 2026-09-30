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

# ai: claude-sonnet-5 | 2026-09-29
"""routes/applications.py's tracking-row routes -- add_application_sheet_row,
update_application_sheet_row, add_application_sheet_stage. Covers the
2026-09-29 Pydantic validation cleanup."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from flask import Flask

from routes.applications import applications_bp

_TEST_USER_ID = 1


class FakeConnection:
    def commit(self) -> None:
        pass


class FakeJobsRepo:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {
            5: {"id": 5, "company": "Acme", "job_title": "Engineer"}
        }
        self._next_id = 6
        self.inserted: dict[str, Any] | None = None
        self.updated: dict[str, Any] | None = None

    def insert(self, user_id: int, fields: dict[str, Any]) -> int:
        self.inserted = fields
        row_id = self._next_id
        self._next_id += 1
        self.rows[row_id] = {"id": row_id, **fields}
        return row_id

    def get_by_id(self, user_id: int, job_id: int) -> dict[str, Any] | None:
        row = self.rows.get(job_id)
        return dict(row) if row else None

    def update(self, user_id: int, job_id: int, fields: dict[str, Any]) -> None:
        self.updated = fields
        self.rows[job_id].update(fields)


class FakeStagesRepo:
    def __init__(self) -> None:
        self.inserted: dict[str, Any] | None = None

    def insert(self, user_id: int, fields: dict[str, Any]) -> None:
        self.inserted = fields


class FakeStageStatusesRepo:
    def get_all(self) -> list[dict[str, Any]]:
        return [{"key": "applied", "is_terminal": False}]


def _make_app(monkeypatch: pytest.MonkeyPatch, repos: dict[str, Any]) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(applications_bp, url_prefix="/api")

    @contextmanager
    def fake_get_repos():
        yield repos

    monkeypatch.setattr("routes.applications.current_user_id", lambda: _TEST_USER_ID)
    monkeypatch.setattr("routes.applications.get_repos", fake_get_repos)
    return app


# ─── add_application_sheet_row ─────────────────────────────────────────────


def test_add_application_row_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs_repo = FakeJobsRepo()
    app = _make_app(monkeypatch, {"jobs": jobs_repo, "stages": FakeStagesRepo(), "conn": FakeConnection()})
    client = app.test_client()

    response = client.post(
        "/api/applications", json={"company": "Acme", "status": "applied"}
    )

    assert response.status_code == 201
    assert jobs_repo.inserted["company"] == "Acme"
    assert jobs_repo.inserted["status"] == "applied"


def test_add_application_row_rejects_missing_company_and_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(
        monkeypatch, {"jobs": FakeJobsRepo(), "stages": FakeStagesRepo(), "conn": FakeConnection()}
    )
    client = app.test_client()

    response = client.post("/api/applications", json={"notes": "just notes"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "company or job_title is required"


def test_add_application_row_ignores_unknown_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """extra="ignore" -- the frontend hands the whole edited row object
    through, which may carry bookkeeping keys like row_number."""
    jobs_repo = FakeJobsRepo()
    app = _make_app(monkeypatch, {"jobs": jobs_repo, "stages": FakeStagesRepo(), "conn": FakeConnection()})
    client = app.test_client()

    response = client.post(
        "/api/applications",
        json={"company": "Acme", "row_number": 3, "id": 999},
    )

    assert response.status_code == 201
    assert "row_number" not in jobs_repo.inserted
    assert "id" not in jobs_repo.inserted


# ─── update_application_sheet_row ──────────────────────────────────────────


def test_update_application_row_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs_repo = FakeJobsRepo()
    app = _make_app(monkeypatch, {"jobs": jobs_repo, "conn": FakeConnection()})
    client = app.test_client()

    response = client.put("/api/applications/5", json={"company": "New Co"})

    assert response.status_code == 200
    assert jobs_repo.updated == {"company": "New Co"}


def test_update_application_row_rejects_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch, {"jobs": FakeJobsRepo(), "conn": FakeConnection()})
    client = app.test_client()

    response = client.put("/api/applications/5", json={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "company or job_title is required"


# ─── add_application_sheet_stage ───────────────────────────────────────────


def test_add_application_stage_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    stages_repo = FakeStagesRepo()
    app = _make_app(
        monkeypatch,
        {
            "jobs": FakeJobsRepo(),
            "stages": stages_repo,
            "stage_statuses": FakeStageStatusesRepo(),
            "conn": FakeConnection(),
        },
    )
    client = app.test_client()

    response = client.post(
        "/api/applications/5/stages",
        json={"status": "applied", "date": "2026-09-29"},
    )

    assert response.status_code == 201
    assert stages_repo.inserted["status"] == "applied"
    assert stages_repo.inserted["job_id"] == 5


def test_add_application_stage_rejects_missing_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(
        monkeypatch,
        {
            "jobs": FakeJobsRepo(),
            "stages": FakeStagesRepo(),
            "stage_statuses": FakeStageStatusesRepo(),
            "conn": FakeConnection(),
        },
    )
    client = app.test_client()

    response = client.post("/api/applications/5/stages", json={"date": "2026-09-29"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "status is required"


def test_add_application_stage_rejects_missing_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(
        monkeypatch,
        {
            "jobs": FakeJobsRepo(),
            "stages": FakeStagesRepo(),
            "stage_statuses": FakeStageStatusesRepo(),
            "conn": FakeConnection(),
        },
    )
    client = app.test_client()

    response = client.post("/api/applications/5/stages", json={"status": "applied"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "date is required"


def test_add_application_stage_rejects_invalid_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(
        monkeypatch,
        {
            "jobs": FakeJobsRepo(),
            "stages": FakeStagesRepo(),
            "stage_statuses": FakeStageStatusesRepo(),
            "conn": FakeConnection(),
        },
    )
    client = app.test_client()

    response = client.post(
        "/api/applications/5/stages",
        json={"status": "not_a_real_status", "date": "2026-09-29"},
    )

    assert response.status_code == 400
    assert "Invalid status" in response.get_json()["error"]
