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

# ai: claude-sonnet-5 | 2026-08-13
"""ATS/platform identification from a job or career-site URL."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# ai: claude-sonnet-5 | 2026-10-15
# LinkedIn's /jobs/search-results/ page honors `start` for pagination (start=0
# is page 1, start=25 is page 2, ...) -- this is the page size that paging
# advances by.
LINKEDIN_SEARCH_PAGE_SIZE = 25


def linkedin_search_url_with_offset(url: str, offset: int) -> str:
    """Return `url` with its `start` query param set to `offset`.

    Only meaningful for a linkedin.com search-results URL; callers should
    check platform_for_site_url(url) == "linkedin" first. Used so a scheduled
    automatic_browser LinkedIn career-site scan pages through a search's full
    result set across successive runs instead of re-capturing the same first
    page every time (see next_scan_offset handling in routes/career_sites.py
    and backend/tasks.py's _record_capture_source).
    """
    parsed = urlparse(url)
    params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "start"
    ]
    if offset:
        params.append(("start", str(offset)))
    return urlunparse(parsed._replace(query=urlencode(params)))


def platform_for_site_url(site_url: str) -> str:
    host = site_url.lower()
    if "greenhouse.io" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "ashbyhq.com" in host:
        return "ashby"
    if "wellfound.com" in host:
        return "wellfound"
    if "linkedin.com" in host:
        return "linkedin"
    if "myworkdayjobs.com" in host:
        return "workday"
    if "discord.com/careers" in host or "discord.com/jobs" in host:
        return "greenhouse"
    # ai: claude-sonnet-4-6 | 2026-06-25
    if "welcometothejungle.com" in host:
        return "welcometothejungle"
    return "generic"


def platform_label(platform: str) -> str:
    labels = {
        "greenhouse": "Greenhouse",
        "lever": "Lever",
        "workday": "Workday",
        "ashby": "Ashby",
        "wellfound": "Wellfound",
        "linkedin": "LinkedIn",
        "welcometothejungle": "Welcome to the Jungle",
    }
    return labels.get(platform.lower(), platform.title() or "Career Site")
