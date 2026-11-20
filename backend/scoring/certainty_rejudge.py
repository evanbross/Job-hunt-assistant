# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================
"""Re-judge a stored job's certainty against the identity it has now.

A job's certainty_level/score/reason come from the parser at capture time. When
a repair later fills in a company or title the capture lacked, the stored
reason still says that field is missing (job 8335: "company name is missing or
ambiguous" next to company "Lockheed Martin"), and the stale low score keeps
sorting the job down the Job Queue. Re-running the same parser, with the job's
current company/title/URL as capture metadata, keeps one definition of
certainty (email_parser's prompt rules) instead of a second, drifting one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.db.job_columns import JobColumns
from backend.email.email_parser import EmailParser, KnownJobFields, best_parsed_match
from backend.scoring.jd_enrichment import job_description_text
from backend.utils.utils import get_repos

logger = logging.getLogger(__name__)

# The parser's "From:" line. Not a real sender, and deliberately not one that
# routes to a sender-specific shortcut parser (WTTJ, Matcha, Jobgether).
_REJUDGE_SENDER = "rejudge@local"


# ai: claude-opus-5-5 | 2026-11-19
@dataclass(frozen=True)
class CertaintyJudgment:
    level: str
    score: int
    reason: str

    def as_columns(self) -> dict[str, Any]:
        return {
            JobColumns.CERTAINTY_LEVEL: self.level,
            JobColumns.CERTAINTY_SCORE: self.score,
            JobColumns.CERTAINTY_REASON: self.reason,
        }


# ai: claude-opus-5-5 | 2026-11-19
class CertaintyRejudger:
    """Runs the parser over a stored job's own text and current identity."""

    def __init__(self, user_id: int, parser: EmailParser | None = None) -> None:
        self._parser = parser or EmailParser(user_id)

    def judge(self, job: dict[str, Any]) -> CertaintyJudgment | None:
        """The job's certainty as the parser sees it now, or None when there is
        nothing to judge (no stored text) or the parser found no posting in it,
        in which case the stored certainty is left as it is.

        Raises AIProviderError / ValueError from the parser like any parse.
        """
        text = job_description_text(job)
        if not text:
            return None
        parsed_jobs = self._parser.parse(
            str(job.get(JobColumns.CAPTURE_LABEL) or ""),
            _REJUDGE_SENDER,
            text,
            known=KnownJobFields(
                company=str(job.get(JobColumns.COMPANY) or "").strip(),
                job_title=str(job.get(JobColumns.JOB_TITLE) or "").strip(),
                url=str(job.get(JobColumns.URL) or "").strip(),
            ),
        )
        if not parsed_jobs:
            return None
        best = best_parsed_match(parsed_jobs)
        return CertaintyJudgment(
            level=best["certainty_level"],
            score=best["certainty_score"],
            reason=best["certainty_reason"],
        )


# ai: claude-opus-5-5 | 2026-11-19
def rejudge_job_certainty(user_id: int, job_id: int, rejudger: CertaintyRejudger) -> bool:
    """Re-judge one job and store the result; returns whether it was stored.

    Read, release, LLM call, reopen to write: the pooled connection is never
    held across the parser call (PYTHON_STANDARDS.md, connection lifetime).
    The write only lands if the job's identity is still what was judged.
    """
    with get_repos(user_id) as repos:
        job = repos["jobs"].get_by_id(user_id, job_id)
    if job is None:
        return False
    judgment = rejudger.judge(job)
    if judgment is None:
        return False
    with get_repos(user_id) as repos:
        applied = repos["jobs"].update_certainty_if_identity_unchanged(
            user_id, job_id, job, judgment.as_columns()
        )
        repos["conn"].commit()
    if not applied:
        logger.info("Certainty re-judge for job %d skipped: identity changed meanwhile", job_id)
    return applied
