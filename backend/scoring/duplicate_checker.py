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
  2. Companies must match by rule (companies_match): the same name once legal
     suffixes are dropped, one name being the other's leading words, or a
     close spelling of a long name. Not a fuzzy score -- token_set_ratio
     counted any shared word as a match (Scout / Help Scout).
  3. Titles use rapidfuzz token_set_ratio, which handles word-order and subset
     differences ("PrinSE CoreAI" vs "Principal Software Engineer - CoreAI"),
     gated so different seniority levels, specialties, or team qualifiers at
     the same company never count as the same role.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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

# Minimum rapidfuzz score (0–100) required on the title. The company side is
# a rule-based match (see _company_match_score), not a threshold.
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

# ai: claude-opus-5-5 | 2026-11-13
# A trailing clause that names where or how the job is worked, not a team:
# "Staff Software Engineer - Platform (Remote anywhere in USA Only)". Read as
# a qualifier it made that title "different" from the same company's "Staff
# Software Engineer - Platform" (Legion Intelligence, 2026-11-13). Such clauses
# are stripped before the qualifier is extracted. Matched against one clause
# at a time, so a word like "us" only counts inside a trailing clause.
_LOCATION_CLAUSE_RE: re.Pattern[str] = re.compile(
    r"\b(?:remote|hybrid|on[\s-]?site|in[\s-]?office|anywhere|work from home|wfh"
    r"|usa?|u\.s\.?(?:a\.?)?|united states|north america|americas|canada|uk|europe"
    r"|emea|apac|latam|(?:pacific|eastern|central|mountain) time|[pecm][sd]?t"
    r"|time\s?zones?)\b",
    re.IGNORECASE,
)
# Dash/comma clauses exclude parentheses so " - Platform (Remote)" peels off
# "(Remote)" first rather than being taken as one clause.
_TRAILING_CLAUSE_RE: re.Pattern[str] = re.compile(
    r"(?:\s*\(([^)]*)\)|\s[-–]\s([^-–()]+)|,\s*([^,()]+))\s*$"
)

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
    # ai: claude-opus-5-5 | 2026-11-13
    (re.compile(r"\bintern(ship)?\b"), "intern"),
]

# ai: claude-opus-5-5 | 2026-11-13
# Engineering specialties, matched against the plain lowercased title -- not
# _normalize()'s output, whose abbreviation table rewrites "DevOps" into
# "developer ops". Two titles that each name one, with none in common, are
# different roles at the same company ("Software Engineer - Backend" vs "Full
# Stack Software Engineer" scored 81 on token_set_ratio; "Senior DevOps
# Engineer" vs "Senior Fullstack Software Engineer" 76). One side naming none
# still matches, like the seniority and qualifier gates.
_SPECIALTY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bback[\s-]?end\b"), "backend"),
    (re.compile(r"\bfront[\s-]?end\b"), "frontend"),
    (re.compile(r"\bfull[\s-]?stack\b"), "fullstack"),
    (re.compile(r"\bdev[\s-]?ops\b|\bsite reliability\b|\bsre\b"), "devops"),
    (re.compile(r"\bmobile\b|\bios\b|\bandroid\b"), "mobile"),
    (re.compile(r"\bmachine learning\b|\bml\b|\bmle\b"), "ml"),
    (re.compile(r"\bdata\b"), "data"),
    (re.compile(r"\bsecurity\b"), "security"),
    (re.compile(r"\bembedded\b|\bfirmware\b"), "embedded"),
    (re.compile(r"\bqa\b|\bquality\b|\bin test\b|\bsdet\b"), "qa"),
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


# ai: claude-opus-5-5 | 2026-11-13
def _strip_location_clauses(title: str) -> str:
    """Drop trailing clauses that only say where/how the job is worked."""
    while True:
        m = _TRAILING_CLAUSE_RE.search(title)
        if not m or not _LOCATION_CLAUSE_RE.search(next(g for g in m.groups() if g is not None)):
            return title
        title = title[: m.start()].rstrip()


# ai: claude-sonnet-4-6 | 2026-07-13
def _extract_qualifier(title: str) -> str | None:
    """Return the trailing department/team qualifier of a job title, if any.

    Returns None when the title has no parenthetical, dash-, or comma-clause
    suffix, so titles like "Senior Staff Software Engineer" are left alone.
    Location/work-arrangement clauses are not qualifiers and are skipped.
    """
    title = _strip_location_clauses(title)  # ai: claude-opus-5-5 | 2026-11-13
    for pattern in _QUALIFIER_PATTERNS:
        m = pattern.search(title)
        if m:
            return m.group(1).strip()
    return None


# ai: claude-opus-5-5 | 2026-11-13
def _extract_specialties(title: str) -> frozenset[str]:
    """The engineering specialties a raw job title names (often none)."""
    lowered = title.lower()
    return frozenset(name for pattern, name in _SPECIALTY_PATTERNS if pattern.search(lowered))


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


# ai: claude-opus-5-5 | 2026-11-13
# Trailing words that name a legal form, not the company ("Acme Inc." is Acme).
# Not "company": it is usually part of the name ("The Browser Company" stripped
# to "Browser" matched "Browser Use"), and "Ford Motor" / "Ford Motor Company"
# still match by leading words.
_LEGAL_SUFFIXES: frozenset[str] = frozenset(
    {"inc", "incorporated", "llc", "ltd", "limited", "co", "corp", "corporation",
     "plc", "gmbh", "pbc", "sa", "ag", "lp", "llp", "ulc"}
)
# Below this length a one-letter difference is a different name (Coast/Toast,
# Radar/Raydar), so only the exact-name rules apply.
_MIN_FUZZY_COMPANY_CHARS = 8
_COMPANY_FUZZY_THRESHOLD = 90
# Score reported for a leading-words match ("Virta" / "Virta Health"): a real
# match, but below duplicate_matching's 95 auto-skip bar, so it always gets a
# human look -- the shorter name can be a different company ("Weave" /
# "Weave Robotics").
_PREFIX_COMPANY_SCORE = 90
_BOARD_TOKEN_DIGITS_RE = re.compile(r"(?<=[a-z])\d+$")


# ai: claude-opus-5-5 | 2026-11-13
def _company_core(name: str) -> list[str]:
    """A company name's identifying words: normalized, with a leading "The" and
    trailing legal suffixes dropped. A lowercase one-word name is treated as a
    Greenhouse/Lever board token, whose trailing digits are a disambiguator the
    ATS added ("addepar1", "cordial81"), not part of the name."""
    tokens = _normalize(name.replace("&", " and ")).split()
    if len(tokens) > 1 and tokens[0] == "the":
        tokens = tokens[1:]
    while len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    if len(tokens) == 1:
        token = tokens[0]
        if name.strip() == name.strip().lower():
            token = _BOARD_TOKEN_DIGITS_RE.sub("", token)
        if token.startswith("the") and len(token) > 6:  # "Theladders" is Ladders
            token = token[3:]
        tokens = [token]
    return tokens


# ai: claude-opus-5-5 | 2026-11-13
def _single_name_score(a: str, b: str) -> int | None:
    core_a, core_b = _company_core(a), _company_core(b)
    if not core_a or not core_b:
        return None
    # Same words, or the same letters spaced differently (a board token
    # "paynearme" vs "PayNearMe, Inc.").
    if core_a == core_b or "".join(core_a) == "".join(core_b):
        return 100
    # One name is the other's leading words: "Virta" / "Virta Health". Whole
    # words only, so "Meta" never matches "Metabase", and only as a prefix, so
    # "Scout" never matches "Help Scout".
    shorter, longer = sorted((core_a, core_b), key=len)
    if longer[: len(shorter)] == shorter:
        return _PREFIX_COMPANY_SCORE
    # A spelling variant of a long name. Plain ratio, not token_set_ratio: a
    # shared generic word ("Health", "Technologies", "Entertainment") must not
    # carry two different names over the bar.
    text_a, text_b = " ".join(core_a), " ".join(core_b)
    if min(len(text_a), len(text_b)) < _MIN_FUZZY_COMPANY_CHARS:
        return None
    ratio = round(fuzz.ratio(text_a, text_b))
    return ratio if ratio >= _COMPANY_FUZZY_THRESHOLD else None


# ai: claude-opus-5-5 | 2026-11-13
def _company_match_score(a: str, b: str) -> int | None:
    """How surely two company names are the same company (90-100), or None
    when they aren't. A "/" separates alternate names ("galileo
    financial/SoFi"); the best-scoring pair of them counts."""
    names_a = [part for part in (a or "").split("/") if part.strip()]
    names_b = [part for part in (b or "").split("/") if part.strip()]
    scores = [
        score
        for x in names_a
        for y in names_b
        if (score := _single_name_score(x, y)) is not None
    ]
    return max(scores, default=None)


# ai: claude-opus-5-5 | 2026-11-13
def companies_match(a: str, b: str) -> bool:
    """True when two company names refer to the same company.

    Replaces a token_set_ratio >= 80 score, which counted any shared word or
    one-letter difference as the same company -- Coast/Toast, Scout/Help Scout,
    Seeq Corporation/Corvel Corporation, Virta Health/Verana Health (all linked
    as duplicates in real data).
    """
    return _company_match_score(a, b) is not None


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
        own = _TitleFeatures.of(job_title)
        best: DuplicateMatch | None = None
        best_combined: float = 0.0

        for row in sheet_jobs:
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
            match = _match_row(own, company, row)
            if match is None:
                continue
            combined = (match["company_score"] + match["title_score"]) / 2.0
            if combined > best_combined:
                best_combined = combined
                best = match

        return best

    # ai: claude-opus-5-5 | 2026-11-13
    def match_pair(self, job_title: str, company: str, row: dict) -> DuplicateMatch | None:
        """Whether one job's company and title match *row*'s, by the same
        company rules and title gates as check() -- without check()'s
        closed-req and distinct-posting filters, which decide whether a match
        should be acted on today, not whether two jobs are the same role."""
        return _match_row(_TitleFeatures.of(job_title), company, row)


# ai: claude-opus-5-5 | 2026-11-13
@dataclass(frozen=True)
class _TitleFeatures:
    """A title's comparison features, computed once per check() call rather
    than once per candidate row."""

    raw: str
    normalized: str
    seniority: str | None
    specialties: frozenset[str]
    is_flex: bool
    qualifier: str | None

    @classmethod
    def of(cls, title: str) -> _TitleFeatures:
        normalized = _normalize(title)
        return cls(
            raw=title,
            normalized=normalized,
            seniority=_extract_seniority(normalized),
            specialties=_extract_specialties(title),
            is_flex=bool(_FLEX_PREFIX_RE.match(title)),
            qualifier=_extract_qualifier(title),
        )


# ai: claude-opus-5-5 | 2026-11-13 -- body moved unchanged out of check()'s loop
def _match_row(own: _TitleFeatures, company: str, row: dict) -> DuplicateMatch | None:
    sheet_company_raw = row.get("company") or ""
    sheet_title_raw = row.get("job_title") or ""
    if not sheet_company_raw and not sheet_title_raw:
        return None

    # ai: claude-opus-5-5 | 2026-11-13 -- rule-based company match, see companies_match
    c_score = _company_match_score(company, sheet_company_raw)
    if c_score is None:
        return None
    norm_sheet_title = _normalize(sheet_title_raw)
    t_score: int = fuzz.token_set_ratio(own.normalized, norm_sheet_title)
    if t_score < _TITLE_THRESHOLD:
        return None
    # A FLEX-prefixed title and its unprefixed namesake are
    # different jobs; token_set_ratio scores the one-token subset
    # difference at 100, so check the prefix explicitly.
    if own.is_flex != bool(_FLEX_PREFIX_RE.match(sheet_title_raw)):
        return None
    # Both titles name an explicit seniority level and they differ
    # (e.g. "Principal Software Engineer" vs "Staff Software
    # Engineer") — different rungs at the same company are
    # different roles, even though token_set_ratio scores the
    # shared remainder of the title above the threshold.
    sheet_seniority = _extract_seniority(norm_sheet_title)
    if own.seniority is not None and sheet_seniority is not None and own.seniority != sheet_seniority:
        return None
    # ai: claude-opus-5-5 | 2026-11-13
    sheet_specialties = _extract_specialties(sheet_title_raw)
    if own.specialties and sheet_specialties and not (own.specialties & sheet_specialties):
        return None
    # Both titles name a specific department/team (e.g. "Staff Software
    # Engineer, Backend" vs "Staff Software Engineer, Configuration
    # Platform") — token_set_ratio largely ignores this differing
    # suffix, so require the qualifiers themselves to match too.
    sheet_qualifier = _extract_qualifier(sheet_title_raw)
    if own.qualifier and sheet_qualifier:
        qualifier_score = fuzz.token_set_ratio(_normalize(own.qualifier), _normalize(sheet_qualifier))
        if qualifier_score < _QUALIFIER_THRESHOLD:
            return None
    return DuplicateMatch(
        sheet_company=sheet_company_raw,
        sheet_title=sheet_title_raw,
        company_score=c_score,
        title_score=t_score,
        row=row,
    )
