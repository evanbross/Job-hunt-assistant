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

# ai: claude-sonnet-5 | 2026-08-21
"""Recovers a job's on-site/hybrid/remote workplace type when a JD (re-)fetch
would otherwise silently drop it.

LinkedIn renders workplace type (Hybrid/Remote/On-site) as a top-card UI badge
that is not part of the job description HTML. Every code path that re-fetches
a job's JD from its URL (LinkedIn's guest jobs API, or any other career-site
fetch) therefore returns text with no workplace-type signal at all, even
though a lighter-weight source captured earlier — an email digest card
("Company · City, ST (Hybrid)"), a logged-in browser DOM capture — usually
does state it. Callers that are about to let fetched JD text replace that
earlier text should route the replacement through
ensure_workplace_type_preserved() so the signal isn't lost.

No project imports: this is a leaf module so any ingestion/enrichment call
site can import it without risking a circular import.
"""

from __future__ import annotations

import re

_WORKPLACE_TYPE_RE = re.compile(
    r"\((Hybrid|Remote|On-site|Onsite)\)|(?<![A-Za-z])(Hybrid|Remote|On-site|Onsite)(?=\s*[·|]|\s+role\b|\s+position\b|\s*$)",
    re.IGNORECASE,
)

_CANONICAL_LABELS = {
    "hybrid": "Hybrid",
    "remote": "Remote",
    "onsite": "On-site",
    "on-site": "On-site",
}


def extract_workplace_type(text: str) -> str | None:
    """Best-effort extraction of a Hybrid/Remote/On-site workplace-type badge.

    Scoped to the first 2000 characters so it reads the same "quick facts"
    region a human would (title/company/location line), rather than matching
    an unrelated mention of "remote" buried deep in a job's body copy.
    """
    if not text:
        return None
    match = _WORKPLACE_TYPE_RE.search(text[:2000])
    if not match:
        return None
    value = (match.group(1) or match.group(2)).strip().lower()
    return _CANONICAL_LABELS[value]


def ensure_workplace_type_preserved(fetched_text: str, prior_text: str) -> str:
    """Prepend a "Workplace Type: X" line to fetched_text if prior_text names
    a workplace type that fetched_text itself doesn't mention.

    Returns fetched_text unchanged when it's empty, already states a
    workplace type, or prior_text doesn't name one either.
    """
    if not fetched_text or extract_workplace_type(fetched_text):
        return fetched_text
    workplace_type = extract_workplace_type(prior_text)
    if not workplace_type:
        return fetched_text
    return f"Workplace Type: {workplace_type}\n\n{fetched_text}"
