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

"""Job-title normalization for the Titles report and title rules
(docs/contacts-recruiter-classification-plan.md). Deliberately gentler than
backend/scoring/company_matching.py's normalize_company_name(): titles keep
their words and punctuation (only case and whitespace are collapsed) so the
normalized form is still readable as a label and the report's "contains
recruit/talent/sourc" quick filters keep working against it."""

from __future__ import annotations

import re

_WHITESPACE_RE = re.compile(r"\s+")


# ai: claude-sonnet-5 | 2026-09-28
def normalize_title(value: str | None) -> str:
    return _WHITESPACE_RE.sub(" ", (value or "").strip()).lower()
