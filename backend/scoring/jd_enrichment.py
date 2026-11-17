# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-08-13
"""JD text selection, enrichment, and re-scoring."""

from __future__ import annotations

import logging
import time
from http.client import HTTPException
from typing import Any
from urllib.parse import urlparse

from backend.db import (
    CaptureQueueOrigin,
    IJobRepository,
    IPenaltyRuleRepository,
    IResumeRepository,
)
from backend.db.job_columns import JobColumns
from backend.scanning.career_site_scanner import CareerSiteScanner
from backend.scoring.fit_scorer import FitScorer
from backend.scoring.jd_limits import RAW_JD_SNIPPET_MAX_CHARS
from backend.scoring.job_validator import (
    SCORING_FAILED_WARNING,
    TRACKING_LINK_JD_WARNING,
    has_import_warning,
    remove_import_warning,
)
from backend.scoring.workplace_type import ensure_workplace_type_preserved
from backend.utils.utils import (
    ACTIVE_JOB_STATUSES,
    USER_COMMITTED_JOB_STATUSES,
    get_repos,
)

logger = logging.getLogger(__name__)

_ENRICH_MIN_CHARS = 300
_ENRICH_ADVANTAGE_CHARS = 300


# ai: claude-sonnet-5 | 2026-08-12
def job_description_text(job: dict[str, Any]) -> str:
    """Return the best available JD text for *job*.

    job_description holds the enriched/full text when a re-scan has found one;
    raw_jd_snippet holds whatever was captured on initial ingest. Prefer
    job_description when present, else fall back to the snippet.
    """
    return str(
        job.get(JobColumns.JOB_DESCRIPTION) or job.get(JobColumns.RAW_JD_SNIPPET) or ""
    ).strip()


# ai: claude-sonnet-5 | 2026-08-12
def scoring_text_for_job(job: dict[str, Any]) -> str:
    """Return the text to feed the fit scorer for *job*.

    Prefers the enriched job_description when present (it's the fullest
    text available). Otherwise falls back to the captured snippet joined
    with the capture label (a real email subject line for email-sourced
    jobs, a synthetic description otherwise), since that label alone
    carries useful title/context signal the scorer shouldn't lose.
    """
    description = job.get(JobColumns.JOB_DESCRIPTION)
    if description:
        return str(description)
    return "\n".join(
        part
        for part in [
            job.get(JobColumns.CAPTURE_LABEL, ""),
            job.get(JobColumns.RAW_JD_SNIPPET, ""),
        ]
        if part
    )


def is_richer_description(incoming_text: str, existing_job: dict[str, Any]) -> bool:
    """Return True if incoming_text is substantially richer than the stored description."""
    if len(incoming_text) < _ENRICH_MIN_CHARS:
        return False
    existing = job_description_text(existing_job)
    return len(incoming_text) > len(existing) + _ENRICH_ADVANTAGE_CHARS


# ai: claude-opus-5-5 | 2026-10-13
def unenriched_skip_reason(
    incoming_len: int, existing_job: dict[str, Any]
) -> tuple[str, bool]:
    """Explain why a re-captured existing job's description wasn't updated.

    Returns (reason, description_gap). description_gap is True only when the
    stored JD is itself below the usable minimum, i.e. the job still lacks a
    real description and nothing this import found could fill it. A short
    capture alone isn't a gap: rescanning a search/list page yields only
    ~200-char cards for jobs whose full JD is already stored — the
    2026-10-13 LinkedIn listener rescan logged 22 such false alarms.
    """
    existing_len = len(job_description_text(existing_job))
    if existing_len >= _ENRICH_MIN_CHARS:
        return (
            f"Job already in database with a full description ({existing_len} "
            f"chars); captured content ({incoming_len} chars) was not richer.",
            False,
        )
    if incoming_len < _ENRICH_MIN_CHARS:
        return (
            f"Captured content too short to update description ({incoming_len} "
            f"chars captured; {_ENRICH_MIN_CHARS} required). The page may not "
            f"have fully rendered, or the link may have expired.",
            True,
        )
    return (
        f"Captured content ({incoming_len} chars) was not significantly richer "
        f"than the stored description ({existing_len} chars); need "
        f"{existing_len + _ENRICH_ADVANTAGE_CHARS + 1} chars to trigger an update.",
        True,
    )


def enrich_job_description(
    user_id: int,
    repos: dict[str, Any],
    job: dict[str, Any],
    description: str,
    *,
    resume_text: str,
    penalty_rules: list[dict],
    fit_feedback: list[dict],
) -> None:
    """Overwrite a job's description and re-score it against the candidate profile.

    Does not change status for applied or skipped jobs.
    """
    job_id = int(job["id"])
    # ai: claude-opus-5-5 | 2026-11-13 -- description/warning fields now shared
    # with refresh_job_description_from_url via description_fields.
    update_fields = description_fields(job, description)
    repos["jobs"].update(user_id, job_id, update_fields)
    try:
        result = FitScorer(user_id).score(
            job_title=str(job.get("job_title") or ""),
            company=str(job.get("company") or ""),
            email_body=description,
            resume_text=resume_text,
            penalty_rules=penalty_rules,
            fit_feedback=fit_feedback,
        )
        score = int(result["score"])
        score_updates: dict[str, Any] = {
            "fit_score": score,
            "fit_reasoning": result["reasoning"],
            "salary_min": result["salary_min"],
            "salary_max": result["salary_max"],
            # ai: claude-sonnet-5 | 2026-09-21 — update_fields may already hold the
            # tracking-link-cleaned value; job's copy is stale in that case.
            "import_warnings": remove_import_warning(
                update_fields[JobColumns.IMPORT_WARNINGS], SCORING_FAILED_WARNING
            ),
        }
        # ai: claude-opus-5-5 | 2026-11-13 -- was its own {"skipped", "applied"}
        # copy, which let enrichment pull a possible_duplicate out of review.
        new_status = status_after_rescore(str(job.get("status") or ""), score)
        if new_status is not None:
            score_updates["status"] = new_status
        repos["jobs"].update(user_id, job_id, score_updates)
    except Exception as exc:
        logger.warning("Re-scoring failed for enriched job %d: %s", job_id, exc)


# ai: claude-opus-5-5 | 2026-11-13
# Statuses a re-score must leave alone: the user's own Apply/Skip decisions,
# and a pending duplicate review (a better JD doesn't settle whether the job
# is a duplicate).
_RESCORE_KEEPS_STATUS: frozenset[str] = USER_COMMITTED_JOB_STATUSES | {"possible_duplicate"}


# ai: claude-opus-5-5 | 2026-11-13
def status_after_rescore(current_status: str, score: int) -> str | None:
    """The queue/triage status a fresh fit score puts a job in, or None when
    the job's current status must be kept."""
    if current_status in _RESCORE_KEEPS_STATUS:
        return None
    return "pending_review" if score >= 70 else "triage"


# ai: claude-opus-5-5 | 2026-11-13
def description_fields(job: dict[str, Any], description: str) -> dict[str, Any]:
    """Columns to write when *description* becomes the job's JD. A real
    description resolves the tracking-link warning; other warnings stay."""
    return {
        JobColumns.JOB_DESCRIPTION: description,
        JobColumns.RAW_JD_SNIPPET: description[:RAW_JD_SNIPPET_MAX_CHARS],
        JobColumns.IMPORT_WARNINGS: remove_import_warning(
            job.get(JobColumns.IMPORT_WARNINGS), TRACKING_LINK_JD_WARNING
        ),
    }


# ai: claude-opus-5-5 | 2026-11-17 -- shared by the import repair, the
# broken-import rescan and the per-email rescan; only the first used to queue.
def queue_tracking_link_jd_capture(
    repos: dict[str, Any],
    user_id: int,
    job: dict[str, Any],
    *,
    origin: CaptureQueueOrigin,
    origin_detail: str,
) -> bool:
    """Queue *job*'s URL for the browser extension when its JD is only a tracking link.

    The posting renders its JD with JavaScript or behind a sign-in, so a plain
    fetch can't get it; the capture pipeline matches the capture back to the job
    by URL and patches job_description in place. Writes through *repos*; the
    caller commits. Returns whether a capture was queued: False when the job has
    no tracking-link warning, no http(s) URL to capture, or is skipped (nobody
    reads a skipped job's JD, and each capture opens a browser tab).
    """
    if str(job.get("status") or "") == "skipped":
        return False
    if not has_import_warning(job.get(JobColumns.IMPORT_WARNINGS), TRACKING_LINK_JD_WARNING):
        return False
    capture_url = str(job.get("url") or "").strip()
    if not capture_url.lower().startswith(("http://", "https://")):
        return False
    site_name = str(job.get("source") or "").strip() or urlparse(capture_url).netloc
    repos["browser_capture_queue"].enqueue(
        user_id, capture_url, site_name, origin=origin, origin_detail=origin_detail
    )
    return True


# ai: claude-opus-5-5 | 2026-11-13
# Below this the fetch hit a login wall, an error page, or a client-rendered
# shell; the caller decides the fallback (the route queues a browser capture).
_MIN_REFRESHED_TEXT_CHARS = 150


class JobNotFoundError(LookupError):
    """No job with that id for this user."""


class JobHasNoUrlError(ValueError):
    """The job has no stored URL to re-fetch from."""


# ai: claude-opus-5-5 | 2026-11-13 -- extracted from routes/jobs.py's
# refresh_job_from_url route so scripts can reuse it. The route only wrote
# raw_jd_snippet, so a stale job_description (which every reader prefers) kept
# winning over the fresh fetch; this writes both via description_fields.
def refresh_job_description_from_url(user_id: int, job_id: int) -> dict[str, Any] | None:
    """Re-fetch a job's JD from its stored URL, re-score it, and save both.

    Returns the updated job, or None when the fetch yielded too little text to
    use (nothing is written then). Raises JobNotFoundError / JobHasNoUrlError;
    a scoring failure propagates unchanged. No DB connection is held across the
    fetch or the AI call. The status write is conditional, so an Apply/Skip
    that lands mid-refresh is never overwritten.
    """
    with get_repos(user_id) as repos:
        job = repos["jobs"].get_by_id(user_id, job_id)
        if not job:
            raise JobNotFoundError(f"Job {job_id} not found")
        url = str(job.get("url") or "").strip()
        if not url:
            raise JobHasNoUrlError(f"Job {job_id} has no URL")
        resume_row = repos["resume"].get_latest(user_id)
        resume_text = str(resume_row.get("content") or "") if resume_row else ""
        penalty_rules = repos["penalties"].get_enabled(user_id)
        fit_feedback = repos["jobs"].get_not_a_fit_with_feedback(user_id)

    t0 = time.monotonic()
    try:
        fetched = CareerSiteScanner().scan_single_job_url(str(job.get("source") or ""), url)
    # URLError, timeouts and SSL errors are OSErrors; UnsafeUrlError is a ValueError.
    except (ValueError, OSError, HTTPException) as exc:
        logger.warning("refresh_job_description_from_url: fetch failed for job %d (%s): %s", job_id, url, exc)
        return None
    # ai: claude-sonnet-5 | 2026-08-21 (moved from routes/jobs.py)
    # LinkedIn's fetched JD (guest jobs API) never carries the Hybrid/Remote/
    # On-site top-card badge -- recover it from whatever text was already
    # stored (email digest snippet, prior DOM capture) before it's replaced.
    new_text = ensure_workplace_type_preserved(fetched["job_text"], job_description_text(job))
    logger.info(
        "refresh_job_description_from_url: job %d fetched %d chars in %.1fs",
        job_id,
        len(new_text),
        time.monotonic() - t0,
    )
    if len(new_text) < _MIN_REFRESHED_TEXT_CHARS:
        return None

    score_result = FitScorer(user_id).score(
        job_title=str(job.get("job_title") or ""),
        company=str(job.get("company") or ""),
        email_body=new_text,
        resume_text=resume_text,
        penalty_rules=penalty_rules,
        fit_feedback=fit_feedback,
    )
    fit_score = int(score_result["score"])

    new_fields = description_fields(job, new_text)
    with get_repos(user_id) as repos:
        # ai: claude-sonnet-4-6 | 2026-06-26 (moved from routes/jobs.py)
        # Certainty describes ingestion-time parsing and is stale once the JD
        # has been fetched directly; keeping it showed "Certainty score: 45% /
        # company name missing" on a fully refreshed job.
        repos["jobs"].update(
            user_id,
            job_id,
            {
                **new_fields,
                "fit_score": fit_score,
                "fit_reasoning": score_result["reasoning"],
                "salary_min": score_result["salary_min"],
                "salary_max": score_result["salary_max"],
                JobColumns.IMPORT_WARNINGS: remove_import_warning(
                    new_fields[JobColumns.IMPORT_WARNINGS], SCORING_FAILED_WARNING
                ),
                JobColumns.CERTAINTY_LEVEL: None,
                JobColumns.CERTAINTY_SCORE: None,
                JobColumns.CERTAINTY_REASON: None,
            },
        )
        new_status = status_after_rescore(str(job.get("status") or ""), fit_score)
        if new_status is not None:
            repos["jobs"].update_if_status_not_in(
                user_id, job_id, {"status": new_status}, _RESCORE_KEEPS_STATUS
            )
        updated_job = repos["jobs"].get_by_id(user_id, job_id)
        repos["conn"].commit()
    return updated_job


# ai: claude-opus-5-5 | 2026-10-28 -- opens its own short-lived connections
# instead of borrowing the caller's: every score() is an AI call, and holding
# one pooled connection across all of them could exhaust the pool.
def rescore_active_queue_jobs(user_id: int) -> dict[str, int]:
    """Re-score active queue jobs after candidate profile changes. The caller
    must commit the profile change first -- the scorer reads the profile
    through its own connection."""
    with get_repos(user_id) as repos:
        jobs_repo: IJobRepository = repos["jobs"]
        resume_repo: IResumeRepository = repos["resume"]
        penalties_repo: IPenaltyRuleRepository = repos["penalties"]

        queue_jobs = jobs_repo.get_by_statuses(user_id, list(ACTIVE_JOB_STATUSES))
        resume_row = resume_repo.get_latest(user_id)
        resume_text: str = resume_row["content"] if resume_row else ""
        penalty_rules: list[dict] = penalties_repo.get_enabled(user_id)
        fit_feedback: list[dict] = jobs_repo.get_not_a_fit_with_feedback(user_id)
    scorer = FitScorer(user_id)

    rescored = 0
    failed = 0

    for job in queue_jobs:
        try:
            # ai: claude-sonnet-5 | 2026-08-12 — was raw_jd_snippet-only, silently
            # ignoring any enriched job_description; now matches re_evaluate_job_fit.
            scoring_text = scoring_text_for_job(job)
            result = scorer.score(
                job_title=job.get("job_title", ""),
                company=job.get("company", ""),
                email_body=scoring_text,
                resume_text=resume_text,
                penalty_rules=penalty_rules,
                fit_feedback=fit_feedback,
            )
            # ai: claude-opus-5-5 | 2026-10-28 -- a failed write is discarded when
            # get_repos closes the uncommitted connection, so no explicit rollback.
            with get_repos(user_id) as repos:
                repos["jobs"].update(
                    user_id,
                    job["id"],
                    {
                        "fit_score": result["score"],
                        "fit_reasoning": result["reasoning"],
                        "salary_min": result["salary_min"],
                        "salary_max": result["salary_max"],
                        # ai: claude-sonnet-5 | 2026-09-21
                        "import_warnings": remove_import_warning(
                            job.get("import_warnings"), SCORING_FAILED_WARNING
                        ),
                    },
                )
                repos["conn"].commit()
            rescored += 1
        except Exception as exc:
            logger.warning(
                "Queue re-score failed for job %s: %s",
                job.get("id"),
                exc,
                exc_info=True,
            )
            failed += 1

    return {"rescored": rescored, "failed": failed}
