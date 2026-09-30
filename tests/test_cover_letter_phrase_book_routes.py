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
"""routes/cover_letter.py's phrase-book routes and learn_cover_letter_style --
none of these had route-level test coverage before. Covers the 2026-09-29
Pydantic validation cleanup."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from flask import Flask

from routes.cover_letter import cover_letter_bp

_TEST_USER_ID = 1


class FakeConnection:
    def commit(self) -> None:
        pass


class FakePhrasesRepo:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {
            1: {"id": 1, "tag": "mission", "label": "Mission", "phrase_text": "old text"}
        }
        self._next_id = 2

    def insert(self, user_id: int, fields: dict[str, Any]) -> int:
        row_id = self._next_id
        self._next_id += 1
        self.rows[row_id] = {"id": row_id, **fields}
        return row_id

    def get_by_id(self, user_id: int, phrase_id: int) -> dict[str, Any] | None:
        row = self.rows.get(phrase_id)
        return dict(row) if row else None

    def update(self, user_id: int, phrase_id: int, fields: dict[str, Any]) -> None:
        self.rows[phrase_id].update(fields)


def _make_app(monkeypatch: pytest.MonkeyPatch, phrases_repo: FakePhrasesRepo) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(cover_letter_bp, url_prefix="/api")

    @contextmanager
    def fake_get_repos():
        yield {"cover_letter_phrases": phrases_repo, "conn": FakeConnection()}

    monkeypatch.setattr("routes.cover_letter.current_user_id", lambda: _TEST_USER_ID)
    monkeypatch.setattr("routes.cover_letter.get_repos", fake_get_repos)
    return app


# ─── create_phrase_book_entry / update_phrase_book_entry ──────────────────


def test_create_phrase_book_entry_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch, FakePhrasesRepo())
    client = app.test_client()

    response = client.post(
        "/api/phrase-book",
        json={"tag": "growth", "label": "Growth", "phrase_text": "We care about growth."},
    )

    assert response.status_code == 201
    data = response.get_json()
    assert data["tag"] == "growth"


def test_create_phrase_book_entry_rejects_missing_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(monkeypatch, FakePhrasesRepo())
    client = app.test_client()

    response = client.post("/api/phrase-book", json={"tag": "growth"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "tag, label, and phrase_text are required"


def test_update_phrase_book_entry_partial_update(monkeypatch: pytest.MonkeyPatch) -> None:
    phrases_repo = FakePhrasesRepo()
    app = _make_app(monkeypatch, phrases_repo)
    client = app.test_client()

    response = client.put("/api/phrase-book/1", json={"label": "New Label"})

    assert response.status_code == 200
    data = response.get_json()
    assert data["label"] == "New Label"
    assert data["tag"] == "mission"


def test_update_phrase_book_entry_rejects_blank_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(monkeypatch, FakePhrasesRepo())
    client = app.test_client()

    response = client.put("/api/phrase-book/1", json={"label": "   "})

    assert response.status_code == 400
    assert response.get_json()["error"] == "label cannot be empty"


def test_update_phrase_book_entry_404_for_unknown_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _make_app(monkeypatch, FakePhrasesRepo())
    client = app.test_client()

    response = client.put("/api/phrase-book/999", json={"label": "x"})

    assert response.status_code == 404


# ─── learn_cover_letter_style ──────────────────────────────────────────────


def test_learn_cover_letter_style_rejects_missing_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = Flask(__name__)
    app.register_blueprint(cover_letter_bp, url_prefix="/api")
    client = app.test_client()

    response = client.post(
        "/api/jobs/7/cover-letter/learn", json={"draft": "draft text"}
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "draft and final text are required"


def test_learn_cover_letter_style_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("routes.cover_letter.current_user_id", lambda: _TEST_USER_ID)

    class FakeLearner:
        def __init__(self, user_id: int) -> None:
            assert user_id == _TEST_USER_ID

        def learn_from_edit(self, draft, final, chat_messages):
            assert draft == "draft text"
            assert final == "final text"
            return ["rule 1"]

    monkeypatch.setattr("routes.cover_letter.CoverLetterStyleLearner", FakeLearner)

    app = Flask(__name__)
    app.register_blueprint(cover_letter_bp, url_prefix="/api")
    client = app.test_client()

    response = client.post(
        "/api/jobs/7/cover-letter/learn",
        json={"draft": "draft text", "final": "final text"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"applied": ["rule 1"]}


def test_learn_cover_letter_style_defaults_bad_chat_messages_to_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("routes.cover_letter.current_user_id", lambda: _TEST_USER_ID)

    captured: dict[str, Any] = {}

    class FakeLearner:
        def __init__(self, user_id: int) -> None:
            pass

        def learn_from_edit(self, draft, final, chat_messages):
            captured["chat_messages"] = chat_messages
            return []

    monkeypatch.setattr("routes.cover_letter.CoverLetterStyleLearner", FakeLearner)

    app = Flask(__name__)
    app.register_blueprint(cover_letter_bp, url_prefix="/api")
    client = app.test_client()

    response = client.post(
        "/api/jobs/7/cover-letter/learn",
        json={"draft": "draft text", "final": "final text", "chat_messages": "not a list"},
    )

    assert response.status_code == 200
    assert captured["chat_messages"] == []
