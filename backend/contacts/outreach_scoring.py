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

"""Pure, transparent outreach-priority scoring for imported contacts."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Mapping

COMPANY_WEIGHT = 0.35
ROLE_WEIGHT = 0.30
RELATIONSHIP_WEIGHT = 0.25
ENGAGEMENT_WEIGHT = 0.10

ACTIVE_COMPANY_SCORE = 10.0
PAST_COMPANY_SCORE = 5.0

_RECRUITER_SCORES: Mapping[str, float] = {
    "high_reliability": 10.0,
    "motion": 6.0,
    "unrated": 4.0,
    "low_reliability": 2.0,
    "offshore_spam": 0.0,
}
_HIRING_MANAGER_PHRASES = ("hiring manager", "head of engineering")
_LEADERSHIP_TERMS = (
    "chief technology officer",
    "cto",
    "vice president",
    "vp ",
    "director",
    "engineering manager",
    "software manager",
)


# ai: gpt-5 | 2026-10-02
def score_contact(
    contact: Mapping[str, Any],
    company_relevance: Mapping[str, float],
    *,
    as_of: date | None = None,
) -> float:
    """Return a 0–10 weighted outreach score rounded to one decimal place."""
    factors = (
        COMPANY_WEIGHT * _company_score(contact, company_relevance)
        + ROLE_WEIGHT * _role_score(contact)
        + RELATIONSHIP_WEIGHT * _relationship_score(contact, as_of or datetime.now(UTC).date())
        + ENGAGEMENT_WEIGHT * _engagement_score(contact)
    )
    # Decimal-style half-up rounding is easier to tune by eye than Python's
    # banker rounding (where 1.25 unexpectedly displays as 1.2).
    return int(factors * 10 + 0.5) / 10


# ai: gpt-5 | 2026-10-02
def _company_score(contact: Mapping[str, Any], relevance: Mapping[str, float]) -> float:
    company = str(contact.get("company_normalized") or "")
    return float(relevance.get(company, 0.0)) if company else 0.0


# ai: gpt-5 | 2026-10-02
def _role_score(contact: Mapping[str, Any]) -> float:
    recruiter_type = contact.get("recruiter_type")
    if recruiter_type in _RECRUITER_SCORES:
        return _RECRUITER_SCORES[str(recruiter_type)]

    title = str(contact.get("normalized_title") or "").strip().lower()
    if any(phrase in title for phrase in _HIRING_MANAGER_PHRASES):
        return 9.0
    if any(term in title for term in _LEADERSHIP_TERMS):
        return 8.0
    if "manager" in title or "lead" in title:
        return 6.0
    return 2.0 if title else 0.0


# ai: gpt-5 | 2026-10-02
def _relationship_score(contact: Mapping[str, Any], as_of: date) -> float:
    # ai: claude-opus-5-5 | 2026-10-14 -- was contact["emails"] (renamed with contact_outreach)
    outreach = contact.get("outreach") or []
    if outreach:
        return 10.0 if any(entry.get("response_received_at") for entry in outreach) else 5.0

    connected_on = _parse_date(contact.get("connected_on"))
    if connected_on is None:
        return 0.0
    age_days = max(0, (as_of - connected_on).days)
    if age_days < 60:
        return 2.0
    if age_days < 365 * 2:
        return _interpolate(age_days, 60, 365 * 2, 10.0, 8.0)
    if age_days < 365 * 5:
        return _interpolate(age_days, 365 * 2, 365 * 5, 7.0, 5.0)
    return 3.0


# ai: gpt-5 | 2026-10-02
def _engagement_score(contact: Mapping[str, Any]) -> float:
    return (8.0 if contact.get("interested") else 0.0) + (
        2.0 if str(contact.get("notes") or "").strip() else 0.0
    )


# ai: gpt-5 | 2026-10-02
def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ai: gpt-5 | 2026-10-02
def _interpolate(value: int, start: int, end: int, high: float, low: float) -> float:
    progress = (value - start) / (end - start)
    return high + (low - high) * progress
