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

"""Groups imported contacts by normalized company name, so the frontend can
fetch the whole map once and look up matches per job card client-side
instead of issuing a request per company (see JobQueue/index.jsx)."""

from __future__ import annotations

from typing import Any


# ai: claude-sonnet-5 | 2026-07-25
def build_company_map(contacts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    company_map: dict[str, list[dict[str, Any]]] = {}
    for contact in contacts:
        key = contact.get("company_normalized") or ""
        if not key:
            continue
        company_map.setdefault(key, []).append(
            {
                "id": contact["id"],
                "full_name": contact["full_name"],
                "position": contact["position"],
                "profile_url": contact["profile_url"],
                "connected_on": contact["connected_on"],
            }
        )
    return company_map
