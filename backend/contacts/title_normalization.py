# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
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
