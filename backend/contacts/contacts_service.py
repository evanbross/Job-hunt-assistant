# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
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
