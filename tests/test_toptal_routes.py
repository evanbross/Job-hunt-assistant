# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-09-29
"""routes/toptal.py request validation -- generate_toptal_bio and
toptal_bio_chat's Pydantic conversion (2026-09-29 Pydantic validation
cleanup)."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from flask import Flask

from routes.toptal import toptal_bp


def _make_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(toptal_bp, url_prefix="/api")
    return app


def test_generate_bio_rejects_missing_job_description() -> None:
    client = _make_app().test_client()

    response = client.post("/api/toptal/bio", json={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "job_description is required"


def test_generate_bio_rejects_blank_job_description() -> None:
    client = _make_app().test_client()

    response = client.post("/api/toptal/bio", json={"job_description": "   "})

    assert response.status_code == 400
    assert response.get_json()["error"] == "job_description is required"


def test_generate_bio_rejects_unknown_field() -> None:
    client = _make_app().test_client()

    response = client.post(
        "/api/toptal/bio", json={"job_description": "A role.", "extra": "nope"}
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid request", "fields": ["extra"]}


def test_generate_bio_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("routes.toptal.current_user_id", lambda: 1)
    monkeypatch.setattr(
        "routes.toptal.get_repos", lambda: _fake_repos_context()
    )
    monkeypatch.setattr("routes.toptal.get_candidate_profile", lambda user_id, repos: "profile")
    monkeypatch.setattr(
        "routes.toptal.AIProviderClient",
        lambda user_id: _FakeAIClient(reply="Generated bio text."),
    )

    client = _make_app().test_client()
    response = client.post(
        "/api/toptal/bio", json={"job_description": "Build things for a client."}
    )

    assert response.status_code == 200
    assert response.get_json() == {"bio": "Generated bio text."}


def test_bio_chat_rejects_empty_messages() -> None:
    client = _make_app().test_client()

    response = client.post("/api/toptal/bio/chat", json={"messages": []})

    assert response.status_code == 400
    assert response.get_json()["error"] == "No messages provided"


def test_bio_chat_rejects_missing_messages() -> None:
    client = _make_app().test_client()

    response = client.post("/api/toptal/bio/chat", json={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "No messages provided"


# ai: claude-sonnet-5 | 2026-09-29
def test_bio_chat_rejects_all_malformed_messages() -> None:
    client = _make_app().test_client()

    response = client.post("/api/toptal/bio/chat", json={"messages": ["invalid"]})

    assert response.status_code == 400
    assert response.get_json()["error"] == "No valid messages provided"


def test_bio_chat_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("routes.toptal.current_user_id", lambda: 1)
    monkeypatch.setattr(
        "routes.toptal.get_repos", lambda: _fake_repos_context()
    )
    monkeypatch.setattr("routes.toptal.get_candidate_profile", lambda user_id, repos: "profile")
    monkeypatch.setattr(
        "routes.toptal.AIProviderClient",
        lambda user_id: _FakeAIClient(reply="Refined bio text."),
    )

    client = _make_app().test_client()
    response = client.post(
        "/api/toptal/bio/chat",
        json={"messages": [{"role": "user", "content": "Make it punchier."}]},
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["reply"] == "Refined bio text."
    assert data["updated_bio"] is None


@contextmanager
def _fake_repos_context():
    yield {}


class _FakeAIClient:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def complete(self, **kwargs: object) -> str:
        return self._reply
