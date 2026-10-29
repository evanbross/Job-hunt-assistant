# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
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

from backend.scoring.posting_identity import are_distinct_postings
from backend.scoring.skip_reasons import is_req_closed_job_row

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
    # ai: claude-sonnet-5 | 2026-10-08
    (re.compile(r"\blead\b"), "lead"),
    (re.compile(r"\bsenior\b"), "senior"),
    (re.compile(r"\bjunior\b"), "junior"),
]

# ai: claude-sonnet-5 | 2026-07-25
# A trailing digit or roman numeral on an "engineer" title names a specific
# numbered rung on the pre-Senior career ladder (Engineer I/II/III, Software
# Engineer 1/2/3) — entry-to-mid-level roles a candidate works up through
# before reaching "Senior". It is never a synonym for "Senior"/"Staff"/etc.,
# and each number is its own distinct level (Engineer 2 != Engineer 3).
_NUMBERED_LEVEL_RE: re.Pattern[str] = re.compile(r"\bengineer\s+(iv|iii|ii|i|[1-5])\b")
_ROMAN_NUMERAL_LEVELS: dict[str, str] = {"i": "1", "ii": "2", "iii": "3", "iv": "4"}

# Marriott prefixes temporary/flexible positions with "FLEX" — a FLEX role and
# its non-FLEX namesake are different jobs. Matched against the raw title.
_FLEX_PREFIX_RE: re.Pattern[str] = re.compile(r"^\s*flex\b", re.IGNORECASE)


class DuplicateMatch(TypedDict):
    sheet_company: str
    sheet_title: str
    company_score: int
    title_score: int
    # ai: claude-opus-5-5 | 2026-10-29 -- the matched sheet_jobs row itself, so
    # callers needn't re-find it by company+title, which can land on a
    # different same-titled row (several CVS openings share a title).
    row: dict


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
    numbered_match = _NUMBERED_LEVEL_RE.search(normalized_title)
    if numbered_match:
        raw_level = numbered_match.group(1)
        return f"engineer level {_ROMAN_NUMERAL_LEVELS.get(raw_level, raw_level)}"
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


# ai: claude-sonnet-5 | 2026-09-22
def _company_similarity(a: str, b: str) -> int:
    """Fuzzy similarity score (0-100) between two company names.

    Falls back to slug equality for cases like a Greenhouse board token
    stored as the company name ("paynearmeinc" vs "PayNearMe, Inc.").
    """
    score: int = fuzz.token_set_ratio(_normalize(a), _normalize(b))
    if score < _COMPANY_THRESHOLD:
        a_slug, b_slug = _slugify(a), _slugify(b)
        if a_slug and b_slug and a_slug == b_slug:
            score = 100
    return score


# ai: claude-sonnet-5 | 2026-09-22
def companies_match(a: str, b: str) -> bool:
    """True when two company names likely refer to the same company."""
    if not a or not b:
        return False
    return _company_similarity(a, b) >= _COMPANY_THRESHOLD


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
        job_url: str,
        sheet_jobs: list[dict],
    ) -> DuplicateMatch | None:
        """
        Returns the best-matching sheet row as a DuplicateMatch, or None if no
        row clears both thresholds.

        job_url: the checked job's URL ("" when it has none). Required so no
        caller can skip the distinct-posting rule below.
        sheet_jobs: list of dicts with at least 'company' and 'job_title' keys
        ('url' and 'id' when known).
        """
        norm_title = _normalize(job_title)
        own_seniority = _extract_seniority(norm_title)
        own_is_flex = bool(_FLEX_PREFIX_RE.match(job_title))

        best: DuplicateMatch | None = None
        best_combined: float = 0.0

        for row in sheet_jobs:
            sheet_company_raw = row.get("company") or ""
            sheet_title_raw = row.get("job_title") or ""

            if not sheet_company_raw and not sheet_title_raw:
                continue

            # ai: claude-sonnet-5 | 2026-09-23
            # A closed req is a dead listing: a *similar* new posting at the same
            # company is a fresh opening, not a duplicate of it. Enforced here, the
            # one place every fuzzy caller funnels through, so no call site can
            # forget it.
            if is_req_closed_job_row(row):
                continue
            # ai: claude-opus-5-5 | 2026-10-29
            # Different job IDs on the same site are separate openings, however
            # alike their titles (CVS lists several same-titled requisitions).
            if are_distinct_postings(job_url, row.get("url") or ""):
                continue

            # ai: claude-sonnet-5 | 2026-09-22 — extracted to _company_similarity, reused by companies_match
            c_score: int = _company_similarity(company, sheet_company_raw)
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
                        row=row,
                    )

        return best
