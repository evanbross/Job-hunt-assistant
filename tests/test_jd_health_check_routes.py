# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-09-29
"""POST/PUT /jd-health-check/targets -- covers the 2026-09-29 Pydantic
validation cleanup, including the new validate_outbound_url fail-fast check
on target URLs (these targets feed the same CareerSiteScanner fetch path,
via safe_urlopen, as routes/career_sites.py's career sites)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import redis
from flask import Flask

import config
from backend.celery_app import celery_app
from backend.security.task_ownership import TaskOwnership
from backend.task_liveness import WorkerLiveness, set_worker_liveness
from backend.task_progress import WORKER_STOPPED_MESSAGE
from routes.jd_health_check import jd_health_check_bp

_TEST_USER_ID = 1


class FakeConnection:
    def commit(self) -> None:
        pass


class FakeTargetsRepo:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {
            3: {"id": 3, "platform": "greenhouse", "label": "Canary", "url": "https://x"}
        }
        self._next_id = 4

    def insert(self, user_id: int, fields: dict[str, Any]) -> int:
        row_id = self._next_id
        self._next_id += 1
        self.rows[row_id] = {"id": row_id, **fields}
        return row_id

    def get_by_id(self, user_id: int, target_id: int) -> dict[str, Any] | None:
        row = self.rows.get(target_id)
        return dict(row) if row else None

    def update(self, user_id: int, target_id: int, fields: dict[str, Any]) -> None:
        self.rows[target_id].update(fields)


def _make_app(monkeypatch: pytest.MonkeyPatch, targets_repo: FakeTargetsRepo) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(jd_health_check_bp, url_prefix="/api")

    @contextmanager
    def fake_get_repos():
        yield {"jd_health_targets": targets_repo, "conn": FakeConnection()}

    monkeypatch.setattr("routes.jd_health_check.current_user_id", lambda: _TEST_USER_ID)
    monkeypatch.setattr("routes.jd_health_check.get_repos", fake_get_repos)
    return app


def test_create_target_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "routes.jd_health_check.validate_outbound_url", lambda url: None
    )
    app = _make_app(monkeypatch, FakeTargetsRepo())
    client = app.test_client()

    response = client.post(
        "/api/jd-health-check/targets",
        json={
            "platform": "greenhouse",
            "label": "New Canary",
            "url": "https://boards.greenhouse.io/example",
        },
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data["platform"] == "greenhouse"
    assert data["enabled"] == 1


def test_create_target_rejects_unknown_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch, FakeTargetsRepo())
    client = app.test_client()

    response = client.post(
        "/api/jd-health-check/targets",
        json={"platform": "bogus", "label": "x", "url": "https://boards.greenhouse.io/x"},
    )

    assert response.status_code == 400
    assert "platform must be one of" in response.get_json()["error"]


def test_create_target_rejects_missing_label_and_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(monkeypatch, FakeTargetsRepo())
    client = app.test_client()

    response = client.post(
        "/api/jd-health-check/targets", json={"platform": "greenhouse"}
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "label and url are required"


def test_create_target_rejects_unsafe_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """New 2026-09-29 fail-fast check, same as routes/career_sites.py --
    previously an unsafe url was only ever caught later, at scan time."""
    app = _make_app(monkeypatch, FakeTargetsRepo())
    client = app.test_client()

    response = client.post(
        "/api/jd-health-check/targets",
        json={
            "platform": "greenhouse",
            "label": "Internal",
            "url": "http://127.0.0.1:8000/jobs",
        },
    )

    assert response.status_code == 400
    assert "url rejected" in response.get_json()["error"]


def test_update_target_partial_update(monkeypatch: pytest.MonkeyPatch) -> None:
    targets_repo = FakeTargetsRepo()
    app = _make_app(monkeypatch, targets_repo)
    client = app.test_client()

    response = client.put(
        "/api/jd-health-check/targets/3", json={"enabled": False}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["enabled"] == 0
    assert data["label"] == "Canary"


def test_update_target_rejects_unsafe_url(monkeypatch: pytest.MonkeyPatch) -> None:
    targets_repo = FakeTargetsRepo()
    app = _make_app(monkeypatch, targets_repo)
    client = app.test_client()

    response = client.put(
        "/api/jd-health-check/targets/3",
        json={"url": "http://169.254.169.254/latest/meta-data/"},
    )

    assert response.status_code == 400
    assert "url rejected" in response.get_json()["error"]


def test_update_target_404_for_unknown_id(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch, FakeTargetsRepo())
    client = app.test_client()

    response = client.put(
        "/api/jd-health-check/targets/999", json={"label": "New"}
    )

    assert response.status_code == 404


# ─── run status: a run whose worker died ──────────────────────────────────────


# ai: claude-opus-5-5 | 2026-11-17
@pytest.fixture
def liveness() -> Iterator[WorkerLiveness]:
    """Real Redis (the result backend too) under a throwaway prefix."""
    client = redis.Redis.from_url(config.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    try:
        client.ping()
    except (redis.RedisError, OSError):
        pytest.skip("no Redis reachable at REDIS_URL")
    prefix = f"jha:test-liveness:{uuid.uuid4().hex}"
    worker_liveness = WorkerLiveness(client, key_prefix=prefix)
    set_worker_liveness(worker_liveness)
    yield worker_liveness
    set_worker_liveness(None)
    for key in client.scan_iter(f"{prefix}*"):
        client.delete(key)


# ai: claude-opus-5-5 | 2026-11-17
@pytest.fixture
def run_in_progress(isolated_task_ownership: TaskOwnership) -> Iterator[str]:
    """A run of this account's stored as PROGRESS in Celery's result backend."""
    task_id = f"test-{uuid.uuid4().hex}"
    celery_app.backend.store_result(task_id, {"checked": 2}, "PROGRESS")
    isolated_task_ownership.record(task_id, _TEST_USER_ID)
    yield task_id
    celery_app.backend.forget(task_id)


# ai: claude-opus-5-5 | 2026-11-17
def test_a_run_whose_worker_died_reports_failure(
    monkeypatch: pytest.MonkeyPatch, liveness: WorkerLiveness, run_in_progress: str
) -> None:
    """The panel polls until SUCCESS or FAILURE; a killed worker's run must end it."""
    client = _make_app(monkeypatch, FakeTargetsRepo()).test_client()

    response = client.get(f"/api/jd-health-check/run/{run_in_progress}/status")

    assert response.get_json() == {"state": "FAILURE", "error": WORKER_STOPPED_MESSAGE}


# ai: claude-opus-5-5 | 2026-11-17
def test_a_run_its_worker_still_holds_reports_progress(
    monkeypatch: pytest.MonkeyPatch, liveness: WorkerLiveness, run_in_progress: str
) -> None:
    liveness.heartbeat("celery@test-host", "boot-1")
    liveness.record_run(run_in_progress, "celery@test-host")
    client = _make_app(monkeypatch, FakeTargetsRepo()).test_client()

    response = client.get(f"/api/jd-health-check/run/{run_in_progress}/status")

    assert response.get_json() == {"state": "PROGRESS", "info": {"checked": 2}}
