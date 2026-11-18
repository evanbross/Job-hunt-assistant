# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-17
"""backend/application_answer_suggestions.py: the AI fallback for
application questions autofill couldn't fill. The provider is faked; the
profile, saved answers, candidate profile and on/off setting are read from
the real schema."""

from __future__ import annotations

import json
from typing import Any

import pytest
from flask import Flask

import backend.application_answer_suggestions as suggestions_module
import config
from backend.application_answer_suggestions import (
    AnswerSuggestion,
    FormQuestion,
    parse_suggestions,
    suggest_application_answers,
)
from backend.db import PostgresUserAccountRepository, get_connection
from backend.db.row_level_security import set_current_user
from backend.settings_service import set_setting
from backend.utils.utils import get_repos
from routes.settings import settings_bp
from tests.db_roles import runtime_database_url


class FakeAIClient:
    """Stands in for AIProviderClient: records each prompt, returns `reply`."""

    reply = '{"answers": []}'
    calls: list[dict[str, Any]] = []

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id

    def __enter__(self) -> FakeAIClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def complete(self, **kwargs: Any) -> str:
        FakeAIClient.calls.append({"user_id": self.user_id, **kwargs})
        return FakeAIClient.reply


@pytest.fixture
def fake_ai(monkeypatch: pytest.MonkeyPatch) -> type[FakeAIClient]:
    FakeAIClient.calls = []
    FakeAIClient.reply = '{"answers": []}'
    monkeypatch.setattr(suggestions_module, "AIProviderClient", FakeAIClient)
    return FakeAIClient


@pytest.fixture
def user_id(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> int:
    # The app's own (row-level-security) role, as in production.
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        account = PostgresUserAccountRepository(conn).create_pending(
            "owner@example.com", "seed-owner@example.com", "2099-01-01"
        )
        conn.commit()
        set_current_user(conn.cursor(), account["id"])
        conn.commit()
        return int(account["id"])
    finally:
        conn.close()


def _seed(user_id: int) -> None:
    with get_repos(user_id) as repos:
        repos["application_profile"].upsert(
            user_id,
            {"city": "Mebane", "work_authorized": True, "gender": "Male", "race": "Prefer not to say"},
        )
        repos["application_answers"].upsert(
            user_id, question_text="What is your notice period?", answer_text="2 weeks"
        )
        repos["candidate_profiles"].upsert(user_id, "Senior backend engineer, 10 years of Python.", "2026-11-17")
        repos["conn"].commit()


def _prompt(call: dict[str, Any]) -> str:
    return call["messages"][0].content


def test_suggests_answers_from_the_persons_own_facts(user_id: int, fake_ai: type[FakeAIClient]) -> None:
    _seed(user_id)
    fake_ai.reply = json.dumps(
        {
            "answers": [
                {"id": "q1", "answer": "10"},
                {"id": "q2", "answer": "yes"},
                {"id": "q3", "answer": None},
            ]
        }
    )
    questions = [
        FormQuestion(id="q1", question="Years of Python experience?"),
        FormQuestion(id="q2", question="Can you start within a month?", options=("Yes", "No")),
        FormQuestion(id="q3", question="Why do you want to work here?"),
    ]

    result = suggest_application_answers(user_id, questions, "Acme - Backend Engineer")

    assert result.disabled is False
    assert result.suggestions == [AnswerSuggestion("q1", "10"), AnswerSuggestion("q2", "Yes")]
    [call] = fake_ai.calls
    prompt = _prompt(call)
    assert call["user_id"] == user_id
    assert "10 years of Python" in prompt
    assert "What is your notice period?" in prompt
    assert '"city": "Mebane"' in prompt
    assert "Acme - Backend Engineer" in prompt
    # Demographic profile fields never reach the model.
    assert "Male" not in prompt and "Prefer not to say" not in prompt


def test_never_asks_about_voluntary_or_consent_questions(user_id: int, fake_ai: type[FakeAIClient]) -> None:
    questions = [
        FormQuestion(id="q1", question="What is your gender?", options=("Male", "Female")),
        FormQuestion(id="q2", question="Are you a protected veteran?"),
        FormQuestion(id="q3", question="I certify that the information above is true"),
        FormQuestion(id="q4", question="Signature"),
        FormQuestion(id="q5", question="Years of Python experience?"),
    ]

    suggest_application_answers(user_id, questions, None)

    prompt = _prompt(fake_ai.calls[0])
    assert "Years of Python experience?" in prompt
    for excluded in ("gender", "veteran", "certify", "Signature"):
        assert excluded not in prompt.split("Questions to answer:")[1]


def test_only_sensitive_questions_makes_no_ai_call(user_id: int, fake_ai: type[FakeAIClient]) -> None:
    result = suggest_application_answers(user_id, [FormQuestion(id="q1", question="Disability status")], None)

    assert result.suggestions == []
    assert fake_ai.calls == []


def test_turned_off_setting_makes_no_ai_call(user_id: int, fake_ai: type[FakeAIClient]) -> None:
    with get_repos(user_id) as repos:
        set_setting("autofill_ai_suggestions", "false", user_id, repos)
        repos["conn"].commit()

    result = suggest_application_answers(user_id, [FormQuestion(id="q1", question="Years of Python?")], None)

    assert result.disabled is True
    assert fake_ai.calls == []


def test_settings_route_switch_turns_suggestions_off(
    user_id: int, fake_ai: type[FakeAIClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Application Profile tab's switch: PUT/GET /api/config with the
    generic string setting, read back by the suggestion service."""
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(settings_bp, url_prefix="/api")
    monkeypatch.setattr("routes.settings.current_user_id", lambda: user_id)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user_id

    assert client.get("/api/config").get_json()["autofill_ai_suggestions"] == "true"
    assert client.put("/api/config", json={"autofill_ai_suggestions": "false"}).status_code == 200
    assert client.get("/api/config").get_json()["autofill_ai_suggestions"] == "false"

    result = suggest_application_answers(user_id, [FormQuestion(id="q1", question="Years of Python?")], None)
    assert result.disabled is True
    assert fake_ai.calls == []


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        # A choice answer comes back in the option's own spelling.
        ('{"answers": [{"id": "q1", "answer": "  yes, ACTIVE "}]}', [AnswerSuggestion("q1", "Yes, active")]),
        # Not one of the options: dropped rather than typed into a dropdown.
        ('{"answers": [{"id": "q1", "answer": "Maybe"}]}', []),
        # Select-all-that-apply.
        ('{"answers": [{"id": "q1", "answer": "No; yes, active"}]}', [AnswerSuggestion("q1", "No; Yes, active")]),
        # Unknown id, null, blank and duplicate answers are all dropped.
        (
            '{"answers": [{"id": "zz", "answer": "x"}, {"id": "q2", "answer": null},'
            ' {"id": "q2", "answer": " "}, {"id": "q2", "answer": "4 weeks"}, {"id": "q2", "answer": "5"}]}',
            [AnswerSuggestion("q2", "4 weeks")],
        ),
        # Prose around the JSON is tolerated.
        ('Sure!\n{"answers": [{"id": "q2", "answer": "4 weeks"}]}', [AnswerSuggestion("q2", "4 weeks")]),
    ],
)
def test_parse_suggestions(reply: str, expected: list[AnswerSuggestion]) -> None:
    questions = [
        FormQuestion(id="q1", question="Clearance?", options=("Yes, active", "No")),
        FormQuestion(id="q2", question="Notice period?"),
    ]
    assert parse_suggestions(reply, questions) == expected


def test_parse_suggestions_rejects_a_reply_without_answers() -> None:
    with pytest.raises(ValueError):
        parse_suggestions('{"result": "ok"}', [FormQuestion(id="q1", question="Q?")])
