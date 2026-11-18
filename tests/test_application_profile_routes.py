# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from flask import Flask

from backend.db import PostgresUserAccountRepository, get_connection
from backend.db.repositories.application_answers import (
    AnswerNotFoundError,
    QuestionTakenError,
)
from backend.utils.utils import get_repos
from routes.application_profile import application_profile_bp

# ai: claude-sonnet-5 | 2026-08-13


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


_FAKE_USER_ID = 1


class FakeApplicationProfileRepo:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row
        self.last_upsert_fields: dict[str, Any] | None = None

    def get(self, user_id: int) -> dict[str, Any] | None:
        assert user_id == _FAKE_USER_ID
        return self.row

    def upsert(self, user_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        assert user_id == _FAKE_USER_ID
        # ai: claude-sonnet-5 | 2026-10-14 -- a snapshot independent of self.row,
        # which the route mutates in place after this returns (see
        # _with_decoded_previous_employers) and would otherwise make this look
        # like it never received the raw JSON-encoded string.
        self.last_upsert_fields = dict(fields)
        self.row = {**(self.row or {"id": 1}), **fields}
        return self.row


class FakeResumeRepo:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row

    def get_latest(self, user_id: int) -> dict[str, Any] | None:
        assert user_id == _FAKE_USER_ID
        return self.row


class FakeApplicationAnswerRepo:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self._next_id = len(self.rows) + 1

    def get_all(self, user_id: int) -> list[dict[str, Any]]:
        assert user_id == _FAKE_USER_ID
        return list(self.rows)

    def upsert(
        self, user_id: int, *, question_text: str, answer_text: str, platform: str | None = None
    ) -> dict[str, Any]:
        assert user_id == _FAKE_USER_ID
        row = {
            "id": self._next_id,
            "question_text": question_text,
            "answer_text": answer_text,
            "platform": platform,
        }
        self._next_id += 1
        self.rows.append(row)
        return row

    def delete(self, user_id: int, answer_id: int) -> None:
        assert user_id == _FAKE_USER_ID
        self.rows = [r for r in self.rows if r["id"] != answer_id]


def _make_app(repos: dict, monkeypatch: pytest.MonkeyPatch) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(application_profile_bp, url_prefix="/api")
    monkeypatch.setattr("routes.application_profile.get_repos", make_fake_get_repos(repos))
    monkeypatch.setattr(
        "routes.application_profile.current_user_id", lambda: _FAKE_USER_ID
    )
    return app


def test_get_application_profile_returns_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "application_profile": FakeApplicationProfileRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.get("/api/application-profile")
    assert response.status_code == 200
    assert response.get_json() is None


def test_update_application_profile_upserts_and_returns_row(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "application_profile": FakeApplicationProfileRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.put(
        "/api/application-profile",
        json={"legal_name": "Evan Ross", "work_authorized": True, "requires_visa_sponsorship": False},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["legal_name"] == "Evan Ross"
    assert data["work_authorized"] is True
    assert data["requires_visa_sponsorship"] is False


def test_update_application_profile_partial_update_preserves_other_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: a PUT sending only one field must not null out the rest
    of the profile. Confirmed live — model_dump() without exclude_unset=True
    fills in every field's default (None), which the repo then writes as an
    explicit overwrite, wiping a real saved profile down to just the one
    field sent."""
    repos = {"conn": FakeConnection(), "application_profile": FakeApplicationProfileRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    client.put(
        "/api/application-profile",
        json={
            "legal_name": "Evan Ross",
            "email": "evan@eross.com",
            "city": "Mebane, NC",
            "work_authorized": True,
            "requires_visa_sponsorship": False,
        },
    )

    response = client.put(
        "/api/application-profile",
        json={"current_company": "ZombieTech LLC, Self-employed"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["current_company"] == "ZombieTech LLC, Self-employed"
    assert data["legal_name"] == "Evan Ross"
    assert data["email"] == "evan@eross.com"
    assert data["city"] == "Mebane, NC"
    assert data["work_authorized"] is True
    assert data["requires_visa_sponsorship"] is False


def test_update_application_profile_stores_previous_employers_as_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """previous_employers is stored as a JSON-encoded TEXT column (same
    convention as jobs.dismissed_duplicate_ids) -- the route must encode on
    the way in and decode on the way out so the frontend only ever sees a
    real array, never the raw stored string."""
    repos = {"conn": FakeConnection(), "application_profile": FakeApplicationProfileRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.put(
        "/api/application-profile",
        json={"previous_employers": ["Acme", "Widget Co"]},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["previous_employers"] == ["Acme", "Widget Co"]
    # the repo (standing in for the DB) only ever sees the JSON-encoded string
    assert repos["application_profile"].last_upsert_fields["previous_employers"] == '["Acme", "Widget Co"]'

    get_response = client.get("/api/application-profile")
    assert get_response.get_json()["previous_employers"] == ["Acme", "Widget Co"]


def test_update_application_profile_dedupes_previous_employers_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = {"conn": FakeConnection(), "application_profile": FakeApplicationProfileRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.put(
        "/api/application-profile",
        json={"previous_employers": [" Acme ", "acme", "", "Widget Co"]},
    )
    assert response.status_code == 200
    assert response.get_json()["previous_employers"] == ["Acme", "Widget Co"]


def test_scan_resume_requires_a_resume_on_file(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "resume": FakeResumeRepo(None)}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.post("/api/application-profile/scan-resume")
    assert response.status_code == 409


def test_scan_resume_returns_employers(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "resume": FakeResumeRepo({"content": "Worked at Acme Corp"})}
    client = _make_app(repos, monkeypatch).test_client()
    monkeypatch.setattr(
        "routes.application_profile.scan_resume_for_previous_employers",
        lambda user_id, resume_text: (["Acme Corp"], None),
    )

    response = client.post("/api/application-profile/scan-resume")
    assert response.status_code == 200
    assert response.get_json()["employers"] == ["Acme Corp"]


def test_scan_resume_surfaces_ai_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "resume": FakeResumeRepo({"content": "Worked at Acme Corp"})}
    client = _make_app(repos, monkeypatch).test_client()
    monkeypatch.setattr(
        "routes.application_profile.scan_resume_for_previous_employers",
        lambda user_id, resume_text: ([], "ANTHROPIC_API_KEY is not set"),
    )

    response = client.post("/api/application-profile/scan-resume")
    assert response.status_code == 502


def test_update_application_profile_rejects_invalid_body(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "application_profile": FakeApplicationProfileRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.put("/api/application-profile", json={"work_authorized": "not-a-bool"})
    assert response.status_code == 400


def test_list_application_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {
        "conn": FakeConnection(),
        "application_answers": FakeApplicationAnswerRepo(
            [{"id": 1, "question_text": "Security clearance?", "answer_text": "No", "platform": None}]
        ),
    }
    client = _make_app(repos, monkeypatch).test_client()

    response = client.get("/api/application-answers")
    assert response.status_code == 200
    assert response.get_json()[0]["question_text"] == "Security clearance?"


def test_upsert_application_answer_requires_both_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "application_answers": FakeApplicationAnswerRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.post("/api/application-answers", json={"question_text": "Only a question?"})
    assert response.status_code == 400


def test_upsert_application_answer_success(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "application_answers": FakeApplicationAnswerRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.post(
        "/api/application-answers",
        json={"question_text": "Do you need sponsorship?", "answer_text": "No", "platform": "Ashby"},
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data["question_text"] == "Do you need sponsorship?"
    assert data["platform"] == "Ashby"


def test_delete_application_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {
        "conn": FakeConnection(),
        "application_answers": FakeApplicationAnswerRepo([{"id": 5, "question_text": "x", "answer_text": "y", "platform": None}]),
    }
    client = _make_app(repos, monkeypatch).test_client()

    response = client.delete("/api/application-answers/5")
    assert response.status_code == 200
    assert repos["application_answers"].get_all(_FAKE_USER_ID) == []


# ai: claude-opus-5-5 | 2026-11-17
# Editing in place, merging and removing aliases: HTTP mapping against a repo that
# raises the real repository errors, then the same routes against the real tables.


class _RaisingAnswerRepo:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def update(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self.error

    def merge(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self.error

    def delete_alias(self, *args: Any, **kwargs: Any) -> None:
        raise self.error


_ANSWER_BODY = {"question_text": "Clearance?", "answer_text": "None"}


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [(AnswerNotFoundError(1), 404), (QuestionTakenError("Clearance?"), 409)],
)
def test_update_application_answer_maps_repository_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected_status: int
) -> None:
    repos = {"conn": FakeConnection(), "application_answers": _RaisingAnswerRepo(error)}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.put("/api/application-answers/1", json=_ANSWER_BODY)
    assert response.status_code == expected_status


def test_update_application_answer_requires_both_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "application_answers": FakeApplicationAnswerRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.put("/api/application-answers/1", json={"question_text": "Q?", "answer_text": "  "})
    assert response.status_code == 400


@pytest.mark.parametrize(
    "body",
    [
        {"keep_id": 1, "merge_ids": []},
        {"keep_id": 1, "merge_ids": [1]},
        {"merge_ids": [2]},
        {"keep_id": "one", "merge_ids": [2]},
    ],
)
def test_merge_application_answers_rejects_invalid_bodies(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    repos = {"conn": FakeConnection(), "application_answers": FakeApplicationAnswerRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.post("/api/application-answers/merge", json=body)
    assert response.status_code == 400


def test_merge_and_alias_delete_return_404_for_missing_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "application_answers": _RaisingAnswerRepo(AnswerNotFoundError(9))}
    client = _make_app(repos, monkeypatch).test_client()

    assert client.post("/api/application-answers/merge", json={"keep_id": 1, "merge_ids": [9]}).status_code == 404
    assert client.delete("/api/application-answers/aliases/9").status_code == 404


def test_answer_routes_against_the_real_tables(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Merge, edit in place, alias removal and listing through the real repository."""
    conn = get_connection(pg_dsn)
    try:
        account = PostgresUserAccountRepository(conn).create_pending(
            "owner@example.com", "seed-owner@example.com", "2099-01-01"
        )
        conn.commit()
    finally:
        conn.close()
    user_id = account["id"]
    app = Flask(__name__)
    app.register_blueprint(application_profile_bp, url_prefix="/api")
    monkeypatch.setattr("routes.application_profile.get_repos", lambda: get_repos(user_id))
    monkeypatch.setattr("routes.application_profile.current_user_id", lambda: user_id)
    client = app.test_client()

    keep = client.post("/api/application-answers", json={"question_text": "Sponsorship?", "answer_text": "No"}).get_json()
    other = client.post("/api/application-answers", json={"question_text": "Visa?", "answer_text": "no"}).get_json()

    merged = client.post(
        "/api/application-answers/merge",
        json={"keep_id": keep["id"], "merge_ids": [other["id"]], "answer_text": " No, never "},
    )
    assert merged.status_code == 200
    assert merged.get_json()["answer_text"] == "No, never"
    [alias] = merged.get_json()["aliases"]
    assert alias["question_text"] == "Visa?"

    edited = client.put(
        f"/api/application-answers/{keep['id']}",
        json={"question_text": "Need sponsorship?", "answer_text": "No", "platform": "Ashby"},
    )
    assert edited.status_code == 200
    assert edited.get_json()["question_text"] == "Need sponsorship?"

    taken = client.post("/api/application-answers", json={"question_text": "Clearance?", "answer_text": "None"}).get_json()
    conflict = client.put(
        f"/api/application-answers/{taken['id']}", json={"question_text": "visa?", "answer_text": "x"}
    )
    assert conflict.status_code == 409

    assert client.delete(f"/api/application-answers/aliases/{alias['id']}").status_code == 200
    listed = {row["id"]: row for row in client.get("/api/application-answers").get_json()}
    assert set(listed) == {keep["id"], taken["id"]}
    assert listed[keep["id"]]["aliases"] == []
    assert listed[keep["id"]]["use_count"] == 0
