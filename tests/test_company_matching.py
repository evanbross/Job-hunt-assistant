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

from __future__ import annotations

import pytest

from backend.company_matching import normalize_company_name

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
