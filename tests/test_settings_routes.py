# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-09-29
"""routes/settings.py's penalty-rule, email-subject-rule, and update_config
(non-SSRF fields) routes -- none had route-level test coverage before.
Covers the 2026-09-29 Pydantic validation cleanup. See
tests/test_ssrf_protection.py for update_config's ollama_base_url /
no-session ordering coverage, which is untouched by this file."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from flask import Flask

from routes.settings import settings_bp

_TEST_USER_ID = 1


class FakeConnection:
    def commit(self) -> None:
        pass


class FakePenaltiesRepo:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {1: {"id": 1, "rule_text": "old", "penalty_points": 5}}
        self._next_id = 2

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


class FakeEmailSubjectRulesRepo:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {
            1: {"id": 1, "pattern": "old", "enabled": 1, "rule_type": "blacklist"}
        }
        self._next_id = 2

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


def _make_app(monkeypatch: pytest.MonkeyPatch, repos: dict[str, Any]) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(settings_bp, url_prefix="/api")

    @contextmanager
    def fake_get_repos():
        yield repos

    monkeypatch.setattr("routes.settings.current_user_id", lambda: _TEST_USER_ID)
    monkeypatch.setattr("routes.settings.get_repos", fake_get_repos)
    return app


# ─── create_penalty / update_penalty ───────────────────────────────────────


def test_create_penalty_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    penalties_repo = FakePenaltiesRepo()
    app = _make_app(monkeypatch, {"penalties": penalties_repo, "conn": FakeConnection()})
    client = app.test_client()

    response = client.post(
        "/api/penalties", json={"rule_text": "too junior", "penalty_points": 10}
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data["penalty_points"] == 10
    assert data["scope"] == "any"
    assert data["enabled"] == 1


def test_create_penalty_rejects_missing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch, {"penalties": FakePenaltiesRepo(), "conn": FakeConnection()})
    client = app.test_client()

    response = client.post("/api/penalties", json={"rule_text": "x"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "rule_text and penalty_points are required"


def test_create_penalty_rejects_non_numeric_points(monkeypatch: pytest.MonkeyPatch) -> None:
    """Previously an unhandled int() ValueError escaped as a 500 -- now a
    clean 400 (see PenaltyCreate's docstring)."""
    app = _make_app(monkeypatch, {"penalties": FakePenaltiesRepo(), "conn": FakeConnection()})
    client = app.test_client()

    response = client.post(
        "/api/penalties", json={"rule_text": "x", "penalty_points": "not a number"}
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "penalty_points must be a number"


def test_create_penalty_rejects_invalid_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch, {"penalties": FakePenaltiesRepo(), "conn": FakeConnection()})
    client = app.test_client()

    response = client.post(
        "/api/penalties",
        json={"rule_text": "x", "penalty_points": 5, "scope": "bogus"},
    )

    assert response.status_code == 400
    assert "scope must be one of" in response.get_json()["error"]


def test_update_penalty_partial_update(monkeypatch: pytest.MonkeyPatch) -> None:
    penalties_repo = FakePenaltiesRepo()
    app = _make_app(monkeypatch, {"penalties": penalties_repo, "conn": FakeConnection()})
    client = app.test_client()

    response = client.put("/api/penalties/1", json={"penalty_points": 20})

    assert response.status_code == 200
    data = response.get_json()
    assert data["penalty_points"] == 20
    assert data["rule_text"] == "old"


def test_update_penalty_rejects_unknown_field(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch, {"penalties": FakePenaltiesRepo(), "conn": FakeConnection()})
    client = app.test_client()

    response = client.put("/api/penalties/1", json={"unknown_field": "x"})

    assert response.status_code == 400


def test_update_penalty_404_for_unknown_id(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch, {"penalties": FakePenaltiesRepo(), "conn": FakeConnection()})
    client = app.test_client()

    response = client.put("/api/penalties/999", json={"penalty_points": 1})

    assert response.status_code == 404


# ─── create_email_subject_rule / update_email_subject_rule ────────────────


def test_create_email_subject_rule_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    rules_repo = FakeEmailSubjectRulesRepo()
    app = _make_app(monkeypatch, {"email_subject_rules": rules_repo, "conn": FakeConnection()})
    client = app.test_client()

    response = client.post(
        "/api/email-subject-rules", json={"pattern": "URGENT", "rule_type": "whitelist"}
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data["pattern"] == "urgent"
    assert data["rule_type"] == "whitelist"


def test_create_email_subject_rule_rejects_missing_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(
        monkeypatch, {"email_subject_rules": FakeEmailSubjectRulesRepo(), "conn": FakeConnection()}
    )
    client = app.test_client()

    response = client.post("/api/email-subject-rules", json={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "pattern is required"


def test_create_email_subject_rule_rejects_invalid_rule_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(
        monkeypatch, {"email_subject_rules": FakeEmailSubjectRulesRepo(), "conn": FakeConnection()}
    )
    client = app.test_client()

    response = client.post(
        "/api/email-subject-rules", json={"pattern": "x", "rule_type": "bogus"}
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "rule_type must be blacklist or whitelist"


def test_update_email_subject_rule_partial_update(monkeypatch: pytest.MonkeyPatch) -> None:
    rules_repo = FakeEmailSubjectRulesRepo()
    app = _make_app(monkeypatch, {"email_subject_rules": rules_repo, "conn": FakeConnection()})
    client = app.test_client()

    response = client.put("/api/email-subject-rules/1", json={"pattern": "NEW"})

    assert response.status_code == 200
    assert response.get_json()["pattern"] == "new"


def test_update_email_subject_rule_rejects_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(
        monkeypatch, {"email_subject_rules": FakeEmailSubjectRulesRepo(), "conn": FakeConnection()}
    )
    client = app.test_client()

    response = client.put("/api/email-subject-rules/1", json={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "No data provided"


def test_update_email_subject_rule_404_for_unknown_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(
        monkeypatch, {"email_subject_rules": FakeEmailSubjectRulesRepo(), "conn": FakeConnection()}
    )
    client = app.test_client()

    response = client.put("/api/email-subject-rules/999", json={"pattern": "x"})

    assert response.status_code == 404


# ─── update_config (non-SSRF fields) ───────────────────────────────────────


def test_update_config_rejects_out_of_range_lookback_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = Flask(__name__)
    app.register_blueprint(settings_bp, url_prefix="/api")
    client = app.test_client()

    response = client.put("/api/config", json={"email_lookback_days": 0})

    assert response.status_code == 400
    assert response.get_json()["error"] == "email_lookback_days must be at least 1"


def test_update_config_rejects_non_numeric_lookback_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = Flask(__name__)
    app.register_blueprint(settings_bp, url_prefix="/api")
    client = app.test_client()

    response = client.put("/api/config", json={"email_lookback_days": "abc"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "email_lookback_days must be a number"


def test_update_config_rejects_invalid_ai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    app = Flask(__name__)
    app.register_blueprint(settings_bp, url_prefix="/api")
    client = app.test_client()

    response = client.put("/api/config", json={"ai_provider": "bogus"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "ai_provider must be anthropic, openai, or ollama"


def test_update_config_rejects_bad_jd_health_check_time() -> None:
    app = Flask(__name__)
    app.register_blueprint(settings_bp, url_prefix="/api")
    client = app.test_client()

    response = client.put("/api/config", json={"jd_health_check_time_utc": "25:99"})

    assert response.status_code == 400
    assert (
        response.get_json()["error"]
        == "jd_health_check_time_utc must be in HH:MM 24-hour format"
    )


def test_update_config_rejects_empty_body() -> None:
    app = Flask(__name__)
    app.register_blueprint(settings_bp, url_prefix="/api")
    client = app.test_client()

    response = client.put("/api/config", json={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "No data provided"
