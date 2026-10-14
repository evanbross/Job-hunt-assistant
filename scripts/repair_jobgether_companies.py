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

# ai: claude-sonnet-5 | 2026-10-06
# ai: claude-sonnet-5 | 2026-10-08 — widened from the "Jobgether" placeholder
# case to every missing-company Jobgether row (see docstring update below)
"""One-off maintenance script: repair Jobgether jobs with no real company on file.

Original root cause (fixed alongside this script, see backend/utils/company_urls.py
_KNOWN_JOB_BOARD_HOSTS): jobgether.com was missing from the known-job-board
exclusion list, so whenever a Jobgether capture had no usable company text,
backend.utils.company_urls.company_from_career_domain() fell back to parsing
jobgether.com's own domain and returned "Jobgether" as the company. 286 rows
were affected this way (284 via jobgether.com/offer/ URLs; 2 more share the
literal company name "Jobgether" but are LinkedIn postings for a Staff/Senior
engineering role at Jobgether the company itself — those are correct and
intentionally excluded below).

A second, independent failure mode leaves company plain blank instead of the
"Jobgether" placeholder: the browser extension's client-side company
extraction (content.js buildJobgetherJobPayload) can still come up empty for
a given posting (e.g. job 6071 — EnergyHub, captured 2026-10-08), and nothing
re-fetches the page to recover it afterward. This script's candidate filter
originally only matched the placeholder case, leaving blank-company rows
unrepaired even though jobgether_company_repair_updates() below already
handles both. Widened here to cover both.

Each affected jobgether.com/offer/ row's URL is the offer page itself, so the
live page is authoritative for its own company: re-fetches it and extracts
the real employer from the page's own schema.org JobPosting JSON-LD (see
backend.scanning.import_repair.jobgether_company_repair_updates). A company
the user already corrected by hand (e.g. job 5752 -> "Instacart") is left
untouched; a listing that has been taken down (no JobPosting data) is
reported and skipped for manual review.

Usage:
    python scripts/repair_jobgether_companies.py          # apply fixes
    python scripts/repair_jobgether_companies.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from urllib.error import HTTPError as UrllibHTTPError
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.scanning.import_repair import (  # noqa: E402
    fetch_jobgether_page_html,
    is_jobgether_placeholder_company,
    is_missing_company,
    jobgether_company_repair_updates,
    post_repair_duplicate_check,
)
from backend.utils.url_safety import UnsafeUrlError  # noqa: E402
from backend.utils.utils import get_repos  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
)
logger = logging.getLogger(__name__)

_USER_ID = 1  # sole account in this single-tenant deployment (evan@eross.com)
_FETCH_INTERVAL_SECONDS = 1.0


def _candidates(repos: dict) -> list[dict]:
    jobs = repos["jobs"].get_all_ordered(_USER_ID)
    return [
        job
        for job in jobs
        if (
            is_jobgether_placeholder_company(job.get("company"))
            or is_missing_company(job.get("company"))
        )
        and str(job.get("url") or "").startswith("https://jobgether.com/offer/")
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Report planned changes without writing them."
    )
    args = parser.parse_args()

    with get_repos(_USER_ID) as repos:
        candidates = _candidates(repos)
        logger.info("Found %d candidate job(s) with company == 'Jobgether'", len(candidates))

        repaired = 0
        unchanged = 0
        failed = 0
        for i, job in enumerate(candidates, start=1):
            job_id = int(job["id"])
            url = str(job["url"])
            logger.info("[%d/%d] job %d: %s", i, len(candidates), job_id, url)
            if i > 1:
                time.sleep(_FETCH_INTERVAL_SECONDS)

            try:
                html = fetch_jobgether_page_html(url)
            except (UrllibHTTPError, URLError, UnsafeUrlError, TimeoutError) as exc:
                logger.warning("  fetch failed: %s", exc)
                failed += 1
                continue

            updates, problem = jobgether_company_repair_updates(job, html)
            if problem:
                logger.warning("  %s", problem)
                failed += 1
                continue
            if not updates:
                logger.info("  no change needed")
                unchanged += 1
                continue

            logger.info("  company: %r -> %r", job.get("company") or "", updates["company"])
            if not args.dry_run:
                repos["jobs"].update(_USER_ID, job_id, updates)
                repos["conn"].commit()
                # ai: claude-sonnet-5 | 2026-10-08
                # Re-evaluate this job's duplicate status now that its real
                # company is on file -- a stale duplicate_of_id set while
                # every Jobgether row still shared the "Jobgether" placeholder
                # company would otherwise point at an unrelated employer
                # forever. See post_repair_duplicate_check's docstring.
                updated_job = repos["jobs"].get_by_id(_USER_ID, job_id)
                if updated_job:
                    if post_repair_duplicate_check(_USER_ID, repos["jobs"], updated_job):
                        repos["conn"].commit()
            repaired += 1

        logger.info(
            "Done. repaired=%d unchanged=%d failed=%d%s",
            repaired,
            unchanged,
            failed,
            " (dry run — no writes made)" if args.dry_run else "",
        )


if __name__ == "__main__":
    main()
