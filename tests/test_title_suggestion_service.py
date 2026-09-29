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

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.ai_provider import AIProviderError
from backend.contacts import title_suggestion_service
from backend.contacts.title_suggestion_service import suggest_titles

# ai: claude-sonnet-5 | 2026-09-28

_USER_ID = 1


def _title_row(
    normalized_title: str,
    *,
    title: str | None = None,
    rule_exists: bool = False,
    unflagged_count: int = 1,
) -> dict[str, Any]:
    return {
        "normalized_title": normalized_title,
        "title": title or normalized_title,
        "contact_count": unflagged_count,
        "flagged_count": 0,
        "unflagged_count": unflagged_count,
        "rule_exists": rule_exists,
        "rule_recruiter_type": None,
    }


class FakeTitleRuleRepo:
    def __init__(
        self,
        report: list[dict[str, Any]],
        agency_titles: set[str] | None = None,
        representative_companies: dict[str, str] | None = None,
    ) -> None:
        self._report = report
        self._agency_titles = agency_titles or set()
        self._representative_companies = representative_companies or {}

    def titles_report(self, user_id: int) -> list[dict[str, Any]]:
        return self._report

    def titles_with_agency_company(self, user_id: int, normalized_titles: list[str]) -> set[str]:
        return {t for t in normalized_titles if t in self._agency_titles}

    def representative_companies(self, user_id: int, normalized_titles: list[str]) -> dict[str, str]:
        return {t: self._representative_companies.get(t, "Acme") for t in normalized_titles}


def test_flags_high_confidence_titles_without_ai() -> None:
    repos = {"title_rules": FakeTitleRuleRepo([_title_row("technical recruiter")])}

    result = suggest_titles(_USER_ID, repos, use_ai=False)

    assert [s.normalized_title for s in result.suggestions] == ["technical recruiter"]
    assert result.suggestions[0].reason == "keyword match"
    assert result.ai_error is None


def test_ignores_titles_that_already_have_a_rule() -> None:
    repos = {"title_rules": FakeTitleRuleRepo([_title_row("recruiter", rule_exists=True)])}

    result = suggest_titles(_USER_ID, repos, use_ai=False)

    assert result.suggestions == []


def test_ignores_titles_with_no_undecided_contacts() -> None:
    repos = {"title_rules": FakeTitleRuleRepo([_title_row("recruiter", unflagged_count=0)])}

    result = suggest_titles(_USER_ID, repos, use_ai=False)

    assert result.suggestions == []


def test_promotes_ambiguous_title_with_an_agency_company() -> None:
    repos = {
        "title_rules": FakeTitleRuleRepo(
            [_title_row("talent partner")], agency_titles={"talent partner"}
        )
    }

    result = suggest_titles(_USER_ID, repos, use_ai=False)

    assert [s.normalized_title for s in result.suggestions] == ["talent partner"]
    assert "agency" in result.suggestions[0].reason


def test_does_not_suggest_ambiguous_title_without_an_agency_company() -> None:
    repos = {"title_rules": FakeTitleRuleRepo([_title_row("talent partner")], agency_titles=set())}

    result = suggest_titles(_USER_ID, repos, use_ai=False)

    assert result.suggestions == []


def test_unrelated_titles_are_not_suggested_without_ai() -> None:
    repos = {"title_rules": FakeTitleRuleRepo([_title_row("software engineer")])}

    result = suggest_titles(_USER_ID, repos, use_ai=False)

    assert result.suggestions == []


class _FakeAIClient:
    labels_by_call: list[str] = []
    calls: list[str] = []

    def __init__(self, user_id: int) -> None:
        pass

    def complete(self, *, messages, max_tokens, json_response) -> str:
        _FakeAIClient.calls.append(messages[0].content)
        labels = _FakeAIClient.labels_by_call.pop(0)
        return json.dumps([{"label": label} for label in labels])


def test_uses_ai_for_leftovers_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {
        "title_rules": FakeTitleRuleRepo(
            [_title_row("growth lead"), _title_row("account executive")]
        )
    }
    _FakeAIClient.labels_by_call = [["recruiter", "unsure"]]
    _FakeAIClient.calls = []
    monkeypatch.setattr(title_suggestion_service, "AIProviderClient", _FakeAIClient)

    result = suggest_titles(_USER_ID, repos, use_ai=True)

    assert [s.normalized_title for s in result.suggestions] == ["growth lead"]
    assert result.suggestions[0].reason == "AI suggested"
    assert result.ai_error is None


def test_does_not_call_ai_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"title_rules": FakeTitleRuleRepo([_title_row("growth lead")])}
    _FakeAIClient.calls = []
    monkeypatch.setattr(title_suggestion_service, "AIProviderClient", _FakeAIClient)

    result = suggest_titles(_USER_ID, repos, use_ai=False)

    assert result.suggestions == []
    assert _FakeAIClient.calls == []


def test_ai_failure_is_surfaced_but_tier1_suggestions_still_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = {
        "title_rules": FakeTitleRuleRepo(
            [_title_row("technical recruiter"), _title_row("growth lead")]
        )
    }

    class _BrokenAIClient:
        def __init__(self, user_id: int) -> None:
            pass

        def complete(self, **kwargs) -> str:
            raise AIProviderError("ANTHROPIC_API_KEY is not set")

    monkeypatch.setattr(title_suggestion_service, "AIProviderClient", _BrokenAIClient)

    result = suggest_titles(_USER_ID, repos, use_ai=True)

    assert [s.normalized_title for s in result.suggestions] == ["technical recruiter"]
    assert result.ai_error is not None
