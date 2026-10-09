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

# ai: claude-sonnet-5 | 2026-10-08
"""One-off maintenance script: clear duplicate_of_id links that now point at
a different company than the job itself.

Concrete case this cleans up: a batch of Jobgether postings was originally
ingested with company == "Jobgether" (the aggregator's own domain, mistaken
for the employer -- see import_repair.is_jobgether_placeholder_company).
While every Jobgether row still shared that literal placeholder company,
DuplicateChecker scored company similarity at 100% for any two of them, so
generic titles like "Senior Software Engineer" cross-matched completely
unrelated employers (GitHub, Reddit, Penn Interactive, Docker, ... all got
flagged as "possible duplicate of" a handful of anchor jobs). The company
field was later repaired to the real employer (scripts/repair_jobgether_
companies.py), but that script never re-ran duplicate detection, so jobs
were left with a corrected company and a now-nonsensical duplicate_of_id
still pointing at an unrelated company's listing. Confirmed live via job
5606 (Penn Interactive) still pointing at job 5555 (Gametime) -- and 5606
had already been applied to by the time this script was written, which is
exactly why the status-reset step below is gated the way it is.

Deliberately narrow and clear-only (does NOT re-run the fuzzy matcher to
find a replacement match): an earlier version of this script re-ran
DuplicateChecker.check() against the full ~6000-row job list for every
flagged job and proposed "re-pointing" thousands of already-correct
duplicate pairs (e.g. two genuinely duplicate postings swapping which one
pointed at which) purely because the matcher's best-match pick shifts
across a full re-scan. That's far more churn than the actual bug warrants.
This script instead only asks one question per flagged job: does the job's
current company still fuzzy-match (duplicate_checker.companies_match) its
duplicate target's current company? If yes, the link is left completely
alone, no matter how the matcher would score things today. If no, the link
is stale:
  - duplicate_hint/duplicate_of_id are always cleared -- they're
    descriptive/pointer fields, not user progress, so there's nothing to
    protect by leaving them.
  - status is only reset back to pending_review/triage while the job is
    still sitting in "possible_duplicate" -- the state this exact bug chain
    put it in. A job the user has since applied to, skipped, etc. keeps its
    real status; only the stale pointer/hint are cleaned up underneath it.

Usage:
    python scripts/revalidate_duplicate_links.py          # apply fixes
    python scripts/revalidate_duplicate_links.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.scoring.duplicate_checker import companies_match  # noqa: E402
from backend.utils.utils import get_repos  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
)
logger = logging.getLogger(__name__)

_USER_ID = 1  # sole account in this single-tenant deployment (evan@eross.com)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Report planned changes without writing them."
    )
    args = parser.parse_args()

    with get_repos(_USER_ID) as repos:
        all_jobs = repos["jobs"].get_all_ordered(_USER_ID)
        by_id = {int(j["id"]): j for j in all_jobs}
        flagged = [j for j in all_jobs if j.get("duplicate_of_id") is not None]
        logger.info("Found %d job(s) with a duplicate_of_id set", len(flagged))

        pointer_cleared = 0
        status_reset = 0
        dangling = 0

        for job in flagged:
            job_id = int(job["id"])
            target_id = int(job["duplicate_of_id"])
            target = by_id.get(target_id)
            if target is None:
                dangling += 1
                logger.warning("job %d: duplicate_of_id #%d does not exist -- leaving as-is", job_id, target_id)
                continue

            if companies_match(str(job.get("company") or ""), str(target.get("company") or "")):
                continue  # still a valid same-company match -- leave alone

            logger.info(
                "job %d (%s @ %s): stale link to #%d (%s @ %s) -- CLEAR pointer/hint%s",
                job_id, job.get("job_title"), job.get("company"),
                target_id, target.get("job_title"), target.get("company"),
                " + reset status" if job.get("status") == "possible_duplicate" else "",
            )
            if args.dry_run:
                pointer_cleared += 1
                if job.get("status") == "possible_duplicate":
                    status_reset += 1
                continue

            # Pointer/hint are descriptive, not user progress -- always safe to clear.
            repos["jobs"].update(_USER_ID, job_id, {"duplicate_hint": None, "duplicate_of_id": None})
            repos["conn"].commit()
            pointer_cleared += 1

            # Status reset only applies while the job is still sitting in the
            # possible_duplicate state this stale link put it in; guarded
            # atomically against a concurrent /apply or /skip in this window.
            fit_score = job.get("fit_score") or 0
            updated = repos["jobs"].update_if_status_in(
                _USER_ID,
                job_id,
                {"status": "pending_review" if fit_score >= 70 else "triage"},
                {"possible_duplicate"},
            )
            if updated is not None:
                repos["conn"].commit()
                status_reset += 1

        logger.info(
            "Done. pointer_cleared=%d status_reset=%d dangling=%d%s",
            pointer_cleared, status_reset, dangling,
            " (dry run -- no writes made)" if args.dry_run else "",
        )


if __name__ == "__main__":
    main()
