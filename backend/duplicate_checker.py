"""
Fuzzy duplicate checker — compares a newly parsed job against entries already
in the Google Sheets tracker so the user can confirm whether it's the same
listing seen before.

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
    (r"\bprin\s+se\b",          "principal software engineer"),
    (r"\bprin\s+swe\b",         "principal software engineer"),
    (r"\bprin\s+eng\b",         "principal engineer"),
    (r"\bstaff\s+se\b",         "staff software engineer"),
    (r"\bstaff\s+swe\b",        "staff software engineer"),
    (r"\bstaff\s+eng\b",        "staff engineer"),
    (r"\bsr\s+se\b",            "senior software engineer"),
    (r"\bsr\s+swe\b",           "senior software engineer"),
    (r"\bsr\s+eng\b",           "senior engineer"),
    # Single-token abbreviations
    (r"\bsr\.?\s*s\.?e\.?\b",   "senior software engineer"),
    (r"\bstaff\s+s\.?e\.?\b",   "staff software engineer"),
    (r"\bprincipal\s+s\.?e\.?\b", "principal software engineer"),
    (r"\bsr\.?\s*swe\b",        "senior software engineer"),
    (r"\bprinse\b",             "principal software engineer"),  # fused, no space
    (r"\bswe\b",                "software engineer"),
    (r"\bs\.?e\.?\b",           "software engineer"),
    (r"\bprin\b",               "principal"),
    (r"\bsr\.?\b",              "senior"),
    (r"\bjr\.?\b",              "junior"),
    (r"\bpe\b",                 "principal engineer"),
    (r"\bic\b",                 "individual contributor"),
    (r"\bmgr\b",                "manager"),
    (r"\bmle\b",                "machine learning engineer"),
    (r"\btpm\b",                "technical program manager"),
    (r"\bpm\b",                 "product manager"),
    (r"\bvp\b",                 "vice president"),
    (r"\beng\b",                "engineer"),
    (r"\bdev\b",                "developer"),
    (r"\barch\b",               "architect"),
    (r"\bfull[\s-]?stack\b",    "fullstack"),
]

# Minimum rapidfuzz scores (0–100) required on each dimension.
_COMPANY_THRESHOLD: int = 80
_TITLE_THRESHOLD: int = 70


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
    t = re.sub(r"[^\w\s]", " ", t)   # strip punctuation
    return re.sub(r"\s+", " ", t).strip()


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

        best: DuplicateMatch | None = None
        best_combined: float = 0.0

        for row in sheet_jobs:
            sheet_company_raw = row.get("company", "")
            sheet_title_raw = row.get("job_title", "")

            if not sheet_company_raw and not sheet_title_raw:
                continue

            c_score: int = fuzz.token_set_ratio(norm_company, _normalize(sheet_company_raw))
            t_score: int = fuzz.token_set_ratio(norm_title, _normalize(sheet_title_raw))

            if c_score >= _COMPANY_THRESHOLD and t_score >= _TITLE_THRESHOLD:
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
