# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""The fit scorer's citizenship-requirement bonus: the model classifies the
posting's requirement (any wording), and FitScorer adds the configured points
when the candidate's own status meets it."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

import config
from backend.db import (
    PostgresCandidateProfileRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.repositories.application_profile import (
    PostgresApplicationProfileRepository,
)
from backend.db.row_level_security import set_current_user
from backend.scoring.fit_scorer import (
    CITIZENSHIP_BONUS_SETTING,
    FitScorer,
    ScoreResult,
    ScorerUserContext,
)
from backend.scoring.us_citizenship import UsCitizenship, meets_requirement
from backend.settings_service import set_setting
from backend.utils.utils import get_repos
from routes.settings import settings_bp
from tests.db_roles import runtime_database_url
from tests.login_session import sign_in

NONE = UsCitizenship.NONE
PERSON = UsCitizenship.US_PERSON
CITIZEN = UsCitizenship.US_CITIZEN
BY_BIRTH = UsCitizenship.CITIZEN_BY_BIRTH


# ai: claude-opus-5-5 | 2026-11-20
@pytest.mark.parametrize(
    ("status", "required", "expected"),
    [
        (BY_BIRTH, PERSON, True),
        (BY_BIRTH, CITIZEN, True),
        (BY_BIRTH, BY_BIRTH, True),
        (CITIZEN, PERSON, True),
        (CITIZEN, CITIZEN, True),
        (CITIZEN, BY_BIRTH, False),  # naturalized citizens excluded
        (PERSON, PERSON, True),
        (PERSON, CITIZEN, False),  # a permanent resident is a US person, not a citizen
        (NONE, PERSON, False),
        (BY_BIRTH, NONE, False),  # no requirement stated: nothing to reward
        (None, PERSON, False),  # status unset: never assume
        (BY_BIRTH, None, False),  # model gave no usable value
    ],
)
def test_meets_requirement(
    status: UsCitizenship | None, required: UsCitizenship | None, expected: bool
) -> None:
    assert meets_requirement(status, required) is expected


# ai: claude-opus-5-5 | 2026-11-20
def test_parse_rejects_values_the_model_made_up() -> None:
    assert UsCitizenship.parse(" US_Citizen ") is UsCitizenship.US_CITIZEN
    assert UsCitizenship.parse("citizen") is None
    assert UsCitizenship.parse(None) is None


def _context(status: UsCitizenship | None = BY_BIRTH, points: int = 15) -> ScorerUserContext:
    return ScorerUserContext(
        timezone="", candidate_profile="", us_citizenship=status, citizenship_bonus_points=points
    )


def _score(
    context: ScorerUserContext,
    ai_response: dict[str, Any],
    penalty_rules: list[dict[str, Any]] | None = None,
) -> ScoreResult:
    client = MagicMock()
    client.complete.return_value = json.dumps(ai_response)
    with (
        patch("backend.scoring.fit_scorer.AIProviderClient", return_value=client),
        patch.object(ScorerUserContext, "load", return_value=context),
        patch.object(FitScorer, "_build_system_prompt", return_value="system prompt"),
    ):
        return FitScorer(1).score(
            job_title="Senior Software Engineer",
            company="Acme Defense",
            email_body="Must be a U.S. person as defined by ITAR. On-site.",
            resume_text="",
            penalty_rules=penalty_rules or [],
        )


# ai: claude-opus-5-5 | 2026-11-20
def test_score_adds_the_bonus_and_itemizes_it_when_the_candidate_qualifies() -> None:
    result = _score(
        _context(BY_BIRTH, 15),
        {"score": 60, "reasoning": "Decent fit.", "citizenship_requirement": "us_person"},
    )

    assert result["score"] == 75
    assert result["reasoning"] == "Decent fit. Bonus applied: +15 for US-person requirement."


# ai: claude-opus-5-5 | 2026-11-20
def test_score_applies_bonus_and_penalties_together_and_clamps() -> None:
    result = _score(
        _context(CITIZEN, 15),
        {"score": 95, "reasoning": "Great fit.", "citizenship_requirement": "us_citizen"},
        penalty_rules=[{"rule_text": "on-site", "penalty_points": 5, "scope": "any"}],
    )

    assert result["score"] == 100  # 95 - 5 + 15, clamped
    assert result["reasoning"] == (
        "Great fit. Penalties applied: -5 for 'on-site'. "
        "Bonus applied: +15 for US-citizen requirement."
    )


# ai: claude-opus-5-5 | 2026-11-20
@pytest.mark.parametrize(
    ("context", "requirement"),
    [
        (_context(None, 15), "us_person"),
        (_context(PERSON, 15), "us_citizen"),
        (_context(BY_BIRTH, 0), "us_person"),
        (_context(BY_BIRTH, 15), "none"),
        (_context(BY_BIRTH, 15), "maybe"),
    ],
    ids=["status-unset", "not-met", "bonus-off", "no-requirement", "bad-value"],
)
def test_score_adds_no_bonus(context: ScorerUserContext, requirement: str) -> None:
    result = _score(
        context, {"score": 60, "reasoning": "Decent fit.", "citizenship_requirement": requirement}
    )

    assert result["score"] == 60
    assert result["reasoning"] == "Decent fit."


# ai: claude-opus-5-5 | 2026-11-20
def test_score_adds_no_bonus_when_the_model_omits_the_field() -> None:
    result = _score(_context(BY_BIRTH, 15), {"score": 60, "reasoning": "Decent fit."})

    assert result["score"] == 60


# ─── against the real schema ────────────────────────────────────────────────────


@pytest.fixture
def scored_user(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        user_id: int = PostgresUserAccountRepository(conn).create_pending(
            "owner@example.com", "seed-owner@example.com", "2099-01-01"
        )["id"]
        # The scorer refuses to run without a candidate profile.
        set_current_user(conn.cursor(), user_id)
        PostgresCandidateProfileRepository(conn).upsert(
            user_id, "Senior backend engineer.", "2026-01-01T00:00:00+00:00"
        )
        conn.commit()
    finally:
        conn.close()
    return user_id


def _save_status(pg_dsn: str, user_id: int, status: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        set_current_user(conn.cursor(), user_id)
        PostgresApplicationProfileRepository(conn).upsert(
            user_id, {"us_citizenship_status": status}
        )
        conn.commit()
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-20
def test_context_reads_status_from_the_profile_and_the_default_bonus(
    pg_dsn: str, scored_user: int
) -> None:
    _save_status(pg_dsn, scored_user, "citizen_by_birth")

    context = ScorerUserContext.load(scored_user)

    assert context.us_citizenship is UsCitizenship.CITIZEN_BY_BIRTH
    assert context.citizenship_bonus_points == 15


# ai: claude-opus-5-5 | 2026-11-20
def test_context_with_no_profile_row_has_no_status(scored_user: int) -> None:
    assert ScorerUserContext.load(scored_user).us_citizenship is None


# ai: claude-opus-5-5 | 2026-11-20
@pytest.mark.parametrize(
    ("stored", "expected"), [("20", 20), ("-5", 0), ("500", 100), ("abc", 15)]
)
def test_context_reads_and_bounds_the_bonus_setting(
    scored_user: int, stored: str, expected: int
) -> None:
    with get_repos(scored_user) as repos:
        set_setting(CITIZENSHIP_BONUS_SETTING, stored, scored_user, repos)
        repos["conn"].commit()

    assert ScorerUserContext.load(scored_user).citizenship_bonus_points == expected


# ai: claude-opus-5-5 | 2026-11-20
def test_prompt_asks_for_the_citizenship_requirement(scored_user: int) -> None:
    with patch("backend.scoring.fit_scorer.AIProviderClient", return_value=MagicMock()):
        scorer = FitScorer(scored_user)
        prompt = scorer._build_system_prompt(
            ScorerUserContext.load(scored_user), "resume", [], []
        )

    assert '"citizenship_requirement"' in prompt
    for level in UsCitizenship:
        assert f'"{level.value}"' in prompt


# ─── the Settings page's /api/config round trip ─────────────────────────────────


def _config_client(user_id: int, monkeypatch: pytest.MonkeyPatch) -> Any:
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(settings_bp, url_prefix="/api")
    monkeypatch.setattr("routes.settings.current_user_id", lambda: user_id)
    client = app.test_client()
    with client.session_transaction() as sess:
        sign_in(sess, user_id)
    return client


# ai: claude-opus-5-5 | 2026-11-20
def test_config_route_saves_the_bonus_the_scorer_reads(
    scored_user: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _config_client(scored_user, monkeypatch)
    assert client.get("/api/config").get_json()[CITIZENSHIP_BONUS_SETTING] == 15

    response = client.put("/api/config", json={CITIZENSHIP_BONUS_SETTING: 25})

    assert response.status_code == 200
    assert response.get_json()[CITIZENSHIP_BONUS_SETTING] == 25
    assert client.get("/api/config").get_json()[CITIZENSHIP_BONUS_SETTING] == 25
    assert ScorerUserContext.load(scored_user).citizenship_bonus_points == 25


# ai: claude-opus-5-5 | 2026-11-20
@pytest.mark.parametrize("value", [-1, 101, "abc", None])
def test_config_route_rejects_a_bad_bonus(value: object) -> None:
    app = Flask(__name__)
    app.register_blueprint(settings_bp, url_prefix="/api")

    response = app.test_client().put("/api/config", json={CITIZENSHIP_BONUS_SETTING: value})

    assert response.status_code == 400
