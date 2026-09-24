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

"""Match-vs-delete logic for email rescans (routes/emails.py's rescan_email).

A rescan re-parses the source email and used to delete every active job it had
previously produced before recreating them from scratch — even ones the new
parse still names. These tests cover the matching helper that lets a rescan
update an unchanged listing in place instead, so only jobs with no match in
the fresh parse get deleted.
"""

from __future__ import annotations

from backend.scoring.duplicate_checker import DuplicateChecker
from routes.emails import find_existing_email_job_match, job_url_base

_dedup_checker = DuplicateChecker()


def test_job_url_base_strips_query_and_fragment() -> None:
    assert (
        job_url_base("https://boards.greenhouse.io/acme/jobs/123?gh_src=abc#top")
        == "https://boards.greenhouse.io/acme/jobs/123"
    )


def test_job_url_base_empty_string_for_empty_input() -> None:
    assert job_url_base("") == ""


def test_matches_existing_job_by_exact_url() -> None:
    candidates = [
        {"id": 1, "company": "Acme", "job_title": "Engineer", "url": "https://acme.example/jobs/1"},
        {"id": 2, "company": "Other Co", "job_title": "Designer", "url": "https://other.example/jobs/2"},
    ]
    parsed = {"company": "Acme", "job_title": "Engineer", "url": "https://acme.example/jobs/1"}

    match = find_existing_email_job_match(parsed, candidates, _dedup_checker)

    assert match is not None
    assert match["id"] == 1


def test_matches_existing_job_by_url_ignoring_query_params() -> None:
    """Tracking params can shift between sends of the same digest email."""
    candidates = [
        {
            "id": 7,
            "company": "NinjaOne",
            "job_title": "Staff Software Engineer",
            "url": "https://boards.greenhouse.io/ninjaone/jobs/999?gh_jid=old",
        }
    ]
    parsed = {
        "company": "NinjaOne",
        "job_title": "Staff Software Engineer",
        "url": "https://boards.greenhouse.io/ninjaone/jobs/999?gh_jid=new",
    }

    match = find_existing_email_job_match(parsed, candidates, _dedup_checker)

    assert match is not None
    assert match["id"] == 7


def test_falls_back_to_fuzzy_company_and_title_when_no_url_match() -> None:
    candidates = [
        {"id": 3, "company": "Acme Inc.", "job_title": "Senior Software Engineer", "url": ""}
    ]
    parsed = {"company": "Acme", "job_title": "Senior Software Engineer", "url": ""}

    match = find_existing_email_job_match(parsed, candidates, _dedup_checker)

    assert match is not None
    assert match["id"] == 3


def test_no_match_returns_none() -> None:
    candidates = [
        {"id": 4, "company": "Acme", "job_title": "Engineer", "url": "https://acme.example/jobs/1"}
    ]
    parsed = {
        "company": "Totally Different Co",
        "job_title": "Unrelated Role",
        "url": "https://different.example/jobs/2",
    }

    match = find_existing_email_job_match(parsed, candidates, _dedup_checker)

    assert match is None


def test_no_match_against_empty_candidate_list() -> None:
    parsed = {"company": "Acme", "job_title": "Engineer", "url": "https://acme.example/jobs/1"}

    assert find_existing_email_job_match(parsed, [], _dedup_checker) is None
