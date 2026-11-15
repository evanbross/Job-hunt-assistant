# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================
# ai: claude-opus-5-5 | 2026-11-13
"""Strip the YC batch and tagline from Work at a Startup jobs' company names.

Until extension 1.34.1, content.js's buildWorkAtAStartupPayload stored the
listing's whole company line as the company -- "Nitrode (W25)•Frontier AI
research to advance game development" instead of "Nitrode" (181 jobs, e.g.
7663-7665). The import pipeline's own company repair can't fix these on a
re-capture: companies_match() treats the junk line and the clean name as the
same company, so it leaves the stored value alone.

No page fetch is needed: the real name is the stored value's own prefix, so
workatastartup_company_name() recovers it exactly. A company already corrected
by hand no longer carries the batch/tagline and is left untouched.

Duplicates are NOT re-checked here: run scripts/recheck_duplicates.py with
--url-prefix https://www.workatastartup.com/jobs/ afterwards. Re-checking each
job as it was renamed made the result depend on order -- a job could be
compared against a partner whose junk name hadn't been cleaned yet.

--refetch-jd also re-fetches the JD for every active (queue/triage) WAAS job
that has none -- 193 of 194 never got one, because the scanner couldn't read
Work at a Startup's client-rendered pages until 2026-11-13. It reuses the
triage card's Re-fetch path (refresh_job_description_from_url), so each job
is also re-scored (one AI call per job) and moves to the queue at >= 70.
Applied/skipped jobs are left out; their JD no longer drives anything.

Usage (PYTHONIOENCODING=utf-8 because some taglines carry emoji that the
Windows console's cp1252 can't print -- only the log line fails, not the row):
    PYTHONIOENCODING=utf-8 uv run python scripts/repair_waas_companies.py [--refetch-jd] --dry-run
    PYTHONIOENCODING=utf-8 uv run python scripts/repair_waas_companies.py [--refetch-jd]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db.job_columns import JobColumns  # noqa: E402
from backend.logging_setup import configure_logging  # noqa: E402
from backend.scanning.import_repair import workatastartup_company_name  # noqa: E402
from backend.scoring.jd_enrichment import refresh_job_description_from_url  # noqa: E402
from backend.utils.utils import ACTIVE_JOB_STATUSES, get_repos  # noqa: E402

configure_logging()
logger = logging.getLogger(__name__)

_USER_ID = 1  # sole account in this single-tenant deployment
_WAAS_JOB_URL_PREFIX = "https://www.workatastartup.com/jobs/"
_FETCH_INTERVAL_SECONDS = 1.0


def _waas_jobs(repos: dict) -> list[dict]:
    return [
        job
        for job in repos["jobs"].get_all_ordered(_USER_ID)
        if str(job.get("url") or "").startswith(_WAAS_JOB_URL_PREFIX)
    ]


def repair_companies(dry_run: bool) -> None:
    with get_repos(_USER_ID) as repos:
        repairs = []
        for job in _waas_jobs(repos):
            company = str(job.get("company") or "")
            cleaned = workatastartup_company_name(company)
            if cleaned and cleaned != company:
                repairs.append((job, cleaned))
        logger.info("Found %d Work at a Startup job(s) with a batch/tagline company", len(repairs))

        for job, cleaned in repairs:
            job_id = int(job["id"])
            logger.info("job %d [%s]: %r -> %r", job_id, job.get("status"), job.get("company"), cleaned)
            if dry_run:
                continue
            repos["jobs"].update(_USER_ID, job_id, {"company": cleaned})
            repos["conn"].commit()

    logger.info(
        "Companies done. repaired=%d%s",
        len(repairs),
        " (dry run -- no writes made)" if dry_run else "",
    )


def refetch_missing_jds(dry_run: bool) -> None:
    # Read the candidate list, then release the connection: each refresh opens
    # its own short-lived ones around the slow fetch and AI scoring call.
    with get_repos(_USER_ID) as repos:
        candidates = [
            job
            for job in _waas_jobs(repos)
            if job.get("status") in ACTIVE_JOB_STATUSES
            and not str(job.get(JobColumns.JOB_DESCRIPTION) or "").strip()
        ]
    logger.info("Found %d active Work at a Startup job(s) with no JD", len(candidates))

    counts = {"refreshed": 0, "to_queue": 0, "no_text": 0, "failed": 0}
    for i, job in enumerate(candidates, start=1):
        job_id = int(job["id"])
        label = f"[{i}/{len(candidates)}] job {job_id} [{job.get('status')}] {job.get('company')} - {job.get('job_title')}"
        if dry_run:
            logger.info("%s: would re-fetch %s", label, job.get("url"))
            continue
        if i > 1:
            time.sleep(_FETCH_INTERVAL_SECONDS)
        try:
            updated = refresh_job_description_from_url(_USER_ID, job_id)
        except Exception as exc:  # one bad job (AI error, DB blip) must not stop the batch
            logger.warning("%s: failed: %s", label, exc)
            counts["failed"] += 1
            continue
        if updated is None:
            logger.info("%s: page had no usable text (closed posting?)", label)
            counts["no_text"] += 1
            continue
        counts["refreshed"] += 1
        if updated.get("status") == "pending_review" and job.get("status") != "pending_review":
            counts["to_queue"] += 1
        logger.info(
            "%s: %d chars, fit %s -> %s, status -> %s",
            label,
            len(str(updated.get(JobColumns.JOB_DESCRIPTION) or "")),
            job.get("fit_score"),
            updated.get("fit_score"),
            updated.get("status"),
        )

    logger.info(
        "JDs done. refreshed=%d moved_to_queue=%d no_text=%d failed=%d%s",
        *counts.values(),
        " (dry run -- no writes made)" if dry_run else "",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Report planned changes without writing them."
    )
    parser.add_argument(
        "--refetch-jd",
        action="store_true",
        help="Also re-fetch and re-score active WAAS jobs that have no JD.",
    )
    args = parser.parse_args()

    repair_companies(args.dry_run)
    if args.refetch_jd:
        refetch_missing_jds(args.dry_run)


if __name__ == "__main__":
    main()
