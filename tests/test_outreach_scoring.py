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

from datetime import date, timedelta

import pytest

from backend.contacts.outreach_scoring import score_contact


# ai: gpt-5 | 2026-10-02
def _contact(**overrides: object) -> dict[str, object]:
    contact: dict[str, object] = {
        "company_normalized": "",
        "normalized_title": "",
        "recruiter_type": None,
        "connected_on": None,
        "emails": [],
        "interested": False,
        "notes": None,
    }
    contact.update(overrides)
    return contact


# ai: gpt-5 | 2026-10-02
@pytest.mark.parametrize(
    ("company_score", "expected"),
    [(10.0, 3.5), (5.0, 1.8), (0.0, 0.0)],
)
def test_company_relevance_tiers(company_score: float, expected: float) -> None:
    contact = _contact(company_normalized="acme")
    relevance = {"acme": company_score} if company_score else {}
    assert score_contact(contact, relevance, as_of=date(2026, 10, 2)) == expected


@pytest.mark.parametrize(
    ("recruiter_type", "expected"),
    [
        ("high_reliability", 3.0),
        ("motion", 1.8),
        ("unrated", 1.2),
        ("low_reliability", 0.6),
        ("offshore_spam", 0.0),
    ],
)
# ai: gpt-5 | 2026-10-02
def test_recruiter_role_tiers(recruiter_type: str, expected: float) -> None:
    assert score_contact(
        _contact(recruiter_type=recruiter_type), {}, as_of=date(2026, 10, 2)
    ) == expected


# ai: gpt-5 | 2026-10-02
def test_non_recruiter_leadership_title_scores_above_unrelated_title() -> None:
    manager = score_contact(
        _contact(normalized_title="engineering manager"), {}, as_of=date(2026, 10, 2)
    )
    unrelated = score_contact(
        _contact(normalized_title="software engineer"), {}, as_of=date(2026, 10, 2)
    )
    assert manager == 2.4
    assert unrelated == 0.6


# ai: gpt-5 | 2026-10-02
@pytest.mark.parametrize(
    ("age_days", "expected"),
    [
        (59, 0.5),
        (60, 2.5),
        (729, 2.0),
        (730, 1.8),
        (1824, 1.3),
        (1825, 0.8),
    ],
)
def test_tenure_curve_boundaries(age_days: int, expected: float) -> None:
    as_of = date(2026, 10, 2)
    score = score_contact(
        _contact(connected_on=as_of - timedelta(days=age_days)), {}, as_of=as_of
    )
    assert score == expected


# ai: gpt-5 | 2026-10-02
def test_email_history_overrides_connection_tenure() -> None:
    as_of = date(2026, 10, 2)
    old_connection = as_of - timedelta(days=365 * 10)
    sent = score_contact(
        _contact(connected_on=old_connection, emails=[{"response_received_at": None}]),
        {},
        as_of=as_of,
    )
    replied = score_contact(
        _contact(connected_on=old_connection, emails=[{"response_received_at": "2026-01-01"}]),
        {},
        as_of=as_of,
    )
    assert sent == 1.3
    assert replied == 2.5


# ai: gpt-5 | 2026-10-02
def test_engagement_combines_interest_and_notes() -> None:
    assert score_contact(
        _contact(interested=True, notes="Met at a conference"), {}, as_of=date(2026, 10, 2)
    ) == 1.0
