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
    PARSER_CONFIDENCE = "parser_confidence"
    CONFIDENCE_REASON = "confidence_reason"
    DATE_FOUND = "date_found"
    ON_SHEET = "on_sheet"
    DUPLICATE_HINT = "duplicate_hint"
    DUPLICATE_OF_ID = "duplicate_of_id"
    SENDER_EMAIL = "sender_email"
    # ai: claude-sonnet-5 | 2026-08-12 — renamed from email_subject: for
    # non-email sources this holds a synthetic capture description (e.g.
    # "Browser Extension streamed job listing"), not a real email subject.
    CAPTURE_LABEL = "capture_label"
    CONFIDENCE_SCORE = "confidence_score"
    SKIP_REASON = "skip_reason"
    SKIP_FEEDBACK = "skip_feedback"
    SKIP_MISMATCH_REASONS = "skip_mismatch_reasons"
    NOTES = "notes"
    # The enriched/full JD, when a re-scan has found one richer than
    # RAW_JD_SNIPPET. See backend/utils/utils.py's job_description_text().
    JOB_DESCRIPTION = "job_description"
    IMPORT_WARNINGS = "import_warnings"
    SNOOZED_UNTIL = "snoozed_until"
    DISMISSED_DUPLICATE_IDS = "dismissed_duplicate_ids"
    SALARY_MIN = "salary_min"
    SALARY_MAX = "salary_max"
    UNREACHABLE_SINCE = "unreachable_since"

    @classmethod
    def all(cls) -> frozenset[str]:
        """Every column name declared above, for allowlist/validation use."""
        return frozenset(
            value
            for name, value in vars(cls).items()
            if name.isupper() and isinstance(value, str)
        )
