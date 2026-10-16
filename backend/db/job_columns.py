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

"""Single source of truth for the `jobs` table's column names.

Deliberately a leaf module (no project imports) so it can be imported from
anywhere — the schema/migration layer, repositories, routes, and business
logic — without creating an import cycle.
"""

from __future__ import annotations


class JobColumns:
    """Column names for the `jobs` table, matching backend/db/schema.py."""

    ID = "id"
    # ai: claude-sonnet-5 | 2026-08-12 — renamed from email_id: holds a real
    # Gmail message ID only for email-sourced jobs; for every other source
    # (browser extension, manual paste, career-site scan, board search) it
    # holds a synthetic "source:date:token"-shaped dedup key instead.
    DEDUP_KEY = "dedup_key"
    COMPANY = "company"
    # ai: claude-sonnet-5 | 2026-10-01 — kept in sync by PostgresJobRepository's
    # insert()/update() whenever COMPANY is written (normalize_company_name()),
    # mirroring contacts.company_normalized's pattern. Lets get_by_company()/
    # get_company_counts() filter in SQL instead of fetching every job for the
    # user and comparing in Python, which no longer scales past a few thousand rows.
    COMPANY_NORMALIZED = "company_normalized"
    JOB_TITLE = "job_title"
    URL = "url"
    SOURCE = "source"
    # ai: claude-sonnet-5 | 2026-08-12 — renamed from raw_email_snippet: this
    # column holds the originally captured JD text regardless of ingestion
    # source (email, browser extension, manual paste, career-site scan), not
    # just email-sourced text.
    RAW_JD_SNIPPET = "raw_jd_snippet"
    FIT_SCORE = "fit_score"
    FIT_REASONING = "fit_reasoning"
    STATUS = "status"
    CERTAINTY_LEVEL = "certainty_level"
    CERTAINTY_REASON = "certainty_reason"
    DATE_FOUND = "date_found"
    ON_SHEET = "on_sheet"
    DUPLICATE_HINT = "duplicate_hint"
    DUPLICATE_OF_ID = "duplicate_of_id"
    SENDER_EMAIL = "sender_email"
    # ai: claude-sonnet-5 | 2026-08-12 — renamed from email_subject: for
    # non-email sources this holds a synthetic capture description (e.g.
    # "Browser Extension streamed job listing"), not a real email subject.
    CAPTURE_LABEL = "capture_label"
    CERTAINTY_SCORE = "certainty_score"
    SKIP_REASON = "skip_reason"
    SKIP_FEEDBACK = "skip_feedback"
    SKIP_MISMATCH_REASONS = "skip_mismatch_reasons"
    NOTES = "notes"
    # The enriched/full JD, when a re-scan has found one richer than
    # RAW_JD_SNIPPET. See backend/scoring/jd_enrichment.py's job_description_text().
    JOB_DESCRIPTION = "job_description"
    IMPORT_WARNINGS = "import_warnings"
    SNOOZED_UNTIL = "snoozed_until"
    DISMISSED_DUPLICATE_IDS = "dismissed_duplicate_ids"
    SALARY_MIN = "salary_min"
    SALARY_MAX = "salary_max"
    UNREACHABLE_SINCE = "unreachable_since"
    # ai: claude-sonnet-5 | 2026-10-14 — LinkedIn's own "You'd be a top
    # applicant"-style AI match assessment, captured verbatim from the search
    # card badge or the detail-pane "AI-powered job assessment" module. Only
    # ever populated for LinkedIn captures; NULL for every other source.
    LINKEDIN_AI_INSIGHT = "linkedin_ai_insight"

    @classmethod
    def all(cls) -> frozenset[str]:
        """Every column name declared above, for allowlist/validation use."""
        return frozenset(
            value
            for name, value in vars(cls).items()
            if name.isupper() and isinstance(value, str)
        )

    # ai: claude-sonnet-5 | 2026-10-16 — the application-history list view
    # (routes/jobs.py's get_history_jobs) only ever displays these columns;
    # JOB_DESCRIPTION/RAW_JD_SNIPPET are large TEXT blobs that used to be
    # pulled (and shipped to the client) for every row on every page even
    # though the UI only shows them once a row is expanded, which is why that
    # endpoint was slow. Named here rather than inlined in the repository
    # query so the summary/full-row contract stays visible in one place.
    @classmethod
    def history_summary(cls) -> tuple[str, ...]:
        """Column names for the lightweight application-history list query."""
        return (
            cls.ID,
            cls.COMPANY,
            cls.JOB_TITLE,
            cls.URL,
            cls.SOURCE,
            cls.FIT_SCORE,
            cls.STATUS,
            cls.DATE_FOUND,
        )
