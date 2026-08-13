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
"""JD text selection, enrichment, and re-scoring."""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.db import IJobRepository, IPenaltyRuleRepository, IResumeRepository
from backend.db.job_columns import JobColumns
from backend.scoring.fit_scorer import FitScorer
from backend.utils.utils import ACTIVE_JOB_STATUSES

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


def enrich_job_description(
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
    repos["jobs"].update(job_id, update_fields)
    try:
        result = FitScorer().score(
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
        }
        if current_status not in {"skipped", "applied"}:
            score_updates["status"] = "pending_review" if score >= 70 else "triage"
        repos["jobs"].update(job_id, score_updates)
    except Exception as exc:
        logger.warning("Re-scoring failed for enriched job %d: %s", job_id, exc)


def rescore_active_queue_jobs(repos: dict[str, object]) -> dict[str, int]:
    """Re-score active queue jobs after candidate profile changes."""
    jobs_repo: IJobRepository = repos["jobs"]  # type: ignore[assignment]
    resume_repo: IResumeRepository = repos["resume"]  # type: ignore[assignment]
    penalties_repo: IPenaltyRuleRepository = repos["penalties"]  # type: ignore[assignment]

    queue_jobs = jobs_repo.get_by_statuses(list(ACTIVE_JOB_STATUSES))
    resume_row = resume_repo.get_latest()
    resume_text: str = resume_row["content"] if resume_row else ""
    penalty_rules: list[dict] = penalties_repo.get_enabled()
    fit_feedback: list[dict] = jobs_repo.get_not_a_fit_with_feedback()
    scorer = FitScorer()

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
            jobs_repo.update(
                job["id"],
                {
                    "fit_score": result["score"],
                    "fit_reasoning": result["reasoning"],
                    "salary_min": result["salary_min"],
                    "salary_max": result["salary_max"],
                },
            )
            repos["conn"].commit()
            rescored += 1
        except Exception as exc:
            repos["conn"].rollback()
            logger.warning(
                "Queue re-score failed for job %s: %s",
                job.get("id"),
                exc,
                exc_info=True,
            )
            failed += 1

    return {"rescored": rescored, "failed": failed}
