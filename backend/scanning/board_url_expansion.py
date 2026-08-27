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
"""Expand job-board listing-page URLs into individual job postings."""

from __future__ import annotations

import logging

from backend.email.email_parser import ParsedJob
from backend.scanning.career_site_scanner import CareerSiteScanner, is_job_board_url

logger = logging.getLogger(__name__)


def expand_board_url_parsed_jobs(parsed_jobs: list[ParsedJob]) -> list[ParsedJob]:
    """Replace any job-board listing URLs with individual job entries.

    When the email parser returns a URL that points to an ATS board listing page
    (not an individual job), this calls scan_site() to discover the actual
    postings and inlines them in place of the board entry.  Falls back to the
    original entry unchanged on any error so the caller always gets a non-empty
    list when the input is non-empty.
    """
    result: list[ParsedJob] = []
    scanner = CareerSiteScanner()

    for parsed in parsed_jobs:
        url = str(parsed.get("url") or "").strip()
        if not url or not url.startswith("https://") or not is_job_board_url(url):
            result.append(parsed)
            continue

        company = str(parsed.get("company") or "").strip()
        source = str(parsed.get("source") or "").strip()

        try:
            outcome = scanner.scan_site(company or source or "Unknown", url)
            expanded = outcome["jobs"]
        except Exception as exc:
            logger.warning("Board URL expansion failed for %s: %s", url, exc)
            result.append(parsed)
            continue

        if not expanded:
            result.append(parsed)
            continue

        for exp_job in expanded:
            result.append(
                ParsedJob(
                    company=exp_job.get("company") or company,
                    job_title=exp_job["job_title"],
                    url=exp_job["job_url"],
                    source=source,
                    certainty_level="high",
                    certainty_score=exp_job["confidence_score"],
                    certainty_reason="Expanded from job board URL in email.",
                )
            )

        logger.info("Expanded board URL %s into %d individual jobs", url, len(expanded))

    return result
