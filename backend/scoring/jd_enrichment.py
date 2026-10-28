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

import json
import logging
from typing import Any

from backend.db import IJobRepository, IPenaltyRuleRepository, IResumeRepository
from backend.db.job_columns import JobColumns
from backend.scoring.fit_scorer import FitScorer
from backend.scoring.job_validator import SCORING_FAILED_WARNING, remove_import_warning
from backend.utils.utils import ACTIVE_JOB_STATUSES, get_repos

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
    update_fields: dict[str, Any] = {
        JobColumns.JOB_DESCRIPTION: description,
        JobColumns.RAW_JD_SNIPPET: description[:6000],
    }
    # ai: claude-sonnet-4-6 | 2026-07-20
    # A real description means the "jd_body_is_tracking_link" warning (raised
    # when the JD was empty and the snippet was just a tracking link) no
    # longer applies; other warning codes are unrelated to JD content and
    # stay untouched.
    try:
        existing_warnings = json.loads(job.get("import_warnings") or "[]")
    except (json.JSONDecodeError, TypeError):
        existing_warnings = []
    if "jd_body_is_tracking_link" in existing_warnings:
        remaining_warnings = [
            code for code in existing_warnings if code != "jd_body_is_tracking_link"
        ]
        update_fields["import_warnings"] = (
            json.dumps(remaining_warnings) if remaining_warnings else None
        )
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
        current_status = str(job.get("status") or "")
        score_updates: dict[str, Any] = {
            "fit_score": score,
            "fit_reasoning": result["reasoning"],
            "salary_min": result["salary_min"],
            "salary_max": result["salary_max"],
            # ai: claude-sonnet-5 | 2026-09-21 — update_fields may already hold the
            # tracking-link-cleaned value; job's copy is stale in that case.
            "import_warnings": remove_import_warning(
                update_fields.get("import_warnings", job.get("import_warnings")),
                SCORING_FAILED_WARNING,
            ),
        }
        if current_status not in {"skipped", "applied"}:
            score_updates["status"] = "pending_review" if score >= 70 else "triage"
        repos["jobs"].update(user_id, job_id, score_updates)
    except Exception as exc:
        logger.warning("Re-scoring failed for enriched job %d: %s", job_id, exc)


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
