# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-10-13
"""unenriched_skip_reason: when a re-captured existing job is a real gap.

The 2026-10-13 LinkedIn listener rescan logged "captured only N chars"
warnings for 22 jobs that already had full (8000+ char) descriptions stored —
a search-page rescan only ever yields short cards, so a short capture alone
is not a problem worth a warning.
"""

from __future__ import annotations

from typing import Any

import pytest

import backend.scoring.jd_enrichment as jd_enrichment
from backend.scoring.jd_enrichment import (
    MIN_REFRESHED_TEXT_CHARS,
    fetch_jd_from_url,
    is_richer_description,
    unenriched_skip_reason,
)


def test_short_capture_of_fully_described_job_is_not_a_gap() -> None:
    existing_job = {"job_description": "x" * 8000}

    reason, gap = unenriched_skip_reason(226, existing_job)

    assert gap is False
    assert "full description (8000 chars)" in reason


def test_short_capture_of_thin_job_is_a_gap() -> None:
    """Job 6307's shape: only a 231-char snippet stored, nothing richer found."""
    existing_job = {"job_description": None, "raw_jd_snippet": "y" * 231}

    reason, gap = unenriched_skip_reason(231, existing_job)

    assert gap is True
    assert "too short" in reason


def test_reported_threshold_actually_triggers_enrichment() -> None:
    """The "need N chars" hint must name a length is_richer_description accepts."""
    existing_job = {"raw_jd_snippet": "y" * 250}

    reason, gap = unenriched_skip_reason(400, existing_job)
    needed = int(reason.split("need ")[1].split(" chars")[0])

    assert gap is True
    assert is_richer_description("z" * needed, existing_job)
    assert not is_richer_description("z" * (needed - 1), existing_job)


# ai: claude-sonnet-5 | 2026-11-25
# fetch_jd_from_url: shared by refresh_job_description_from_url (the "Reload
# from URL" button) and the email scan/rescan routes.


class _FakeScanner:
    def __init__(self, text: str = "", error: Exception | None = None) -> None:
        self._text = text
        self._error = error

    def scan_single_job_url(self, source: str, url: str) -> dict[str, Any]:
        if self._error is not None:
            raise self._error
        return {"job_text": self._text}


def test_fetch_jd_from_url_returns_text_when_long_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "A real job description. " * 10
    assert len(text) >= MIN_REFRESHED_TEXT_CHARS
    monkeypatch.setattr(jd_enrichment, "CareerSiteScanner", lambda: _FakeScanner(text=text))

    assert fetch_jd_from_url("Greenhouse", "https://example.test/jobs/1") == text


def test_fetch_jd_from_url_returns_empty_when_too_short(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jd_enrichment, "CareerSiteScanner", lambda: _FakeScanner(text="too short"))

    assert fetch_jd_from_url("Greenhouse", "https://example.test/jobs/1") == ""


def test_fetch_jd_from_url_returns_empty_on_fetch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        jd_enrichment, "CareerSiteScanner", lambda: _FakeScanner(error=OSError("blocked"))
    )

    assert fetch_jd_from_url("Greenhouse", "https://example.test/jobs/1") == ""
