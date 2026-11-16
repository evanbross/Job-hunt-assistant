# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-15
"""Codes stored in jobs.import_warnings (a JSON array) for issues that need a look.

A leaf module (no project imports) so the database layer can set them too -- the
2026-11-15 same-posting merge (backend/db/same_posting_merge.py) does -- without
importing job_validator.py, whose email-parser import reaches back into backend.db.
job_validator.py re-exports these; the user-facing labels live in routes/jobs.py's
_IMPORT_WARNING_LABELS and frontend/src/utils/jobQueueUtils.ts."""

from __future__ import annotations

# ai: claude-sonnet-5 | 2026-09-21
# Set by insert-time scan paths when the fit scorer threw, so the job is routed
# to triage as "needs scoring" instead of sitting in pending_review with a NULL
# score. Every path that later scores the job successfully must clear it via
# job_validator.remove_import_warning().
SCORING_FAILED_WARNING = "scoring_failed"

# ai: claude-opus-5-5 | 2026-11-13
# Set by normalize_and_validate when JD enrichment failed and the stored
# snippet is only a tracking link. Cleared by any path that stores a real JD.
TRACKING_LINK_JD_WARNING = "jd_body_is_tracking_link"

# Set when a sign-in or search-page URL (posting_identity.is_non_posting_url)
# is dropped from a job, by normalize_and_validate or the 2026-11-15 merge.
NON_POSTING_URL_WARNING = "non_posting_url"

# Set by the 2026-11-15 merge on a job whose URL turned out to be another
# company's posting (e.g. a digest email pairing a job with the wrong link);
# the URL is dropped so it no longer claims to be that posting.
URL_OF_OTHER_JOB_WARNING = "url_of_other_job"
