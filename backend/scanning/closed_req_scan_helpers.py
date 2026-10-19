# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-fable-5 | 2026-07-17
"""Shared by the closed-req scan and the WTTJ repair flow (moved from
routes/applications.py)."""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse

RATE_LIMIT_DEFAULT_BACKOFF_SECONDS = 30.0
RATE_LIMIT_BACKOFF_CAP_SECONDS = 120.0


# ai: claude-fable-5 | 2026-07-17
def rate_limit_backoff_seconds(retry_after: str | None) -> float:
    """Seconds to wait before retrying after an HTTP 429.

    Honors a numeric Retry-After header value, clamped to a sane range;
    falls back to a default backoff when the header is absent or unparseable
    (HTTP-date form included — not worth parsing for this use)."""
    try:
        seconds = float(retry_after) if retry_after is not None else None
    except ValueError:
        seconds = None
    if seconds is None or not 0 <= seconds:
        return RATE_LIMIT_DEFAULT_BACKOFF_SECONDS
    return min(max(seconds, 1.0), RATE_LIMIT_BACKOFF_CAP_SECONDS)


# ai: claude-fable-5 | 2026-07-17 — moved from routes/applications.py so both the
# closed-req scan and the WTTJ repair flow share one canonicalization.
def canonicalize_wttj_url(url: str) -> str:
    """Strip the query string and fragment from a Welcome to the Jungle job URL.

    WTTJ email links carry a ?token=<JWT> auth param plus tracking params. When
    fetched with the token, app.welcometothejungle.com serves a client-side app
    shell with no job content — no closed-req banner and no JSON-LD JobPosting
    block ever appear in the HTML. The bare path URL gets the full
    server-rendered page. WTTJ job URLs identify the job entirely by path, so
    dropping the query is always safe.
    """
    parsed = urlparse(url)
    if "welcometothejungle.com" not in parsed.netloc.lower():
        return url
    return urlunparse(parsed._replace(query="", fragment=""))
