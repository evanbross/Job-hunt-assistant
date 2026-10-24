# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Company name normalization for matching LinkedIn contacts against job
postings. Mirrors contactsMatchKey() in
frontend/src/utils/contactsUtils.ts — keep the suffix list and operation
order identical in both places."""

from __future__ import annotations

import re

# ai: claude-opus-5-5 | 2026-10-23 — the suffix list is now shared by the
# Python regex and the SQL expression below, so the two can't drift apart.
_CORP_SUFFIXES: tuple[str, ...] = ("inc", "llc", "ltd", "corp", "corporation", "company", "co")
_CORP_SUFFIX_RE = re.compile(rf"\b({'|'.join(_CORP_SUFFIXES)})\b\.?")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


# ai: claude-sonnet-5 | 2026-07-25
def normalize_company_name(value: str | None) -> str:
    text = _CORP_SUFFIX_RE.sub("", (value or "").lower())
    return _NON_ALNUM_RE.sub("", text)


# ai: claude-opus-5-5 | 2026-10-23
def normalize_company_name_sql(column: str) -> str:
    """A Postgres expression equal to normalize_company_name(<column>), for
    queries that must match a job's raw company against contacts the same
    way the Job Queue's contact badge does (contacts.company_normalized is
    normalize_company_name(company_raw), with no alias resolution, unlike
    jobs.company_normalized). Postgres spells the word boundary \\y; the
    contract test in tests/test_company_matching_sql.py runs both versions
    over the same names. *column* is interpolated, so it must be a trusted
    identifier, never user input."""
    suffixes = "|".join(_CORP_SUFFIXES)
    return (
        f"regexp_replace(regexp_replace(lower(COALESCE({column}, '')),"
        f" '\\y({suffixes})\\y\\.?', '', 'g'), '[^a-z0-9]+', '', 'g')"
    )
