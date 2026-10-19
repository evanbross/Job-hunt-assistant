# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

import json

import pytest

import backend.resume_employer_scan_service as resume_employer_scan_service
from backend.ai_provider import AIProviderError
from backend.resume_employer_scan_service import scan_resume_for_previous_employers

_USER_ID = 1


class _FakeAIClient:
    response: str = "[]"
    calls: list[str] = []

    def __init__(self, user_id: int) -> None:
        pass

    def complete(self, *, messages, max_tokens, json_response) -> str:
        _FakeAIClient.calls.append(messages[0].content)
        return _FakeAIClient.response


def test_returns_employers_from_ai_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAIClient.response = json.dumps(["Acme Corp", "Widget Co"])
    _FakeAIClient.calls = []
    monkeypatch.setattr(resume_employer_scan_service, "AIProviderClient", _FakeAIClient)

    employers, error = scan_resume_for_previous_employers(_USER_ID, "Worked at Acme Corp 2020-2023...")

    assert employers == ["Acme Corp", "Widget Co"]
    assert error is None
    assert "Acme Corp" in _FakeAIClient.calls[0]


def test_dedupes_case_insensitively(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAIClient.response = json.dumps(["Acme Corp", "acme corp", "Widget Co"])
    monkeypatch.setattr(resume_employer_scan_service, "AIProviderClient", _FakeAIClient)

    employers, error = scan_resume_for_previous_employers(_USER_ID, "resume text")

    assert employers == ["Acme Corp", "Widget Co"]
    assert error is None


def test_strips_code_fence_wrapped_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAIClient.response = '```json\n["Acme Corp"]\n```'
    monkeypatch.setattr(resume_employer_scan_service, "AIProviderClient", _FakeAIClient)

    employers, error = scan_resume_for_previous_employers(_USER_ID, "resume text")

    assert employers == ["Acme Corp"]
    assert error is None


def test_empty_resume_text_short_circuits_without_calling_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAIClient.calls = []
    monkeypatch.setattr(resume_employer_scan_service, "AIProviderClient", _FakeAIClient)

    employers, error = scan_resume_for_previous_employers(_USER_ID, "   ")

    assert employers == []
    assert error is None
    assert _FakeAIClient.calls == []


def test_ai_failure_is_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenAIClient:
        def __init__(self, user_id: int) -> None:
            pass

        def complete(self, **kwargs) -> str:
            raise AIProviderError("ANTHROPIC_API_KEY is not set")

    monkeypatch.setattr(resume_employer_scan_service, "AIProviderClient", _BrokenAIClient)

    employers, error = scan_resume_for_previous_employers(_USER_ID, "resume text")

    assert employers == []
    assert error == "ANTHROPIC_API_KEY is not set"


def test_invalid_json_response_is_surfaced_as_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAIClient.response = "not json and no brackets either"
    monkeypatch.setattr(resume_employer_scan_service, "AIProviderClient", _FakeAIClient)

    employers, error = scan_resume_for_previous_employers(_USER_ID, "resume text")

    assert employers == []
    assert error is not None


def test_non_list_json_response_is_surfaced_as_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAIClient.response = json.dumps({"companies": ["Acme Corp"]})
    monkeypatch.setattr(resume_employer_scan_service, "AIProviderClient", _FakeAIClient)

    employers, error = scan_resume_for_previous_employers(_USER_ID, "resume text")

    assert employers == []
    assert error is not None
