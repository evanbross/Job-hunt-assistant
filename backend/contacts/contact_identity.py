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
from urllib.parse import unquote, urlparse

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


# ai: claude-opus-5-5 | 2026-10-14 -- moved here from outreach_drafts.py 2026-11-11
def linkedin_profile_slug(url: str | None) -> str | None:
    """The member identifier from a linkedin.com/in/<slug> URL, lowercased and
    percent-decoded, or None for anything else. Comparing slugs rather than
    whole URLs tolerates the trailing slashes, query strings, overlay paths
    (/in/<slug>/overlay/...) and http/www variants LinkedIn adds or drops."""
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].lower() != "in":
        return None
    return unquote(parts[1]).lower() or None
