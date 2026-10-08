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

"""Tests for DuplicateChecker's title gates: two jobs at the same company are
not duplicates when their titles name different seniority levels or different
department/team qualifiers, even though rapidfuzz token_set_ratio scores the
shared remainder of the titles above the match threshold."""

from __future__ import annotations

import pytest

from backend.scoring.duplicate_checker import DuplicateChecker


def _match(title_a: str, title_b: str) -> bool:
    checker = DuplicateChecker()
    result = checker.check(
        job_title=title_a,
        company="Acme",
        sheet_jobs=[{"company": "Acme", "job_title": title_b}],
    )
    return result is not None


# ai: claude-fable-5 | 2026-07-13
@pytest.mark.parametrize(
    ("title_a", "title_b"),
    [
        # Different seniority rungs are different roles.
        ("Principal Software Engineer", "Staff Software Engineer"),
        ("Senior Staff Software Engineer", "Principal Software Engineer"),
        ("Staff Software Engineer", "Senior Staff Software Engineer"),
        ("Senior Software Engineer", "StaffSE"),
        ("Senior Staff Software Engineer, Data", "Staff SE Data"),
        # Same base title, different department/team qualifier.
        ("Staff Software Engineer, Backend", "Staff Software Engineer, Websites"),
        (
            "Staff Software Engineer, Backend",
            "Staff Software Engineer (Configuration Platform)",
        ),
        (
            "Staff Software Engineer - Application Infrastructure",
            "Staff Software Engineer, Backend",
        ),
        # Marriott's FLEX prefix marks a temporary/flexible position — a
        # different job than the unprefixed title.
        ("FLEX Senior Software Engineer", "Senior Software Engineer"),
        (
            "Senior Software Engineer- GraphQL Engineering",
            "FLEX Senior Software Engineer- GraphQL Engineering",
        ),
        # ai: claude-sonnet-5 | 2026-07-25
        # A numbered rung (Engineer 1/2/3, Engineer I/II/III) is a distinct
        # pre-Senior level — never equivalent to another number or to Senior.
        ("Software Engineer 2", "Software Engineer 3"),
        ("Software Engineer I", "Software Engineer II"),
        ("Software Engineer 2", "Senior Software Engineer"),
        ("Software Engineer 1", "Staff Software Engineer"),
        # ai: claude-sonnet-5 | 2026-10-08
        # "Lead" is its own rung, distinct from Principal/Staff/Senior (seen with
        # 8th Light's "Lead Software Engineer" being flagged as a duplicate of
        # their "Principal Software Engineer" on an 87% title score).
        ("Lead Software Engineer", "Principal Software Engineer"),
        ("Lead Software Engineer", "Staff Software Engineer"),
        ("Lead Software Engineer", "Senior Software Engineer"),
    ],
)
def test_different_roles_at_same_company_are_not_duplicates(
    title_a: str, title_b: str
) -> None:
    assert not _match(title_a, title_b)


@pytest.mark.parametrize(
    ("title_a", "title_b"),
    [
        # Abbreviations expand to the same title.
        ("Staff Software Engineer", "Staff SE"),
        ("Principal Software Engineer, GRC", "Prin SE"),
        ("PrinSE CoreAI", "Principal Software Engineer - CoreAI"),
        # Same qualifier, different punctuation style.
        (
            "Staff Software Engineer, Care & Onboarding",
            "Staff Software Engineer (Care & Onboarding)",
        ),
        # Qualifier on only one side still matches.
        ("Staff Software Engineer, Payments", "Staff Software Engineer"),
        # Seniority named on only one side still matches.
        ("Software Engineer, Full Stack", "Senior Software Engineer, Full Stack"),
        # Two FLEX postings with the same title are the same job.
        ("FLEX Senior Software Engineer", "FLEX Senior Software Engineer"),
        # Same numbered level, different roman/arabic numeral style.
        ("Software Engineer 2", "Software Engineer II"),
        # Numbered level on only one side still matches (consistent with the
        # existing "seniority named on only one side still matches" rule).
        ("Software Engineer 2", "Software Engineer"),
        # Same "Lead" rung is still a duplicate.
        ("Lead Software Engineer", "Lead Software Engineer"),
    ],
)
def test_equivalent_titles_are_duplicates(title_a: str, title_b: str) -> None:
    assert _match(title_a, title_b)
