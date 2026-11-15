# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Tests for DuplicateChecker's title gates: two jobs at the same company are
not duplicates when their titles name different seniority levels or different
department/team qualifiers, even though rapidfuzz token_set_ratio scores the
shared remainder of the titles above the match threshold."""

from __future__ import annotations

import pytest

from backend.scoring.duplicate_checker import DuplicateChecker, companies_match
from backend.scoring.duplicate_matching import AUTO_REJECT_DUPLICATE_COMPANY_THRESHOLD


def _match(title_a: str, title_b: str) -> bool:
    checker = DuplicateChecker()
    result = checker.check(
        job_title=title_a,
        company="Acme",
        job_url="",
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
        # ai: claude-opus-5-5 | 2026-11-13
        # Stripping a location clause exposes the real team qualifier.
        (
            "Staff Software Engineer - Platform (Remote)",
            "Staff Software Engineer - Websites (Remote)",
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
        # ai: claude-opus-5-5 | 2026-11-13
        # A location/work-arrangement clause is not a team qualifier.
        (
            "Staff Software Engineer - Platform",
            "Staff Software Engineer - Platform (Remote anywhere in USA Only)",
        ),
        ("Senior Software Engineer, Payments (Hybrid)", "Senior Software Engineer, Payments"),
        ("Site Reliability Engineer (US - Pacific time)", "Site Reliability Engineer"),
        ("Senior Backend Engineer - Remote, US", "Senior Backend Engineer, Ledger"),
    ],
)
def test_equivalent_titles_are_duplicates(title_a: str, title_b: str) -> None:
    assert _match(title_a, title_b)


# ai: claude-opus-5-5 | 2026-11-13
# Every pair below was linked as a duplicate in real data by the old
# token_set_ratio >= 80 company score.
@pytest.mark.parametrize(
    ("company_a", "company_b"),
    [
        ("Coast", "Toast"),
        ("REVER", "River"),
        ("Radar", "Raydar"),
        ("Rational", "Fractional"),
        ("Ethena", "Athena"),
        ("Scout", "Help Scout"),
        ("Citizens", "First Citizens Bank"),
        ("Air", "Electric Air"),
        ("Levanta", "Vanta"),
        ("ServiceNow", "ServiceUp"),
        ("Seeq Corporation", "Corvel Corporation"),
        ("Virta Health", "Verana Health"),
        ("Komodo Health", "Omada Health"),
        ("Foundation Health", "Function Health"),
        ("DreamCraft Entertainment, Inc.", "PENN Entertainment, Inc"),
        ("Raytheon Technologies", "Maxar Technologies"),
        ("Meta", "Metabase"),
        ("Fi", "galileo financial/SoFi"),
        ("Browser Use", "The Browser Company"),
    ],
)
def test_different_companies_do_not_match(company_a: str, company_b: str) -> None:
    assert not companies_match(company_a, company_b)
    assert not companies_match(company_b, company_a)


# ai: claude-opus-5-5 | 2026-11-13
@pytest.mark.parametrize(
    ("company_a", "company_b"),
    [
        ("Acme", "acme"),
        ("Acme", "Acme Inc."),
        ("Zip", "Zip Co Limited"),
        ("Intelex Technologies ULC", "Intelex Technologies"),
        ("PayNearMe, Inc.", "paynearme"),  # Greenhouse board token
        ("Addepar", "addepar1"),  # board token with an ATS disambiguator
        ("Cordial", "cordial81"),
        ("Ladders", "Theladders"),
        ("The Home Depot", "Home Depot"),
        ("Hims & Hers", "Hims and Hers"),
        ("Virta", "Virta Health"),
        ("Horizon3", "Horizon3.ai"),
        ("SoFi", "galileo financial/SoFi"),
        ("Clover Health", "Clover Heatlh"),  # a typo in a long name
        ("Ford Motor", "Ford Motor Company"),
    ],
)
def test_variants_of_one_company_match(company_a: str, company_b: str) -> None:
    assert companies_match(company_a, company_b)
    assert companies_match(company_b, company_a)


# ai: claude-opus-5-5 | 2026-11-13
def test_a_leading_words_company_match_stays_below_the_auto_skip_bar() -> None:
    """'Weave' / 'Weave Robotics' may be different companies, so a match on the
    shorter name's words must go to review, never auto-skip."""
    match = DuplicateChecker().check(
        job_title="Senior Software Engineer",
        company="Weave Robotics",
        job_url="",
        sheet_jobs=[{"company": "Weave", "job_title": "Senior Software Engineer"}],
    )
    assert match is not None
    assert match["company_score"] < AUTO_REJECT_DUPLICATE_COMPANY_THRESHOLD


# ai: claude-opus-5-5 | 2026-11-13
@pytest.mark.parametrize(
    ("title_a", "title_b"),
    [
        ("Software Engineer - Backend", "Full Stack Software Engineer"),
        ("Senior DevOps Engineer (Remote)", "Senior Fullstack Software Engineer (Remote)"),
        ("Senior Frontend Engineer", "Senior Backend Engineer (Search)"),
        ("Data Engineer", "QA Engineer"),
        ("Senior Mobile Software Engineer - Android & iOS", "SrSE Backend AI Agent"),
    ],
)
def test_different_specialties_at_same_company_are_not_duplicates(
    title_a: str, title_b: str
) -> None:
    assert not _match(title_a, title_b)


# ai: claude-opus-5-5 | 2026-11-13
@pytest.mark.parametrize(
    "other", ["Senior Software Engineer", "Staff Software Engineer", "Junior Software Engineer"]
)
def test_an_internship_is_its_own_level(other: str) -> None:
    assert not _match("Software Engineering Intern", other)
    assert _match("Software Engineering Intern", "Software Engineering Internship")


# ai: claude-opus-5-5 | 2026-11-13
@pytest.mark.parametrize(
    ("title_a", "title_b"),
    [
        ("Senior Backend Software Engineer", "Sr Backend SE"),
        ("Software Engineer, Full Stack", "Full-Stack Software Engineer"),
        ("Senior DevOps Engineer", "Senior Dev Ops Engineer"),
        ("Frontend Engineer", "Front-End Engineer"),
        ("Machine Learning Engineer", "ML Engineer"),
    ],
)
def test_same_specialty_spelled_differently_still_matches(title_a: str, title_b: str) -> None:
    assert _match(title_a, title_b)
