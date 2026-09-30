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
"""routes/emails.py write routes -- set_email_hidden, move_scanned_emails,
clean_displayed_emails, create_source, update_source. Covers the 2026-09-29
Pydantic validation cleanup."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from flask import Flask

from routes.emails import emails_bp

_TEST_USER_ID = 1


class FakeConnection:
    def commit(self) -> None:
        pass


class FakeScannedEmailsRepo:
    def __init__(self) -> None:
        self.hidden_calls: list[tuple[str, bool]] = []

    def set_hidden(self, user_id: int, email_id: str, hidden: bool) -> None:
        self.hidden_calls.append((email_id, hidden))


class FakeSourcesRepo:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {
            1: {"id": 1, "name": "Existing", "match_type": "sender_domain", "match_value": "x.com"}
        }
        self._next_id = 2

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


def _make_app(monkeypatch: pytest.MonkeyPatch, repos: dict[str, Any]) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(emails_bp, url_prefix="/api")

    @contextmanager
    def fake_get_repos():
        yield repos

    monkeypatch.setattr("routes.emails.current_user_id", lambda: _TEST_USER_ID)
    monkeypatch.setattr("routes.emails.get_repos", fake_get_repos)
    return app


# ─── set_email_hidden ──────────────────────────────────────────────────────


def test_set_email_hidden_defaults_true(monkeypatch: pytest.MonkeyPatch) -> None:
    scanned_repo = FakeScannedEmailsRepo()
    app = _make_app(
        monkeypatch, {"scanned_emails": scanned_repo, "conn": FakeConnection()}
    )
    client = app.test_client()

    response = client.post("/api/emails/abc123/hide", json={})

    assert response.status_code == 200
    assert response.get_json() == {"hidden": True}
    assert scanned_repo.hidden_calls == [("abc123", True)]


def test_set_email_hidden_false(monkeypatch: pytest.MonkeyPatch) -> None:
    scanned_repo = FakeScannedEmailsRepo()
    app = _make_app(
        monkeypatch, {"scanned_emails": scanned_repo, "conn": FakeConnection()}
    )
    client = app.test_client()

    response = client.post("/api/emails/abc123/hide", json={"hidden": False})

    assert response.status_code == 200
    assert scanned_repo.hidden_calls == [("abc123", False)]


# ─── move_scanned_emails / clean_displayed_emails ──────────────────────────


def test_move_scanned_emails_rejects_non_list() -> None:
    app = Flask(__name__)
    app.register_blueprint(emails_bp, url_prefix="/api")
    client = app.test_client()

    response = client.post("/api/emails/move-scanned", json={"email_ids": "not-a-list"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "email_ids must be a list"


def test_move_scanned_emails_rejects_empty_selection() -> None:
    app = Flask(__name__)
    app.register_blueprint(emails_bp, url_prefix="/api")
    client = app.test_client()

    response = client.post("/api/emails/move-scanned", json={"email_ids": []})

    assert response.status_code == 400
    assert response.get_json()["error"] == "Select at least one email"


def test_clean_displayed_emails_rejects_non_list() -> None:
    app = Flask(__name__)
    app.register_blueprint(emails_bp, url_prefix="/api")
    client = app.test_client()

    response = client.post("/api/emails/clean", json={"email_ids": {}})

    assert response.status_code == 400
    assert response.get_json()["error"] == "email_ids must be a list"


def test_clean_displayed_emails_empty_selection_is_a_no_op_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("routes.emails.current_user_id", lambda: _TEST_USER_ID)

    app = Flask(__name__)
    app.register_blueprint(emails_bp, url_prefix="/api")
    client = app.test_client()

    response = client.post("/api/emails/clean", json={"email_ids": []})

    assert response.status_code == 200
    assert response.get_json() == {"checked": 0, "hidden": 0, "hidden_email_ids": []}


# ─── create_source / update_source ─────────────────────────────────────────


def test_create_source_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sources_repo = FakeSourcesRepo()
    app = _make_app(monkeypatch, {"sources": sources_repo, "conn": FakeConnection()})
    client = app.test_client()

    response = client.post(
        "/api/sources",
        json={
            "name": "New Source",
            "match_type": "sender_domain",
            "match_value": "example.com",
        },
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data["name"] == "New Source"
    assert data["enabled"] == 1
    assert data["subject_filter_match_mode"] == "text"


def test_create_source_rejects_missing_match_type() -> None:
    app = Flask(__name__)
    app.register_blueprint(emails_bp, url_prefix="/api")
    client = app.test_client()

    response = client.post(
        "/api/sources", json={"name": "New Source", "match_value": "example.com"}
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "match_type is required"


def test_create_source_rejects_all_fields_missing() -> None:
    app = Flask(__name__)
    app.register_blueprint(emails_bp, url_prefix="/api")
    client = app.test_client()

    response = client.post("/api/sources", json={})

    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] == "Invalid request"
    assert sorted(body["fields"]) == ["match_type", "match_value", "name"]


def test_create_source_rejects_invalid_match_type() -> None:
    app = Flask(__name__)
    app.register_blueprint(emails_bp, url_prefix="/api")
    client = app.test_client()

    response = client.post(
        "/api/sources",
        json={"name": "x", "match_type": "bogus", "match_value": "y"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "match_type is invalid"


def test_create_source_rejects_filter_value_without_mode() -> None:
    app = Flask(__name__)
    app.register_blueprint(emails_bp, url_prefix="/api")
    client = app.test_client()

    response = client.post(
        "/api/sources",
        json={
            "name": "x",
            "match_type": "sender_domain",
            "match_value": "y",
            "subject_filter_value": "urgent",
        },
    )

    assert response.status_code == 400
    assert (
        response.get_json()["error"]
        == "subject_filter_mode is required when a subject filter value is provided"
    )


def test_update_source_partial_update(monkeypatch: pytest.MonkeyPatch) -> None:
    sources_repo = FakeSourcesRepo()
    app = _make_app(monkeypatch, {"sources": sources_repo, "conn": FakeConnection()})
    client = app.test_client()

    response = client.put("/api/sources/1", json={"enabled": False})

    assert response.status_code == 200
    data = response.get_json()
    assert data["enabled"] == 0
    assert data["name"] == "Existing"


def test_update_source_rejects_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    sources_repo = FakeSourcesRepo()
    app = _make_app(monkeypatch, {"sources": sources_repo, "conn": FakeConnection()})
    client = app.test_client()

    response = client.put("/api/sources/1", json={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "No data provided"


def test_update_source_404_for_unknown_id(monkeypatch: pytest.MonkeyPatch) -> None:
    sources_repo = FakeSourcesRepo()
    app = _make_app(monkeypatch, {"sources": sources_repo, "conn": FakeConnection()})
    client = app.test_client()

    response = client.put("/api/sources/999", json={"name": "New Name"})

    assert response.status_code == 404
