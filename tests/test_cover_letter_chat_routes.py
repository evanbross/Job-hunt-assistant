# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-fable-5 | 2026-07-13
"""Regression tests: the pitch/chat routes must include the stored job
description and the uploaded resume in the prompts sent to the AI provider."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from flask import Flask

import routes.cover_letter as cover_letter_module
from backend.resume.resume_prompt import MAX_RESUME_PROMPT_CHARS, resume_prompt_text
from routes.cover_letter import cover_letter_bp, job_and_resume_prompt_blocks

JOB_DESCRIPTION = "Own the Kubernetes platform and mentor the SRE team."
EMAIL_SNIPPET = "Short email snippet about the role."
RESUME_TEXT = "Senior backend engineer with 12 years of Python experience."
PROFILE_TEXT = "CANDIDATE PROFILE SENTINEL"


class FakeConnection:
    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


_FAKE_USER_ID = 1


class FakeJobsRepo:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def get_by_id(self, user_id: int, job_id: int) -> dict[str, Any] | None:
        assert user_id == _FAKE_USER_ID
        return dict(self.row) if job_id == self.row["id"] else None


class FakeResumeRepo:
    def __init__(self, content: str | None = RESUME_TEXT) -> None:
        self._content = content

    def get_latest(self, user_id: int) -> dict[str, Any] | None:
        assert user_id == _FAKE_USER_ID
        if self._content is None:
            return None
        return {"id": 1, "filename": "resume.pdf", "content": self._content}


# ai: claude-sonnet-5 | 2026-08-11
class FakeCoverLetterPhraseRepo:
    def get_all(self, user_id: int) -> list[dict[str, Any]]:
        return []


# ai: claude-sonnet-5 | 2026-09-26 — the candidate profile is read from the
# user's own candidate_profiles row (get_candidate_profile), not a file path.
class FakeCandidateProfileRepo:
    def get(self, user_id: int) -> dict[str, Any] | None:
        assert user_id == _FAKE_USER_ID
        return {"content": PROFILE_TEXT, "updated_at": "2026-01-01T00:00:00+00:00"}


class FakeAIClient:
    """Captures the prompt kwargs the route sends to the AI provider."""

    captured: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def complete(self, **kwargs: Any) -> str:
        FakeAIClient.captured = kwargs
        return "generated reply"


def make_fake_get_repos(repos: dict[str, Any]) -> Any:
    @contextmanager
    def fake() -> Iterator[dict[str, Any]]:
        try:
            yield repos
        finally:
            repos["conn"].close()

    return fake


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    job_row = {
        "id": 7,
        "company": "Acme",
        "job_title": "Platform Engineer",
        "job_description": JOB_DESCRIPTION,
        "raw_jd_snippet": EMAIL_SNIPPET,
        "fit_reasoning": "Strong infra background.",
        "url": "https://example.com/job/7",
        "status": "triage",
    }
    repos = {
        "jobs": FakeJobsRepo(job_row),
        "resume": FakeResumeRepo(),
        "cover_letter_phrases": FakeCoverLetterPhraseRepo(),
        "candidate_profiles": FakeCandidateProfileRepo(),
        "conn": FakeConnection(),
    }
    monkeypatch.setattr(cover_letter_module, "get_repos", make_fake_get_repos(repos))
    monkeypatch.setattr(cover_letter_module, "AIProviderClient", FakeAIClient)
    monkeypatch.setattr(cover_letter_module, "current_user_id", lambda: _FAKE_USER_ID)
    FakeAIClient.captured = {}

    app = Flask(__name__)
    app.register_blueprint(cover_letter_bp, url_prefix="/api")
    return app.test_client()


def test_cover_letter_chat_prompt_includes_job_description_and_resume(
    client: Any,
) -> None:
    res = client.post(
        "/api/jobs/7/cover-letter/chat",
        json={
            "messages": [{"role": "user", "content": "Mention my platform work."}],
            "cover_letter": "Current draft.",
        },
    )
    assert res.status_code == 200
    system = FakeAIClient.captured["system"]
    assert JOB_DESCRIPTION in system
    assert RESUME_TEXT in system
    assert PROFILE_TEXT in system


def test_pitch_chat_prompt_includes_job_description_and_resume(client: Any) -> None:
    res = client.post(
        "/api/jobs/7/pitch/chat",
        json={
            "type": "why_fit",
            "messages": [{"role": "user", "content": "Make it more specific."}],
            "current_text": "Current pitch.",
        },
    )
    assert res.status_code == 200
    system = FakeAIClient.captured["system"]
    assert JOB_DESCRIPTION in system
    assert RESUME_TEXT in system


def test_resume_tailor_chat_prompt_includes_job_description_and_resume(
    client: Any,
) -> None:
    res = client.post(
        "/api/jobs/7/resume-tailor/chat",
        json={
            "messages": [{"role": "user", "content": "Tighten the bullets."}],
            "analysis": "Current analysis.",
        },
    )
    assert res.status_code == 200
    system = FakeAIClient.captured["system"]
    assert JOB_DESCRIPTION in system
    assert RESUME_TEXT in system


def test_generate_pitch_prefers_full_job_description_over_snippet(client: Any) -> None:
    res = client.post("/api/jobs/7/pitch", json={"type": "why_company"})
    assert res.status_code == 200
    user_prompt = FakeAIClient.captured["messages"][0].content
    assert JOB_DESCRIPTION in user_prompt
    assert EMAIL_SNIPPET not in user_prompt
    assert RESUME_TEXT in FakeAIClient.captured["system"]


def test_prompt_blocks_fall_back_to_snippet_and_omit_missing_sections() -> None:
    job_block, resume_block = job_and_resume_prompt_blocks(
        {"job_description": "", "raw_jd_snippet": EMAIL_SNIPPET}, None
    )
    assert EMAIL_SNIPPET in job_block
    assert resume_block == ""

    job_block, resume_block = job_and_resume_prompt_blocks({}, {"content": RESUME_TEXT})
    assert job_block == ""
    assert RESUME_TEXT in resume_block


# ai: claude-opus-5-5 | 2026-10-30
def test_prompt_blocks_keep_the_end_of_a_two_page_resume() -> None:
    """Regression: the pitch routes used to cut the resume at 4,000 characters,
    so the older roles at the end of a normal two-page resume never reached
    the why-fit / why-company prompts."""
    oldest_role = "Boston Technologies, Founding Engineer, FIX bridges to bank APIs"
    two_page_resume = "x" * 7_000 + "\n" + oldest_role
    _, resume_block = job_and_resume_prompt_blocks({}, {"content": two_page_resume})
    assert oldest_role in resume_block


# ai: claude-opus-5-5 | 2026-10-30
def test_resume_prompt_text_is_bounded_by_the_shared_limit() -> None:
    oversized = "  " + "y" * (MAX_RESUME_PROMPT_CHARS + 500) + "  "
    assert resume_prompt_text(oversized) == "y" * MAX_RESUME_PROMPT_CHARS
    assert resume_prompt_text(None) == ""
    assert resume_prompt_text("   ") == ""


# ai: claude-sonnet-5 | 2026-09-29
# Validation-error paths for the Pydantic conversion -- the happy paths above
# already cover prompt content, these cover the 2026-09-29 cleanup itself.


def test_cover_letter_chat_rejects_empty_messages(client: Any) -> None:
    res = client.post(
        "/api/jobs/7/cover-letter/chat", json={"messages": [], "cover_letter": "x"}
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "No messages provided"


# ai: claude-sonnet-5 | 2026-09-29
def test_cover_letter_chat_rejects_all_malformed_messages(client: Any) -> None:
    res = client.post(
        "/api/jobs/7/cover-letter/chat", json={"messages": ["invalid"], "cover_letter": "x"}
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "No valid messages provided"


def test_pitch_chat_rejects_all_malformed_messages(client: Any) -> None:
    res = client.post(
        "/api/jobs/7/pitch/chat", json={"type": "why_fit", "messages": ["invalid"]}
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "No valid messages provided"


def test_resume_tailor_chat_rejects_all_malformed_messages(client: Any) -> None:
    res = client.post(
        "/api/jobs/7/resume-tailor/chat", json={"analysis": "x", "messages": ["invalid"]}
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "No valid messages provided"


def test_pitch_chat_rejects_invalid_type(client: Any) -> None:
    res = client.post(
        "/api/jobs/7/pitch/chat",
        json={"type": "why_not", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert res.status_code == 400
    assert res.get_json()["error"] == "type must be 'why_company' or 'why_fit'"


def test_pitch_chat_rejects_missing_messages(client: Any) -> None:
    res = client.post("/api/jobs/7/pitch/chat", json={"type": "why_fit"})
    assert res.status_code == 400
    assert res.get_json()["error"] == "No messages provided"


def test_resume_tailor_chat_rejects_missing_messages(client: Any) -> None:
    res = client.post("/api/jobs/7/resume-tailor/chat", json={"analysis": "x"})
    assert res.status_code == 400
    assert res.get_json()["error"] == "No messages provided"


def test_generate_pitch_rejects_invalid_type(client: Any) -> None:
    res = client.post("/api/jobs/7/pitch", json={"type": "bogus"})
    assert res.status_code == 400
    assert res.get_json()["error"] == "type must be 'why_company' or 'why_fit'"


def test_generate_pitch_rejects_missing_type(client: Any) -> None:
    res = client.post("/api/jobs/7/pitch", json={})
    assert res.status_code == 400
    assert res.get_json()["error"] == "type must be 'why_company' or 'why_fit'"
