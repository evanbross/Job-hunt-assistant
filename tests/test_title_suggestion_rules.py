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

import pytest

from backend.contacts.title_suggestion_rules import classify_title_tier1

# ai: claude-sonnet-5 | 2026-09-28


@pytest.mark.parametrize(
    "title",
    [
        "technical recruiter",
        "senior recruiter",
        "talent acquisition specialist",
        "sourcer",
        "technical sourcing lead",
        "recruiting coordinator",
    ],
)
def test_flags_high_confidence_titles(title: str) -> None:
    assert classify_title_tier1(title) == "flag"


@pytest.mark.parametrize(
    "title",
    [
        "talent partner",
        "head of talent",
        "people operations",
        "hr business partner",
        "hiring manager",
    ],
)
def test_skips_ambiguous_titles(title: str) -> None:
    assert classify_title_tier1(title) == "skip"


@pytest.mark.parametrize(
    "title",
    [
        "former recruiter",
        "ex-recruiter",
        "ex recruiter",
        "former talent acquisition lead",
    ],
)
def test_negative_patterns_are_never_flagged(title: str) -> None:
    assert classify_title_tier1(title) is None


@pytest.mark.parametrize(
    "title",
    ["software engineer", "product manager", "", "account executive"],
)
def test_unrelated_titles_have_no_opinion(title: str) -> None:
    assert classify_title_tier1(title) is None
