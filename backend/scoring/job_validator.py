# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Pre-insert job validation and normalization.

Call normalize_and_validate() on every job_data dict before inserting into the
database.  It auto-fixes obvious data problems and returns a list of warning
codes for issues that require human review.  Any non-empty warning list should
cause the caller to:
  1. Force the job status to "triage" (unless already "possible_duplicate").
  2. Store the JSON-serialised warnings in the import_warnings column.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from backend.db.job_columns import JobColumns
from backend.email.email_parser import is_email_tracking_url

# ai: claude-opus-5-5 | 2026-11-15 -- moved to a leaf module; re-exported here
from backend.scoring.import_warning_codes import (
    NON_POSTING_URL_WARNING,
    SCORING_FAILED_WARNING,
    TRACKING_LINK_JD_WARNING,
    URL_OF_OTHER_JOB_WARNING,
)
from backend.scoring.posting_identity import is_non_posting_url
from backend.utils.company_urls import company_from_ats_url

__all__ = [
    "NON_POSTING_URL_WARNING",
    "SCORING_FAILED_WARNING",
    "TRACKING_LINK_JD_WARNING",
    "URL_OF_OTHER_JOB_WARNING",
    "has_import_warning",
    "import_warnings_to_json",
    "normalize_and_validate",
    "remove_import_warning",
]

logger = logging.getLogger(__name__)

# Matches "Company Name - Job Title" or "Company – Title" (en/em dash variants).
_COMPANY_IN_TITLE_RE = re.compile(r"^(.+?)\s+[-–—]\s+(.+)$")


def normalize_and_validate(
    job_data: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Normalize and validate a job dict before DB insertion.

    Returns (cleaned_job, warning_codes).  The cleaned_job is a shallow copy of
    job_data with text fields trimmed and auto-fixable issues corrected.  Warning
    codes are short strings like "homepage_url" — callers should treat a non-empty
    list as a signal to route the job to triage and persist the warnings.
    """
    job: dict[str, Any] = dict(job_data)
    warnings: list[str] = []

    # Trim whitespace on key text fields
    for field in ("company", "job_title", "url"):
        if isinstance(job.get(field), str):
            job[field] = job[field].strip()

    company = str(job.get("company") or "").strip()
    job_title = str(job.get("job_title") or "").strip()
    url = str(job.get("url") or "").strip()

    # Auto-fix: company name embedded in title as "Company - Job Title"
    if not company and job_title:
        match = _COMPANY_IN_TITLE_RE.match(job_title)
        if match:
            job["company"] = match.group(1).strip()
            job["job_title"] = match.group(2).strip()
            company = job["company"]
            job_title = job["job_title"]
            logger.debug(
                "Auto-split company from title: %r → %r / %r",
                job_data.get("job_title"),
                company,
                job_title,
            )

    # Flag: missing job title after normalization
    if not job_title:
        warnings.append("missing_job_title")

    # Auto-fix: company slug embedded in Ashby / Lever / Greenhouse URL path
    # ai: claude-sonnet-4-6 | 2026-06-18
    if not company:
        inferred = company_from_ats_url(url)
        if inferred:
            job["company"] = inferred
            company = inferred
            logger.debug("Auto-inferred company from ATS URL: %r → %r", url, inferred)

    # Flag: missing company after auto-fix attempts
    if not company:
        warnings.append("missing_company")

    # Flag: title contains "|" — characteristic of a browser page title, not a job title
    if "|" in job_title:
        warnings.append("page_title")

    # ai: claude-opus-5-5 | 2026-11-15
    # Auto-fix + flag: a sign-in wall or search page names no posting, and keeping
    # it would make every job captured through the same page look like one job
    # (posting_identity.is_non_posting_url). Drop it; import repair can still
    # recover the real link from the snippet, same as for homepage_url.
    if url and is_non_posting_url(url):
        logger.info("Dropping non-posting URL %r from %r", url, job_title)
        job["url"] = ""
        url = ""
        warnings.append(NON_POSTING_URL_WARNING)

    # Flag: URL has no meaningful path — likely an ATS or aggregator homepage
    if url:
        try:
            parsed = urlparse(url)
            if not parsed.path.strip("/"):
                warnings.append("homepage_url")
        except Exception:
            pass

    # Flag: JD enrichment failed and the stored snippet is just a bare
    # tracking/redirect link (e.g. a SendGrid click-tracking URL) rather than
    # real description text — showing that raw link as the job body is
    # confusing, so route it to triage for a manual look.
    if not str(job.get(JobColumns.JOB_DESCRIPTION) or "").strip():
        snippet = str(job.get(JobColumns.RAW_JD_SNIPPET) or "").strip()
        if snippet and is_email_tracking_url(snippet):
            warnings.append(TRACKING_LINK_JD_WARNING)

    return job, warnings



# ai: claude-opus-5-5 | 2026-11-17
def has_import_warning(raw_warnings: str | None, code: str) -> bool:
    """Whether the import_warnings JSON lists *code* (False when empty or unparseable)."""
    if not raw_warnings:
        return False
    try:
        existing = json.loads(raw_warnings)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(existing, list) and code in existing


# ai: claude-sonnet-5 | 2026-09-21
def remove_import_warning(raw_warnings: str | None, code: str) -> str | None:
    """Return the import_warnings JSON with *code* removed (None when none remain).

    Leaves other warning codes untouched, and returns *raw_warnings* unchanged
    when it is empty, unparseable, or doesn't contain *code*.
    """
    if not raw_warnings:
        return raw_warnings
    try:
        existing = json.loads(raw_warnings)
    except (json.JSONDecodeError, TypeError):
        return raw_warnings
    if not isinstance(existing, list) or code not in existing:
        return raw_warnings
    return import_warnings_to_json([w for w in existing if w != code])


def import_warnings_to_json(warnings: list[str]) -> str | None:
    """Serialize a warnings list to a JSON string for the import_warnings column.

    Returns None when warnings is empty so the column stays NULL for clean jobs.
    """
    return json.dumps(warnings) if warnings else None
