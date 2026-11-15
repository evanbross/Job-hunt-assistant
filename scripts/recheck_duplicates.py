# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================
# ai: claude-opus-5-5 | 2026-11-13
"""Re-check duplicate links made by the old company matcher.

Until 2026-11-13 DuplicateChecker counted any shared word or one-letter
difference as the same company (Coast/Toast, Seeq Corporation/Corvel
Corporation, Scout/Help Scout), so existing rows carry duplicate links the
fixed matcher would never make. This re-checks every job currently linked as
a duplicate -- flagged possible_duplicate, or auto-skipped by the app as a
near-certain one -- plus, with --url-prefix, every job at those URLs.

Per job it uses the same decision as post_repair_duplicate_check
(backend.scanning.import_repair.plan_duplicate_recheck): a stale link is
cleared and the job returns to triage/queue; a job the app auto-skipped comes
back too; a pair the user dismissed is never re-flagged; a job the user
applied to or skipped keeps its status (only its duplicate pointer changes).

--preview-waas-renames (dry run only) applies scripts/repair_waas_companies.py's
cleaned names in memory first, to preview the result as if that repair had
finished.

Usage (PYTHONIOENCODING=utf-8: some company names carry emoji):
    PYTHONIOENCODING=utf-8 uv run python scripts/recheck_duplicates.py --dry-run \
        --url-prefix https://www.workatastartup.com/jobs/ --preview-waas-renames
    PYTHONIOENCODING=utf-8 uv run python scripts/recheck_duplicates.py \
        --url-prefix https://www.workatastartup.com/jobs/
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.logging_setup import configure_logging  # noqa: E402
from backend.scanning.import_repair import (  # noqa: E402
    DuplicateRecheck,
    plan_duplicate_recheck,
    post_repair_duplicate_check,
    workatastartup_company_name,
)
from backend.scoring.duplicate_matching import is_auto_duplicate_skip  # noqa: E402
from backend.utils.utils import get_repos  # noqa: E402

configure_logging()
logger = logging.getLogger(__name__)

_USER_ID = 1  # sole account in this single-tenant deployment
_WAAS_JOB_URL_PREFIX = "https://www.workatastartup.com/jobs/"


def _targets(all_jobs: list[dict[str, Any]], url_prefixes: list[str]) -> list[dict[str, Any]]:
    return [
        job
        for job in all_jobs
        if (
            job.get("duplicate_of_id") is not None
            and (job.get("status") == "possible_duplicate" or is_auto_duplicate_skip(job))
        )
        or any(str(job.get("url") or "").startswith(prefix) for prefix in url_prefixes)
    ]


def _describe(plan: DuplicateRecheck, job: dict[str, Any], by_id: dict[int, dict[str, Any]]) -> tuple[str, str]:
    def label(row: dict[str, Any] | None) -> str:
        return f"#{row['id']} {row.get('company')} | {row.get('job_title')}" if row else "-"

    old = by_id.get(job["duplicate_of_id"]) if job.get("duplicate_of_id") else None
    new = by_id.get(plan.duplicate_of_id) if plan.duplicate_of_id else None
    if plan.restores_auto_skip and new is None:
        kind = "auto-skip undone"
    elif new is None:
        kind = "link cleared"
    elif old is None:
        kind = "newly flagged"
    else:
        kind = "re-pointed"
    status = f"{job.get('status')} -> {plan.status}" if plan.status else f"{job.get('status')} (kept)"
    return kind, f"{label(job)}  [{status}]  was {label(old)}  now {label(new)}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Report planned changes without writing them.")
    parser.add_argument("--url-prefix", action="append", default=[], help="Also re-check every job whose URL starts with this.")
    parser.add_argument("--preview-waas-renames", action="store_true", help="Dry run only: clean WAAS company names in memory first.")
    args = parser.parse_args()
    if args.preview_waas_renames and not args.dry_run:
        parser.error("--preview-waas-renames only makes sense with --dry-run")

    with get_repos(_USER_ID) as repos:
        all_jobs = repos["jobs"].get_all_ordered(_USER_ID)
    if args.preview_waas_renames:
        for job in all_jobs:
            if str(job.get("url") or "").startswith(_WAAS_JOB_URL_PREFIX):
                job["company"] = workatastartup_company_name(str(job.get("company") or ""))
    by_id = {int(job["id"]): job for job in all_jobs}
    targets = _targets(all_jobs, args.url_prefix)
    logger.info("Re-checking %d job(s) of %d", len(targets), len(all_jobs))

    counts: Counter[str] = Counter()
    for job in targets:
        plan = plan_duplicate_recheck(job, all_jobs)
        if plan is None:
            counts["unchanged"] += 1
            continue
        kind, detail = _describe(plan, job, by_id)
        counts[kind] += 1
        logger.info("%-16s %s", kind, detail)
        if args.dry_run:
            continue
        # Re-plan from a live read under the duplicate-check lock: the
        # snapshot above is only for choosing targets and for the report.
        with get_repos(_USER_ID) as repos:
            live_job = repos["jobs"].get_by_id(_USER_ID, int(job["id"]))
            if live_job and post_repair_duplicate_check(_USER_ID, repos["jobs"], live_job):
                repos["conn"].commit()

    logger.info(
        "Done. %s%s",
        ", ".join(f"{kind}={n}" for kind, n in sorted(counts.items())),
        " (dry run -- no writes made)" if args.dry_run else "",
    )


if __name__ == "__main__":
    main()
