# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================
"""Re-judge certainty for jobs whose stored reason contradicts their own fields.

Until 2026-11-19 the parser judged a browser-extension capture's certainty from
its listing text alone, never seeing the company/title/URL the extension sent
alongside it, and repairs that later filled a blank company never revisited the
judgment. ~1,030 jobs ended up with reasons like "company name is missing or
ambiguous" next to a stored company (job 8335, "Lockheed Martin"), and with the
low certainty_score that sorts them down the Job Queue.

A candidate is a job whose certainty_reason says something is missing/unclear
and names a company, title or URL field that the job does have. Each one is
re-judged through backend.scoring.certainty_rejudge, the same path repairs now
queue: only certainty_level/score/reason are written, and only if the job's
company/title/URL haven't changed since it was read.

Usage:
    python scripts/rejudge_stale_certainty.py              # dry run: re-judge a sample, write nothing
    python scripts/rejudge_stale_certainty.py --sample 25
    python scripts/rejudge_stale_certainty.py --apply      # re-judge and store every candidate
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.ai_provider import AIProviderError  # noqa: E402
from backend.db.job_columns import JobColumns  # noqa: E402
from backend.logging_setup import configure_logging  # noqa: E402
from backend.scoring.certainty_rejudge import (  # noqa: E402
    CertaintyRejudger,
    rejudge_job_certainty,
)
from backend.utils.utils import get_repos  # noqa: E402

configure_logging()
logger = logging.getLogger(__name__)

_USER_ID = 1  # sole account in this single-tenant deployment
# Concurrent LLM calls. Each worker opens its own short-lived connections.
_MAX_WORKERS = 4

_CLAIMS_MISSING = re.compile(
    r"missing|ambiguous|unclear|unknown|absent|lack"
    r"|not (clearly )?(specified|mentioned|stated|provided|given|identified|present)"
    r"|no (explicit )?(company|url|title|job title|apply)",
    re.IGNORECASE,
)
_FIELD_MENTIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (JobColumns.COMPANY, re.compile(r"company|employer", re.IGNORECASE)),
    (JobColumns.JOB_TITLE, re.compile(r"title|role", re.IGNORECASE)),
    (JobColumns.URL, re.compile(r"url|link", re.IGNORECASE)),
)


# ai: claude-opus-5-5 | 2026-11-19
def contradicts_own_fields(job: dict[str, Any]) -> bool:
    reason = str(job.get(JobColumns.CERTAINTY_REASON) or "")
    if not _CLAIMS_MISSING.search(reason):
        return False
    return any(
        pattern.search(reason) and str(job.get(column) or "").strip()
        for column, pattern in _FIELD_MENTIONS
    )


# ai: claude-opus-5-5 | 2026-11-19
def _dry_run(candidates: list[dict[str, Any]], sample: int) -> None:
    rejudger = CertaintyRejudger(_USER_ID)
    for job in candidates[:sample]:
        try:
            judgment = rejudger.judge(job)
        except (AIProviderError, ValueError) as exc:
            logger.warning("job %d: re-judge failed: %s", job["id"], exc)
            continue
        logger.info(
            "job %d (%s / %s)\n  old: %s/%s %s\n  new: %s",
            job["id"],
            job.get(JobColumns.COMPANY),
            job.get(JobColumns.JOB_TITLE),
            job.get(JobColumns.CERTAINTY_LEVEL),
            job.get(JobColumns.CERTAINTY_SCORE),
            job.get(JobColumns.CERTAINTY_REASON),
            f"{judgment.level}/{judgment.score} {judgment.reason}"
            if judgment
            else "(no judgment -- left unchanged)",
        )


# ai: claude-opus-5-5 | 2026-11-19
def _apply(candidates: list[dict[str, Any]]) -> None:
    rejudger = CertaintyRejudger(_USER_ID)
    stored = not_stored = failed = 0
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {
            pool.submit(rejudge_job_certainty, _USER_ID, int(job["id"]), rejudger): int(job["id"])
            for job in candidates
        }
        for done, future in enumerate(as_completed(futures), start=1):
            job_id = futures[future]
            try:
                if future.result():
                    stored += 1
                else:
                    not_stored += 1
            except (AIProviderError, ValueError) as exc:
                failed += 1
                logger.warning("job %d: re-judge failed: %s", job_id, exc)
            if done % 50 == 0:
                logger.info("%d/%d done", done, len(candidates))
    logger.info(
        "Done. stored=%d not_stored=%d (no judgment, or job changed meanwhile) failed=%d",
        stored,
        not_stored,
        failed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Re-judge and store every candidate."
    )
    parser.add_argument(
        "--sample", type=int, default=10, help="Dry run: how many candidates to re-judge."
    )
    args = parser.parse_args()

    with get_repos(_USER_ID) as repos:
        candidates = [
            job for job in repos["jobs"].get_all_ordered(_USER_ID) if contradicts_own_fields(job)
        ]
    logger.info("Found %d job(s) whose certainty reason contradicts their own fields", len(candidates))

    if args.apply:
        _apply(candidates)
    else:
        _dry_run(candidates, args.sample)
        logger.info("Dry run -- nothing written. Re-run with --apply to store.")


if __name__ == "__main__":
    main()
