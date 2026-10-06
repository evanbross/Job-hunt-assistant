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
"""One-off maintenance script: repair jobs whose company was stored as the
literal board name "Jobgether" instead of the real employer.

Root cause (fixed alongside this script, see backend/utils/company_urls.py
_KNOWN_JOB_BOARD_HOSTS): jobgether.com was missing from the known-job-board
exclusion list, so whenever a Jobgether capture had no usable company text,
backend.utils.company_urls.company_from_career_domain() fell back to parsing
jobgether.com's own domain and returned "Jobgether" as the company. 286 rows
were affected (284 via jobgether.com/offer/ URLs; 2 more share the literal
company name "Jobgether" but are LinkedIn postings for a Staff/Senior
engineering role at Jobgether the company itself — those are correct and
intentionally excluded below).

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
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.scanning.closed_req_scan_helpers import (  # noqa: E402
    rate_limit_backoff_seconds,
)
from backend.scanning.import_repair import (  # noqa: E402
    jobgether_company_repair_updates,
)
from backend.utils.url_safety import UnsafeUrlError, safe_urlopen  # noqa: E402
from backend.utils.utils import get_repos  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
)
logger = logging.getLogger(__name__)

_USER_ID = 1  # sole account in this single-tenant deployment (evan@eross.com)
_FETCH_INTERVAL_SECONDS = 1.0
_FETCH_TIMEOUT_SECONDS = 15
_FETCH_MAX_BYTES = 2_000_000
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _fetch_jobgether_page_html(url: str) -> str:
    """Fetch a Jobgether offer page's HTML, retrying once on a rate-limit response."""
    req = Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    retried = False
    while True:
        try:
            with safe_urlopen(req, timeout=_FETCH_TIMEOUT_SECONDS) as resp:
                raw = resp.read(_FETCH_MAX_BYTES)
                charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
        except UrllibHTTPError as exc:
            if exc.code != 429 or retried:
                raise
            retried = True
            delay = rate_limit_backoff_seconds(
                exc.headers.get("Retry-After") if exc.headers else None
            )
            logger.warning("Rate-limited (HTTP 429) for %s — retrying in %.0fs", url, delay)
            time.sleep(delay)


def _candidates(repos: dict) -> list[dict]:
    jobs = repos["jobs"].get_all_ordered(_USER_ID)
    return [
        job
        for job in jobs
        if str(job.get("company") or "").strip().casefold() == "jobgether"
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
                html = _fetch_jobgether_page_html(url)
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

            logger.info("  company: 'Jobgether' -> %r", updates["company"])
            if not args.dry_run:
                repos["jobs"].update(_USER_ID, job_id, updates)
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
