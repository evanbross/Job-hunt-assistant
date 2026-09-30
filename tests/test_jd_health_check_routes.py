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
"""POST/PUT /jd-health-check/targets -- covers the 2026-09-29 Pydantic
validation cleanup, including the new validate_outbound_url fail-fast check
on target URLs (these targets feed the same CareerSiteScanner fetch path,
via safe_urlopen, as routes/career_sites.py's career sites)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from flask import Flask

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
