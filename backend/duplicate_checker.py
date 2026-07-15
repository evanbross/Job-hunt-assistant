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

"""
Fuzzy duplicate checker — compares a newly parsed job against entries already
in the database so the user can confirm whether it's the same listing seen before.

Matching strategy:
  1. Normalize both sides:
     a. Split camelCase / PascalCase BEFORE lowercasing, so compound
        abbreviations like "PrinSE" → "Prin SE" and "CoreAI" → "Core AI"
        are separated into tokens the abbreviation table can expand.
     b. Lowercase and apply the abbreviation expansion table.
     c. Strip punctuation and collapse whitespace.
  2. Use rapidfuzz token_set_ratio on company name and job title separately.
     token_set_ratio handles word-order differences and subset matches well,
     which is important for titles like "PrinSE CoreAI" vs
     "Principal Software Engineer - CoreAI".
  3. Require BOTH company AND title to exceed their respective thresholds.
     A high company match alone (e.g. two different roles at the same firm)
     is not a duplicate.
"""

from __future__ import annotations

import re
from typing import TypedDict

from rapidfuzz import fuzz

# Regex substitutions applied after lowercasing.
# Order matters — apply more specific / longer patterns first.
_ABBREV_PATTERNS: list[tuple[str, str]] = [
    # Multi-word fused abbreviations (produced after camelCase splitting)
    (r"\bprin\s+se\b", "principal software engineer"),
    (r"\bprin\s+swe\b", "principal software engineer"),
    (r"\bprin\s+eng\b", "principal engineer"),
    (r"\bstaff\s+se\b", "staff software engineer"),
    (r"\bstaff\s+swe\b", "staff software engineer"),
    (r"\bstaff\s+eng\b", "staff engineer"),
    (r"\bsr\s+se\b", "senior software engineer"),
    (r"\bsr\s+swe\b", "senior software engineer"),
    (r"\bsr\s+eng\b", "senior engineer"),
    # Single-token abbreviations
    (r"\bsr\.?\s*s\.?e\.?\b", "senior software engineer"),
    (r"\bstaff\s+s\.?e\.?\b", "staff software engineer"),
    (r"\bprincipal\s+s\.?e\.?\b", "principal software engineer"),
    (r"\bsr\.?\s*swe\b", "senior software engineer"),
    (r"\bprinse\b", "principal software engineer"),  # fused, no space
    (r"\bswe\b", "software engineer"),
    (r"\bs\.?e\.?\b", "software engineer"),
    (r"\bprin\b", "principal"),
    (r"\bsr\.?\b", "senior"),
    (r"\bjr\.?\b", "junior"),
    (r"\bpe\b", "principal engineer"),
    (r"\bic\b", "individual contributor"),
    (r"\bmgr\b", "manager"),
    (r"\bmle\b", "machine learning engineer"),
    (r"\btpm\b", "technical program manager"),
    (r"\bpm\b", "product manager"),
    (r"\bvp\b", "vice president"),
    (r"\beng\b", "engineer"),
    (r"\bdev\b", "developer"),
    (r"\barch\b", "architect"),
    (r"\bfull[\s-]?stack\b", "fullstack"),
]

# Minimum rapidfuzz scores (0–100) required on each dimension.
_COMPANY_THRESHOLD: int = 80
_TITLE_THRESHOLD: int = 70

# Minimum rapidfuzz score required between two titles' extracted qualifiers
# (see _extract_qualifier) before they're treated as the same department/team.
_QUALIFIER_THRESHOLD: int = 60

# Trailing parenthetical, dash-clause, or comma-clause that names a specific
# department/team, e.g. "Staff Software Engineer, Backend" or
# "Staff Software Engineer (Configuration Platform)". Checked in this order
# since a comma can appear *inside* a parenthetical qualifier.
_QUALIFIER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\(([^)]+)\)\s*$"),
    re.compile(r"\s[-–]\s(.+)$"),
    re.compile(r",\s*(.+)$"),
]

# Seniority levels matched against a *normalized* title (abbreviations already
# expanded, punctuation stripped). Checked in order — "senior staff" must be
# tested before "senior" and "staff" so it isn't misread as either alone.
# "Senior/Staff" also normalizes to "senior staff" and is treated as that
# combined level.
_SENIORITY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsenior staff\b"), "senior staff"),
    (re.compile(r"\bdistinguished\b"), "distinguished"),
    (re.compile(r"\bprincipal\b"), "principal"),
    (re.compile(r"\bstaff\b"), "staff"),
    (re.compile(r"\bsenior\b"), "senior"),
    (re.compile(r"\bjunior\b"), "junior"),
]

# Marriott prefixes temporary/flexible positions with "FLEX" — a FLEX role and
# its non-FLEX namesake are different jobs. Matched against the raw title.
_FLEX_PREFIX_RE: re.Pattern[str] = re.compile(r"^\s*flex\b", re.IGNORECASE)


class DuplicateMatch(TypedDict):
    sheet_company: str
    sheet_title: str
    company_score: int
    title_score: int


def _split_camel_case(text: str) -> str:
    """
    Insert spaces at camelCase and PascalCase boundaries so that fused
    abbreviations become separate tokens before lowercasing.

    Examples:
        "PrinSE"   → "Prin SE"
        "CoreAI"   → "Core AI"
        "SrSWE"    → "Sr SWE"
        "StaffEng" → "Staff Eng"
    """
    # "CoreAI" → "Core AI"  (sequence of caps followed by cap+lower)
    t = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    # "PrinSE" → "Prin SE"  (lowercase letter followed by uppercase)
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", t)
    return t


def _normalize(text: str) -> str:
    """Split camelCase, lowercase, expand abbreviations, collapse whitespace."""
    t = _split_camel_case(text)
    t = t.lower().strip()
    for pattern, replacement in _ABBREV_PATTERNS:
        t = re.sub(pattern, replacement, t)
    t = re.sub(r"[^\w\s]", " ", t)  # strip punctuation
    return re.sub(r"\s+", " ", t).strip()


# ai: claude-sonnet-4-6 | 2026-07-13
def _extract_qualifier(title: str) -> str | None:
    """Return the trailing department/team qualifier of a job title, if any.

    Returns None when the title has no parenthetical, dash-, or comma-clause
    suffix, so titles like "Senior Staff Software Engineer" are left alone.
    """
    for pattern in _QUALIFIER_PATTERNS:
        m = pattern.search(title)
        if m:
            return m.group(1).strip()
    return None


# ai: claude-fable-5 | 2026-07-13
def _extract_seniority(normalized_title: str) -> str | None:
    """Return the seniority level named in a normalized title, or None.

    Expects _normalize() output so abbreviations ("Prin SE", "Sr SWE") are
    already expanded into the words the patterns look for.
    """
    for pattern, level in _SENIORITY_PATTERNS:
        if pattern.search(normalized_title):
            return level
    return None


def _slugify(text: str) -> str:
    """Remove all non-alphanumeric chars and lowercase — produces a URL-slug form.

    Used as a fallback company comparison: "paynearmeinc" and "PayNearMe, Inc."
    both slugify to "paynearmeinc", catching cases where a Greenhouse board token
    is stored as the company name instead of the display name.
    """
    return re.sub(r"[^a-z0-9]", "", text.lower())


class DuplicateChecker:
    """
    Compares a job's (company, title) against a list of already-tracked sheet
    rows and returns the closest match if both company and title exceed the
    similarity thresholds.
    """

    def check(
        self,
        job_title: str,
        company: str,
        sheet_jobs: list[dict],
    ) -> DuplicateMatch | None:
        """
        Returns the best-matching sheet row as a DuplicateMatch, or None if no
        row clears both thresholds.

        sheet_jobs: list of dicts with at least 'company' and 'job_title' keys.
        """
        norm_title = _normalize(job_title)
        norm_company = _normalize(company)
        own_seniority = _extract_seniority(norm_title)
        own_is_flex = bool(_FLEX_PREFIX_RE.match(job_title))

        best: DuplicateMatch | None = None
        best_combined: float = 0.0

        for row in sheet_jobs:
            sheet_company_raw = row.get("company") or ""
            sheet_title_raw = row.get("job_title") or ""

            if not sheet_company_raw and not sheet_title_raw:
                continue

            c_score: int = fuzz.token_set_ratio(
                norm_company, _normalize(sheet_company_raw)
            )
            # Slug fallback: catches Greenhouse board tokens stored as company names
            # (e.g. "paynearmeinc" vs "PayNearMe, Inc.").
            if c_score < _COMPANY_THRESHOLD:
                own_slug = _slugify(company)
                sheet_slug = _slugify(sheet_company_raw)
                if own_slug and sheet_slug and own_slug == sheet_slug:
                    c_score = 100
            norm_sheet_title = _normalize(sheet_title_raw)
            t_score: int = fuzz.token_set_ratio(norm_title, norm_sheet_title)

            if c_score >= _COMPANY_THRESHOLD and t_score >= _TITLE_THRESHOLD:
                # A FLEX-prefixed title and its unprefixed namesake are
                # different jobs; token_set_ratio scores the one-token subset
                # difference at 100, so check the prefix explicitly.
                if own_is_flex != bool(_FLEX_PREFIX_RE.match(sheet_title_raw)):
                    continue
                # Both titles name an explicit seniority level and they differ
                # (e.g. "Principal Software Engineer" vs "Staff Software
                # Engineer") — different rungs at the same company are
                # different roles, even though token_set_ratio scores the
                # shared remainder of the title above the threshold.
                sheet_seniority = _extract_seniority(norm_sheet_title)
                if (
                    own_seniority is not None
                    and sheet_seniority is not None
                    and own_seniority != sheet_seniority
                ):
                    continue
                # Both titles name a specific department/team (e.g. "Staff Software
                # Engineer, Backend" vs "Staff Software Engineer, Configuration
                # Platform") — token_set_ratio largely ignores this differing
                # suffix, so require the qualifiers themselves to match too.
                own_qualifier = _extract_qualifier(job_title)
                sheet_qualifier = _extract_qualifier(sheet_title_raw)
                if own_qualifier and sheet_qualifier:
                    qualifier_score = fuzz.token_set_ratio(
                        _normalize(own_qualifier), _normalize(sheet_qualifier)
                    )
                    if qualifier_score < _QUALIFIER_THRESHOLD:
                        continue
                combined = (c_score + t_score) / 2.0
                if combined > best_combined:
                    best_combined = combined
                    best = DuplicateMatch(
                        sheet_company=sheet_company_raw,
                        sheet_title=sheet_title_raw,
                        company_score=c_score,
                        title_score=t_score,
                    )

        return best
