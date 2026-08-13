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
"""Duplicate/tracking match detection against known jobs already in the DB.

Named duplicate_matching.py (not matching.py) to avoid confusion with the
unrelated backend/scoring/company_matching.py (LinkedIn-contact company-name
normalization)."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from backend.scoring.duplicate_checker import DuplicateChecker

# Thresholds for treating a duplicate match as near-certain enough to auto-skip
# instead of routing to the triage queue for manual review. Stricter than
# DuplicateChecker's own thresholds (80/70), which only flag a "possible duplicate".
AUTO_REJECT_DUPLICATE_TITLE_THRESHOLD: int = 95
AUTO_REJECT_DUPLICATE_COMPANY_THRESHOLD: int = 95


def extract_ashby_job_id(url: str) -> str | None:
    """Return the Ashby job ID from an ashbyhq.com URL, or None for any other URL."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        if "ashbyhq.com" not in parsed.netloc.lower():
            return None
        params = parse_qs(parsed.query)
        job_id = (params.get("jobId") or params.get("ashby_jid") or [None])[0]
        return job_id.strip() if isinstance(job_id, str) and job_id.strip() else None
    except Exception:
        return None


def extract_linkedin_job_id(url: str) -> str | None:
    """Return the LinkedIn numeric job ID from a linkedin.com jobs URL, or None.

    LinkedIn sends different tracking URLs for the same posting across emails
    (different trackingId, refId, etc.) but the job ID in the path is stable:
      /jobs/view/4385602723/  →  "4385602723"
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
        if "linkedin.com" not in parsed.netloc.lower():
            return None
        m = re.search(r"/jobs/view/(\d+)", parsed.path)
        return m.group(1) if m else None
    except Exception:
        return None


# ai: claude-sonnet-4-6 | 2026-06-10
def db_match_for_job(
    *,
    job_title: str,
    company: str,
    job_url: str,
    known_jobs: list[dict],
    dedup_checker: DuplicateChecker,
    exclude_id: int | None = None,
) -> tuple[str | None, str | None, str | None, int | None, dict[str, int] | None]:
    """Check known_jobs for an exact URL match or fuzzy title/company duplicate.

    Returns (tracked_status, source_key, duplicate_hint, matched_job_id, match_scores),
    all None when no match. matched_job_id is the DB id of the job that this is a
    duplicate of, when available. match_scores holds the rapidfuzz "title_score" and
    "company_score" for db_fuzzy matches (None for db_url matches and non-matches).
    Duplicates are always flagged regardless of the original job's current status.
    """
    current_job_url = job_url.strip()
    current_ashby_job_id = extract_ashby_job_id(current_job_url)
    current_linkedin_job_id = extract_linkedin_job_id(current_job_url)

    exact_match = next(
        (
            row
            for row in known_jobs
            if (exclude_id is None or row.get("id") != exclude_id)
            and (row.get("url") or "").strip()
            and (
                (row.get("url") or "").strip().lower() == current_job_url.lower()
                or (
                    current_ashby_job_id is not None
                    and extract_ashby_job_id((row.get("url") or "").strip())
                    == current_ashby_job_id
                )
                or (
                    current_linkedin_job_id is not None
                    and extract_linkedin_job_id((row.get("url") or "").strip())
                    == current_linkedin_job_id
                )
            )
        ),
        None,
    )
    if exact_match is not None:
        tracked_status = (exact_match.get("status") or "").strip() or "Tracked"
        duplicate_hint = (
            f"Already tracked as \"{exact_match.get('job_title') or job_title}\" "
            f"at {exact_match.get('company') or company} with status \"{tracked_status}\"."
        )
        matched_id = exact_match.get("id")
        return (
            tracked_status,
            "db_url",
            duplicate_hint,
            (int(matched_id) if matched_id is not None else None),
            None,
        )

    if not company.strip() or not job_title.strip() or not known_jobs:
        return None, None, None, None, None

    fuzzy_candidates = [
        row for row in known_jobs if exclude_id is None or row.get("id") != exclude_id
    ]
    dup = dedup_checker.check(
        job_title=job_title,
        company=company,
        sheet_jobs=fuzzy_candidates,
    )
    if dup is None:
        return None, None, None, None, None

    matched = next(
        (
            row
            for row in fuzzy_candidates
            if (row.get("company") or "") == dup["sheet_company"]
            and (row.get("job_title") or "") == dup["sheet_title"]
        ),
        None,
    )
    tracked_status = ((matched or {}).get("status") or "Tracked").strip()
    duplicate_hint = (
        f"Possible duplicate of \"{dup['sheet_title']}\" "
        f"at {dup['sheet_company']} already tracked "
        f"(title match: {dup['title_score']}%, company match: {dup['company_score']}%)"
    )
    matched_id = matched.get("id") if matched else None
    match_scores: dict[str, int] = {
        "title_score": dup["title_score"],
        "company_score": dup["company_score"],
    }
    return (
        tracked_status,
        "db_fuzzy",
        duplicate_hint,
        (int(matched_id) if matched_id is not None else None),
        match_scores,
    )


def is_near_certain_duplicate(
    tracking_source: str | None, match_scores: dict[str, int] | None
) -> bool:
    """True when a duplicate match is confident enough to auto-skip without review.

    An exact URL/Ashby/LinkedIn job-id match (tracking_source == "db_url") is always
    near-certain. A fuzzy match (tracking_source == "db_fuzzy") is near-certain only
    when both the title and company similarity scores clear the auto-reject thresholds.
    """
    if tracking_source == "db_url":
        return True
    if tracking_source == "db_fuzzy" and match_scores:
        return (
            match_scores.get("title_score", 0) >= AUTO_REJECT_DUPLICATE_TITLE_THRESHOLD
            and match_scores.get("company_score", 0)
            >= AUTO_REJECT_DUPLICATE_COMPANY_THRESHOLD
        )
    return False
