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
"""routes/career_sites.py write routes -- create/update career site, create/
update career rejection rule, reject a scan result, and set a scan result's
tracking status. Covers the 2026-09-29 Pydantic validation cleanup, including
the new validate_outbound_url fail-fast check on career-site URLs (previously
only enforced at actual scan/fetch time via safe_urlopen)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from flask import Flask

from routes.career_sites import career_sites_bp

_TEST_USER_ID = 1


class FakeConnection:
    def commit(self) -> None:
        pass


class FakeCareerSitesRepo:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {
            5: {"id": 5, "name": "Acme", "url": "https://acme.example/jobs"}
        }
        self._next_id = 6

    def insert(self, user_id: int, fields: dict[str, Any]) -> int:
        row_id = self._next_id
        self._next_id += 1
        self.rows[row_id] = {"id": row_id, **fields}
        return row_id

    def get_by_id(self, user_id: int, source_id: int) -> dict[str, Any] | None:
        row = self.rows.get(source_id)
        return dict(row) if row else None

    def update(self, user_id: int, source_id: int, fields: dict[str, Any]) -> None:
        self.rows[source_id].update(fields)


class FakeRejectionRulesRepo:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self._next_id = 1

    def insert(self, user_id: int, fields: dict[str, Any]) -> int:
        row_id = self._next_id
        self._next_id += 1
        self.rows[row_id] = {"id": row_id, **fields}
        return row_id

    def get_by_id(self, user_id: int, rule_id: int) -> dict[str, Any] | None:
        row = self.rows.get(rule_id)
        return dict(row) if row else None

    def update(self, user_id: int, rule_id: int, fields: dict[str, Any]) -> None:
        self.rows[rule_id].update(fields)


class FakeScanResultsRepo:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {9: {"id": 9, "tracking_status": "New"}}

    def get_by_id(self, user_id: int, row_id: int) -> dict[str, Any] | None:
        row = self.rows.get(row_id)
        return dict(row) if row else None

    def update(self, user_id: int, row_id: int, fields: dict[str, Any]) -> None:
        self.rows[row_id].update(fields)


def _make_app(monkeypatch: pytest.MonkeyPatch, repos: dict[str, Any]) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(career_sites_bp, url_prefix="/api")

    @contextmanager
    def fake_get_repos():
        yield repos

    monkeypatch.setattr("routes.career_sites.current_user_id", lambda: _TEST_USER_ID)
    monkeypatch.setattr("routes.career_sites.get_repos", fake_get_repos)
    monkeypatch.setattr(
        "routes.career_sites.platform_for_site_url", lambda url: "generic"
    )
    return app


# ─── create_career_site ────────────────────────────────────────────────────


def test_create_career_site_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sites_repo = FakeCareerSitesRepo()
    app = _make_app(monkeypatch, {"career_sites": sites_repo, "conn": FakeConnection()})
    monkeypatch.setattr("routes.career_sites.validate_outbound_url", lambda url: None)
    client = app.test_client()

    response = client.post(
        "/api/career-sites",
        json={"name": "Beta Co", "url": "https://beta.example/careers"},
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data["name"] == "Beta Co"
    assert data["enabled"] == 1
    assert data["collection_mode"] == "automatic"
    assert data["recapture_interval_days"] == 4


def test_create_career_site_rejects_missing_url(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(
        monkeypatch, {"career_sites": FakeCareerSitesRepo(), "conn": FakeConnection()}
    )
    client = app.test_client()

    response = client.post("/api/career-sites", json={"name": "Beta Co"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "name and url are required"


def test_create_career_site_rejects_unsafe_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """New 2026-09-29 fail-fast check: previously an unsafe url was only
    ever rejected later, at scan time, by CareerSiteScanner's safe_urlopen."""
    app = _make_app(
        monkeypatch, {"career_sites": FakeCareerSitesRepo(), "conn": FakeConnection()}
    )
    client = app.test_client()

    response = client.post(
        "/api/career-sites",
        json={"name": "Internal", "url": "http://127.0.0.1:8000/jobs"},
    )

    assert response.status_code == 400
    assert "url rejected" in response.get_json()["error"]


def test_create_career_site_rejects_invalid_collection_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(
        monkeypatch, {"career_sites": FakeCareerSitesRepo(), "conn": FakeConnection()}
    )
    monkeypatch.setattr("routes.career_sites.validate_outbound_url", lambda url: None)
    client = app.test_client()

    response = client.post(
        "/api/career-sites",
        json={
            "name": "Beta Co",
            "url": "https://beta.example/careers",
            "collection_mode": "not_a_mode",
        },
    )

    assert response.status_code == 400
    assert "collection_mode" in response.get_json()["error"]


# ─── update_career_site ────────────────────────────────────────────────────


def test_update_career_site_partial_update(monkeypatch: pytest.MonkeyPatch) -> None:
    sites_repo = FakeCareerSitesRepo()
    app = _make_app(monkeypatch, {"career_sites": sites_repo, "conn": FakeConnection()})
    client = app.test_client()

    response = client.put("/api/career-sites/5", json={"enabled": False})

    assert response.status_code == 200
    data = response.get_json()
    assert data["enabled"] == 0
    assert data["name"] == "Acme"


def test_update_career_site_rejects_unsafe_url(monkeypatch: pytest.MonkeyPatch) -> None:
    sites_repo = FakeCareerSitesRepo()
    app = _make_app(monkeypatch, {"career_sites": sites_repo, "conn": FakeConnection()})
    client = app.test_client()

    response = client.put(
        "/api/career-sites/5", json={"url": "http://169.254.169.254/latest/meta-data/"}
    )

    assert response.status_code == 400
    assert "url rejected" in response.get_json()["error"]


def test_update_career_site_404_for_unknown_id(monkeypatch: pytest.MonkeyPatch) -> None:
    sites_repo = FakeCareerSitesRepo()
    app = _make_app(monkeypatch, {"career_sites": sites_repo, "conn": FakeConnection()})
    client = app.test_client()

    response = client.put("/api/career-sites/999", json={"name": "New Name"})

    assert response.status_code == 404


# ─── career rejection rules ────────────────────────────────────────────────


def test_create_career_rejection_rule_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    rules_repo = FakeRejectionRulesRepo()
    app = _make_app(
        monkeypatch, {"career_rejection_rules": rules_repo, "conn": FakeConnection()}
    )
    client = app.test_client()

    response = client.post(
        "/api/career-rejection-rules", json={"rule_text": "contains 'unpaid'"}
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data["rule_text"] == "contains 'unpaid'"
    assert data["enabled"] == 1


def test_create_career_rejection_rule_rejects_blank_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(
        monkeypatch,
        {"career_rejection_rules": FakeRejectionRulesRepo(), "conn": FakeConnection()},
    )
    client = app.test_client()

    response = client.post("/api/career-rejection-rules", json={"rule_text": "   "})

    assert response.status_code == 400
    assert response.get_json()["error"] == "rule_text is required"


def test_update_career_rejection_rule_partial_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rules_repo = FakeRejectionRulesRepo()
    rules_repo.rows[1] = {"id": 1, "rule_text": "old", "enabled": 1}
    app = _make_app(
        monkeypatch, {"career_rejection_rules": rules_repo, "conn": FakeConnection()}
    )
    client = app.test_client()

    response = client.put("/api/career-rejection-rules/1", json={"enabled": False})

    assert response.status_code == 200
    assert response.get_json()["enabled"] == 0


# ─── reject_career_scan_result ─────────────────────────────────────────────


def test_reject_career_scan_result_without_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    results_repo = FakeScanResultsRepo()
    app = _make_app(
        monkeypatch,
        {
            "career_scan_results": results_repo,
            "career_rejection_rules": FakeRejectionRulesRepo(),
            "conn": FakeConnection(),
        },
    )
    client = app.test_client()

    response = client.post(
        "/api/career-scan/results/9/reject", json={"reason": "Not a fit"}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["row"]["tracking_status"] == "Rejected"
    assert data["created_rule"] is None


def test_reject_career_scan_result_with_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    results_repo = FakeScanResultsRepo()
    rules_repo = FakeRejectionRulesRepo()
    app = _make_app(
        monkeypatch,
        {
            "career_scan_results": results_repo,
            "career_rejection_rules": rules_repo,
            "conn": FakeConnection(),
        },
    )
    client = app.test_client()

    response = client.post(
        "/api/career-scan/results/9/reject",
        json={"reason": "Not a fit", "add_as_rule": True},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["created_rule"]["rule_text"] == "Not a fit"


def test_reject_career_scan_result_rejects_missing_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(
        monkeypatch,
        {
            "career_scan_results": FakeScanResultsRepo(),
            "career_rejection_rules": FakeRejectionRulesRepo(),
            "conn": FakeConnection(),
        },
    )
    client = app.test_client()

    response = client.post("/api/career-scan/results/9/reject", json={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "reason is required"


# ─── set_career_scan_result_tracking_status ────────────────────────────────


def test_set_tracking_status_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    results_repo = FakeScanResultsRepo()
    app = _make_app(
        monkeypatch, {"career_scan_results": results_repo, "conn": FakeConnection()}
    )
    client = app.test_client()

    response = client.post(
        "/api/career-scan/results/9/tracking-status", json={"status": "Applied"}
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["tracking_status"] == "Applied"
    assert data["tracking_source"] == "manual_applied"


def test_set_tracking_status_rejects_invalid_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(
        monkeypatch,
        {"career_scan_results": FakeScanResultsRepo(), "conn": FakeConnection()},
    )
    client = app.test_client()

    response = client.post(
        "/api/career-scan/results/9/tracking-status", json={"status": "Bogus"}
    )

    assert response.status_code == 400
    assert "status must be one of" in response.get_json()["error"]
