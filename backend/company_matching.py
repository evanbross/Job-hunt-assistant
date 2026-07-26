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

"""Company name normalization for matching LinkedIn contacts against job
postings. Mirrors contactsMatchKey() in
frontend/src/utils/contactsUtils.js — keep the suffix list and operation
order identical in both places."""

from __future__ import annotations

import re

_CORP_SUFFIX_RE = re.compile(r"\b(inc|llc|ltd|corp|corporation|company|co)\b\.?")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


# ai: claude-sonnet-5 | 2026-07-25
def normalize_company_name(value: str | None) -> str:
    text = _CORP_SUFFIX_RE.sub("", (value or "").lower())
    return _NON_ALNUM_RE.sub("", text)
