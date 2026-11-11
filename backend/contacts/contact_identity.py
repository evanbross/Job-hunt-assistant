# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""How a contact's identity columns are derived from what the user (or a LinkedIn
export) says about them -- shared by the Connections.csv import and by contacts
added by hand, so the two produce the same dedup_key for the same person and a
later import merges into a hand-added recruiter instead of duplicating them."""

from __future__ import annotations

from typing import Any

from backend.contacts.title_normalization import normalize_title
from backend.scoring.company_matching import normalize_company_name


# ai: claude-opus-5-5 | 2026-11-11 -- moved out of ContactImportService.import_csv
def contact_dedup_key(profile_url: str | None, full_name: str, company_normalized: str) -> str:
    """The profile URL when there is one, else name|company. Must match the
    contacts.dedup_key backfill in backend/db/schema.py."""
    return profile_url or f"{full_name.lower()}|{company_normalized}"


# ai: claude-opus-5-5 | 2026-11-11
def contact_identity_columns(
    full_name: str, company: str, position: str, profile_url: str | None
) -> dict[str, Any]:
    """The contacts columns that follow from a name, company, title and profile
    URL: the raw values plus their normalized forms and the dedup_key."""
    company_normalized = normalize_company_name(company)
    return {
        "dedup_key": contact_dedup_key(profile_url, full_name, company_normalized),
        "full_name": full_name,
        "company_raw": company,
        "company_normalized": company_normalized,
        "position": position,
        "normalized_title": normalize_title(position),
        "profile_url": profile_url,
    }
