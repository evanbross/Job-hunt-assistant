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

# ai: claude-fable-5 | 2026-07-13
"""Regression tests: the pitch/chat routes must include the stored job
description and the uploaded resume in the prompts sent to the AI provider."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from flask import Flask

import config
import routes.cover_letter as cover_letter_module
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


class FakeJobsRepo:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def get_by_id(self, job_id: int) -> dict[str, Any] | None:
        return dict(self.row) if job_id == self.row["id"] else None


class FakeResumeRepo:
    def __init__(self, content: str | None = RESUME_TEXT) -> None:
        self._content = content

    def get_latest(self) -> dict[str, Any] | None:
        if self._content is None:
            return None
        return {"id": 1, "filename": "resume.pdf", "content": self._content}


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
        "raw_email_snippet": EMAIL_SNIPPET,
        "fit_reasoning": "Strong infra background.",
        "url": "https://example.com/job/7",
        "status": "triage",
    }
    repos = {
        "jobs": FakeJobsRepo(job_row),
        "resume": FakeResumeRepo(),
        "conn": FakeConnection(),
    }
    monkeypatch.setattr(cover_letter_module, "get_repos", make_fake_get_repos(repos))
    monkeypatch.setattr(cover_letter_module, "AIProviderClient", FakeAIClient)
    monkeypatch.setattr(config, "load_candidate_profile", lambda: PROFILE_TEXT)
    FakeAIClient.captured = {}

    app = Flask(__name__)
    app.register_blueprint(cover_letter_bp, url_prefix="/api")
    return app.test_client()


def test_cover_letter_chat_prompt_includes_job_description_and_resume(client: Any) -> None:
    res = client.post(
        "/api/jobs/7/cover-letter/chat",
        json={"messages": [{"role": "user", "content": "Mention my platform work."}],
              "cover_letter": "Current draft."},
    )
    assert res.status_code == 200
    system = FakeAIClient.captured["system"]
    assert JOB_DESCRIPTION in system
    assert RESUME_TEXT in system
    assert PROFILE_TEXT in system


def test_pitch_chat_prompt_includes_job_description_and_resume(client: Any) -> None:
    res = client.post(
        "/api/jobs/7/pitch/chat",
        json={"type": "why_fit",
              "messages": [{"role": "user", "content": "Make it more specific."}],
              "current_text": "Current pitch."},
    )
    assert res.status_code == 200
    system = FakeAIClient.captured["system"]
    assert JOB_DESCRIPTION in system
    assert RESUME_TEXT in system


def test_resume_tailor_chat_prompt_includes_job_description_and_resume(client: Any) -> None:
    res = client.post(
        "/api/jobs/7/resume-tailor/chat",
        json={"messages": [{"role": "user", "content": "Tighten the bullets."}],
              "analysis": "Current analysis."},
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
        {"job_description": "", "raw_email_snippet": EMAIL_SNIPPET}, None
    )
    assert EMAIL_SNIPPET in job_block
    assert resume_block == ""

    job_block, resume_block = job_and_resume_prompt_blocks({}, {"content": RESUME_TEXT})
    assert job_block == ""
    assert RESUME_TEXT in resume_block
