# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

import pytest

from backend.scoring.company_matching import normalize_company_name

# ai: claude-sonnet-5 | 2026-07-25


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme, Inc.", "acme"),
        ("Acme Inc", "acme"),
        ("ACME INC.", "acme"),
        ("Acme LLC", "acme"),
        ("Acme Corp", "acme"),
        ("Acme Corporation", "acme"),
        ("Acme Company", "acme"),
        ("Acme Co.", "acme"),
        ("ObsidianSecurity", "obsidiansecurity"),
        ("Obsidian Security", "obsidiansecurity"),
        ("", ""),
        (None, ""),
        ("...,,,", ""),
    ],
)
def test_normalize_company_name(raw: str | None, expected: str) -> None:
    assert normalize_company_name(raw) == expected
