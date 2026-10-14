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

# ai: claude-opus-5-5 | 2026-10-13
"""unenriched_skip_reason: when a re-captured existing job is a real gap.

The 2026-10-13 LinkedIn listener rescan logged "captured only N chars"
warnings for 22 jobs that already had full (8000+ char) descriptions stored —
a search-page rescan only ever yields short cards, so a short capture alone
is not a problem worth a warning.
"""

from __future__ import annotations

from backend.scoring.jd_enrichment import is_richer_description, unenriched_skip_reason


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
