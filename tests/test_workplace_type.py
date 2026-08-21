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

# ai: claude-sonnet-5 | 2026-08-21
"""Tests for workplace-type recovery when a JD (re-)fetch would drop it.

Regression coverage for job #4150: a LinkedIn "jobs you may be interested in"
digest named the role Hybrid in its card text, but LinkedIn's guest jobs API
(used to fetch the full description) never includes that badge, so the fit
scorer never saw it once the fetched text replaced the digest snippet.
"""

from __future__ import annotations

from backend.scoring.workplace_type import (
    ensure_workplace_type_preserved,
    extract_workplace_type,
)


def test_extract_workplace_type_from_linkedin_digest_card() -> None:
    text = "Selby Jennings · Wilmington, NC (Hybrid)   Actively recruiting"
    assert extract_workplace_type(text) == "Hybrid"


def test_extract_workplace_type_from_jymbii_remote_card() -> None:
    text = "Needl · United States (Remote)   Actively recruiting"
    assert extract_workplace_type(text) == "Remote"


def test_extract_workplace_type_from_explicit_jd_mention() -> None:
    text = "This is a hybrid role requiring 3 days a week in office."
    assert extract_workplace_type(text) == "Hybrid"


def test_extract_workplace_type_ignores_unrelated_culture_mention() -> None:
    text = "We foster a hybrid-friendly, collaborative culture."
    assert extract_workplace_type(text) is None


def test_extract_workplace_type_returns_none_when_absent() -> None:
    text = "Lead Full Stack Engineer Selby Jennings Wilmington, NC 1 week ago"
    assert extract_workplace_type(text) is None


def test_ensure_workplace_type_preserved_recovers_from_prior_text() -> None:
    fetched = "Lead Full Stack Engineer Selby Jennings Wilmington, NC 1 week ago About the job..."
    prior = "Selby Jennings · Wilmington, NC (Hybrid)   Actively recruiting"
    result = ensure_workplace_type_preserved(fetched, prior)
    assert result.startswith("Workplace Type: Hybrid\n\n")
    assert fetched in result


def test_ensure_workplace_type_preserved_leaves_fetched_text_unchanged_when_already_stated() -> None:
    fetched = "This is a hybrid role requiring 3 days a week in office."
    prior = "Company · City (Remote)"
    assert ensure_workplace_type_preserved(fetched, prior) == fetched


def test_ensure_workplace_type_preserved_leaves_fetched_text_unchanged_when_prior_has_no_signal() -> None:
    fetched = "About the job: we build great software."
    prior = "Just a plain digest card with no workplace-type badge."
    assert ensure_workplace_type_preserved(fetched, prior) == fetched


def test_ensure_workplace_type_preserved_handles_empty_fetched_text() -> None:
    assert ensure_workplace_type_preserved("", "Company · City (Hybrid)") == ""
