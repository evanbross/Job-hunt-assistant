# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""backend/leads/call_notes.py: reading uploaded notes files, and turning an AI
reply into a validated proposal (the provider itself is faked -- this tests the
parsing and validation of what it returns, not the provider)."""

from __future__ import annotations

import io
from datetime import date
from typing import Any

import docx
import pytest

from backend.ai_provider import AIProviderError
from backend.leads import call_notes
from backend.leads.call_notes import (
    CallNotesFormatError,
    CallNotesProposal,
    extract_call_notes,
    read_call_notes_upload,
)

_VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
<v Rita Recruiter>Hi, thanks for taking the call.</v>

2
00:00:04.500 --> 00:00:09.000
<v Evan>Happy to. What's the role?</v>
"""


# ai: claude-opus-5-5 | 2026-11-11
def test_vtt_captions_keep_speakers_and_drop_timings() -> None:
    assert read_call_notes_upload("call.vtt", _VTT.encode()) == (
        "Rita Recruiter: Hi, thanks for taking the call.\nEvan: Happy to. What's the role?"
    )


def test_docx_is_read_paragraph_by_paragraph() -> None:
    document = docx.Document()
    document.add_paragraph("Role: Staff Engineer")
    document.add_paragraph("Rate: 90/hr")
    buffer = io.BytesIO()
    document.save(buffer)
    assert read_call_notes_upload("Notes by Gemini.docx", buffer.getvalue()) == "Role: Staff Engineer\nRate: 90/hr"


def test_plain_text_is_trimmed_and_blank_runs_collapsed() -> None:
    assert read_call_notes_upload("n.md", b"\xef\xbb\xbf  a\r\n\r\n\r\n\r\nb  ") == "a\n\nb"


@pytest.mark.parametrize(
    ("filename", "raw", "message"),
    [
        ("notes.pdf", b"x", "Unsupported file type"),
        ("notes.txt", b"\xff\xfe\x00", "isn't UTF-8"),
        ("notes.txt", b"   \n  ", "no text"),
        ("notes.txt", b"x" * (call_notes.MAX_UPLOAD_BYTES + 1), "larger than"),
        ("notes.docx", b"not a zip", "Word document"),
    ],
    # Explicit ids: the oversized payload as an id makes tmp_path names too long on Windows.
    ids=["pdf", "not-utf8", "blank", "too-big", "bad-docx"],
)
def test_unreadable_uploads_are_refused(filename: str, raw: bytes, message: str) -> None:
    with pytest.raises(CallNotesFormatError, match=message):
        read_call_notes_upload(filename, raw)


def test_proposal_drops_unusable_dates_and_bounds_lists() -> None:
    proposal = CallNotesProposal.model_validate(
        {
            "job_title": "  ",
            "hiring_company": "Acme",
            "next_follow_up": "next Tuesday",
            "summary_bullets": ["a", "", *[str(i) for i in range(20)]],
            "next_steps": "not a list",
            "unexpected": "ignored",
        }
    )
    assert proposal.job_title is None
    assert proposal.hiring_company == "Acme"
    assert proposal.next_follow_up is None
    assert len(proposal.summary_bullets) == 12 and proposal.summary_bullets[0] == "a"
    assert proposal.next_steps == []
    assert CallNotesProposal.model_validate({"next_follow_up": "2026-11-20T10:00:00"}).next_follow_up == "2026-11-20"


class _FakeClient:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def __call__(self, user_id: int) -> "_FakeClient":
        return self

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def complete(self, *, messages: list[Any], **kwargs: Any) -> str:
        self.prompts.append(messages[0].content)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_extract_reads_a_fenced_reply_and_tells_the_model_today(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient('Here you go:\n```json\n{"job_title": "Staff Engineer", "next_follow_up": "2026-11-18"}\n```')
    monkeypatch.setattr(call_notes, "AIProviderClient", fake)

    proposal = extract_call_notes(1, "notes", {"role": None}, date(2026, 11, 11))

    assert (proposal.job_title, proposal.next_follow_up) == ("Staff Engineer", "2026-11-18")
    assert "Today is 2026-11-11" in fake.prompts[0]


@pytest.mark.parametrize("reply", ["no json here", "[1, 2]", '{"summary_bullets": 5, "location": {"x": 1}}'])
def test_extract_rejects_an_unusable_reply(monkeypatch: pytest.MonkeyPatch, reply: str) -> None:
    monkeypatch.setattr(call_notes, "AIProviderClient", _FakeClient(reply))
    with pytest.raises(ValueError):
        extract_call_notes(1, "notes", {}, date(2026, 11, 11))


def test_extract_passes_provider_errors_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(call_notes, "AIProviderClient", _FakeClient(AIProviderError("quota")))
    with pytest.raises(AIProviderError):
        extract_call_notes(1, "notes", {}, date(2026, 11, 11))
