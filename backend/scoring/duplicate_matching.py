# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-08-13
"""Duplicate/tracking match detection against known jobs already in the DB.

Named duplicate_matching.py (not matching.py) to avoid confusion with the
unrelated backend/scoring/company_matching.py (LinkedIn-contact company-name
normalization)."""

from __future__ import annotations

import json
from collections.abc import Collection

from backend.scoring.duplicate_checker import DuplicateChecker
from backend.scoring.posting_identity import is_same_posting
from backend.scoring.skip_reasons import is_req_closed_job_row

# Thresholds for treating a duplicate match as near-certain enough to auto-skip
# instead of routing to the triage queue for manual review. Stricter than
# DuplicateChecker's own thresholds (80/70), which only flag a "possible duplicate".
AUTO_REJECT_DUPLICATE_TITLE_THRESHOLD: int = 95
AUTO_REJECT_DUPLICATE_COMPANY_THRESHOLD: int = 95


# ai: claude-opus-5 | 2026-09-23
def dismissed_duplicate_ids(job: dict) -> set[int]:
    """Job ids the user has explicitly dismissed as non-duplicates of *job*.

    Stored on the row as a JSON array by the dismiss-duplicate route. Returns an
    empty set for the unset/corrupt cases so callers can filter unconditionally.
    """
    try:
        raw = json.loads(job.get("dismissed_duplicate_ids") or "[]")
    except (TypeError, ValueError):
        return set()
    if not isinstance(raw, list):
        return set()
    ids: set[int] = set()
    for item in raw:
        try:
            ids.add(int(item))
        except (TypeError, ValueError):
            continue
    return ids


# ai: claude-sonnet-4-6 | 2026-06-10
def db_match_for_job(
    *,
    job_title: str,
    company: str,
    job_url: str,
    known_jobs: list[dict],
    dedup_checker: DuplicateChecker,
    exclude_id: int | None = None,
    dismissed_ids: Collection[int] | None = None,
) -> tuple[str | None, str | None, str | None, int | None, dict[str, int] | None]:
    """Check known_jobs for an exact URL match or fuzzy title/company duplicate.

    Returns (tracked_status, source_key, duplicate_hint, matched_job_id, match_scores),
    all None when no match. matched_job_id is the DB id of the job that this is a
    duplicate of, when available. match_scores holds the rapidfuzz "title_score" and
    "company_score" for db_fuzzy matches (None for db_url matches and non-matches).

    dismissed_ids are jobs the user has already said this one is *not* a duplicate
    of (see dismissed_duplicate_ids); they are dropped from both candidate lists so
    no later re-score can resurrect a match the user has settled.

    An exact URL/job-id match (see posting_identity.is_same_posting) is otherwise flagged regardless of the matched job's
    status — it is literally the same posting, and jobs.url carries a per-user unique
    index (jobs_url_unique_idx), so failing to recognise it would turn a re-import
    into a DuplicateJobUrlError rather than a clean "already tracked" result. Fuzzy
    matches are different: see the req-closed exclusion below.
    """
    skip_ids: set[int] = {int(i) for i in (dismissed_ids or ())}
    if exclude_id is not None:
        skip_ids.add(int(exclude_id))
    known_jobs = [row for row in known_jobs if row.get("id") not in skip_ids]

    current_job_url = job_url.strip()

    # ai: claude-opus-5-5 | 2026-10-29 -- is_same_posting covers the exact URL and
    # every site job ID posting_identity knows (Ashby, LinkedIn, Phenom req IDs).
    exact_match = next(
        (row for row in known_jobs if is_same_posting(row.get("url") or "", current_job_url)),
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

    # ai: claude-opus-5 | 2026-09-23
    # A closed req is a dead listing, so a *similar* new posting at the same
    # company is a fresh opening, not a duplicate of it — flagging it as one
    # buries a live job behind a triage card pointing at an expired req. (Seen
    # with "Sr. Full Stack Engineer" at Tapcart being matched to that company's
    # req-closed "Sr. React Native Engineer" on a 75% title score.) Only the
    # fuzzy path is filtered; an exact URL match is the same posting and must
    # still be recognised — see the docstring. DuplicateChecker.check skips
    # these rows too.
    fuzzy_candidates = [row for row in known_jobs if not is_req_closed_job_row(row)]
    dup = dedup_checker.check(
        job_title=job_title,
        company=company,
        job_url=current_job_url,
        sheet_jobs=fuzzy_candidates,
    )
    if dup is None:
        return None, None, None, None, None

    matched = dup["row"]  # ai: claude-opus-5-5 | 2026-10-29
    tracked_status = (matched.get("status") or "Tracked").strip()
    duplicate_hint = (
        f"Possible duplicate of \"{dup['sheet_title']}\" "
        f"at {dup['sheet_company']} already tracked "
        f"(title match: {dup['title_score']}%, company match: {dup['company_score']}%)"
    )
    matched_id = matched.get("id")
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
