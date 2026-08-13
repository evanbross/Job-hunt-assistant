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

# ai: claude-sonnet-5 | 2026-08-13
"""Content-based auto-reject/auto-flag rules applied during ingestion.

Named auto_reject_rules.py (not job_rejection_rules.py) to avoid confusion
with the unrelated backend/db/repositories/career_rejection_rules.py
(the DB-table repository for user-configured rejection rules)."""

from __future__ import annotations

import re

# ai: claude-fable-5 | 2026-07-16
# MongoDB hybrid-policy boilerplate comes in two forms. The possessive form
# ("MongoDB's hybrid working model", straight or curly apostrophe) is
# self-identifying; the "our" form — usually "We are looking to speak to
# candidates who are based in <city> for our hybrid working model" — only
# counts when the job's company identifies as MongoDB.
_MONGODB_POSSESSIVE_HYBRID_RE = re.compile(
    r"mongodb[’']s\s+hybrid\s+(?:work(?:ing)?\s+)?model", re.IGNORECASE
)
_OUR_HYBRID_MODEL_RE = re.compile(
    r"\bour\s+hybrid\s+work(?:ing)?\s+model\b", re.IGNORECASE
)
_BASED_IN_HYBRID_LOCATION_RE = re.compile(
    r"based\s+in\s+(?P<location>[^.,;\n]{1,60}?)\s+for\s+our\s+hybrid\s+"
    r"work(?:ing)?\s+model",
    re.IGNORECASE,
)
# Mirrors the explicit-remote language recognized by FitScorer's remote exception,
# plus bare "remotely" ("this role will be based remotely in <region>").
_EXPLICIT_REMOTE_RE = re.compile(
    r"\b(?:remote(?:ly)?|fully remote|remote-first|work remotely|remote work|"
    r"remote position|remote role|remote opportunity|option to work remotely)\b",
    re.IGNORECASE,
)


# ai: claude-fable-5 | 2026-07-16
def mongodb_hybrid_rejection_note(company: str, job_text: str) -> str | None:
    """Return an auto-rejection note when a MongoDB posting references its hybrid
    working model without explicitly stating the role is remote, else None.

    Most MongoDB postings are hybrid and signal it only through boilerplate like
    "We are looking to speak to candidates who are based in Sydney for our hybrid
    working model". Unless the posting explicitly says remote, that reference
    means an in-office requirement in the named location.
    """
    if not job_text:
        return None
    is_mongodb_company = "mongodb" in (company or "").lower()
    matched = _MONGODB_POSSESSIVE_HYBRID_RE.search(job_text) or (
        is_mongodb_company and _OUR_HYBRID_MODEL_RE.search(job_text)
    )
    if not matched:
        return None
    if _EXPLICIT_REMOTE_RE.search(job_text):
        return None
    location_match = _BASED_IN_HYBRID_LOCATION_RE.search(job_text)
    if location_match:
        location = re.sub(r"\s+", " ", location_match.group("location")).strip()
        return (
            f"Auto-rejected: MongoDB hybrid role based in {location}, "
            "with no explicit remote statement."
        )
    return (
        "Auto-rejected: references MongoDB's hybrid working model without an "
        "explicit remote statement."
    )


def jd_blocklist_match(haystack: str, rules: list[dict]) -> dict | None:
    """Return the first blocklist rule whose text appears in *haystack*, or None.

    *haystack* should be a lowercased concatenation of all searchable fields for
    the job (title, body, company, URL, etc.) — the caller decides what to include.
    Rules are expected to come from ``career_rejection_rules.get_enabled()``.
    """
    lowered = haystack.lower()
    for rule in rules:
        rule_text = str(rule.get("rule_text") or "").strip().lower()
        if rule_text and rule_text in lowered:
            return rule
    return None
