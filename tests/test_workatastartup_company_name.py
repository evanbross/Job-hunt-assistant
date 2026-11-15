# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================
# ai: claude-opus-5-5 | 2026-11-13
import pytest

from backend.scanning.import_repair import workatastartup_company_name


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        # Byte-for-byte shapes of jobs 7665/7664: NBSP, batch, bullet, tagline.
        ("Nitrode (W25)•Frontier AI research to advance game development", "Nitrode"),
        ("Epsilon3 (S21)•AI-Powered ERP, MES, and Test Software (beta)", "Epsilon3"),
        ("Klavis AI (P26) • MCP integrations", "Klavis AI"),
        ("Acme (F24)", "Acme"),
        # Already clean, or corrected by hand: unchanged.
        ("PostHog", "PostHog"),
        ("Studio (2024) Labs", "Studio (2024) Labs"),
    ],
)
def test_workatastartup_company_name(stored: str, expected: str) -> None:
    assert workatastartup_company_name(stored) == expected
