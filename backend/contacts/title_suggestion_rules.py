# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Tier 1 keyword rules for suggesting recruiter titles
(docs/contacts-recruiter-classification-plan.md, build-order item 4). Pure,
local, no network -- runs against contacts.normalized_title, which is
already lowercased/whitespace-collapsed (backend/contacts/title_normalization.py).
Precision first: an ambiguous or negated title is never auto-flagged on its
own, only ever promoted to "flag" by a corroborating signal (see
title_suggestion_service.py's agency-company check)."""

from __future__ import annotations

from typing import Literal

# High confidence: the title itself is unambiguous.
_FLAG_KEYWORDS: tuple[str, ...] = (
    "recruiter",
    "recruiting",
    "talent acquisition",
    "sourcer",
    "technical sourcing",
)

# Ambiguous: plausibly a recruiter, plausibly not. Never auto-flagged alone --
# only promoted when a second signal corroborates (an agency-flagged company).
_SKIP_KEYWORDS: tuple[str, ...] = (
    "talent partner",
    "head of talent",
    "people operations",
    "hr business partner",
    "hiring manager",
)

# Overrides a flag-keyword match: "former recruiter" must not be suggested.
_NEGATIVE_PATTERNS: tuple[str, ...] = (
    "former recruiter",
    "ex-recruiter",
    "ex recruiter",
    "former talent acquisition",
)

TitleTier1Verdict = Literal["flag", "skip"] | None


# ai: claude-sonnet-5 | 2026-09-28
def classify_title_tier1(normalized_title: str) -> TitleTier1Verdict:
    """"flag" (high confidence recruiter), "skip" (ambiguous -- needs a
    corroborating signal or Tier 2 to be suggested), or None (no opinion --
    falls through to Tier 2)."""
    if not normalized_title:
        return None
    if any(pattern in normalized_title for pattern in _NEGATIVE_PATTERNS):
        return None
    if any(keyword in normalized_title for keyword in _FLAG_KEYWORDS):
        return "flag"
    if any(keyword in normalized_title for keyword in _SKIP_KEYWORDS):
        return "skip"
    return None
