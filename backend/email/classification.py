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
"""Email subject/message classification helpers used by the scan pipeline."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from backend.db import IJobRepository
from backend.db.job_columns import JobColumns
from backend.email.gmail_client import GmailClient, NotAuthenticatedError
from backend.utils.utils import ACTIVE_JOB_STATUSES

logger = logging.getLogger(__name__)


def get_subject_rule_match(subject: str, rules: list[dict]) -> str | None:
    """Check *subject* against enabled email subject rules from the DB.

    Returns 'whitelist', 'blacklist', or None. Whitelist takes immediate
    precedence — if any whitelist pattern matches, the result is 'whitelist'
    regardless of blacklist matches.
    """
    lower = subject.lower()
    matched_blacklist = False
    for rule in rules:
        if rule.get("pattern", "").lower() in lower:
            if rule.get("rule_type") == "whitelist":
                return "whitelist"
            matched_blacklist = True
    return "blacklist" if matched_blacklist else None


def email_date_from_message(full_msg: dict) -> str | None:
    """Extract the Gmail message send timestamp as a UTC ISO string when available."""
    raw = full_msg.get("internalDate")
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw) / 1000, UTC).isoformat(
            timespec="seconds"
        )
    except (ValueError, OSError):
        return None


# ai: claude-sonnet-4-6 | 2026-07-02
def maybe_mark_source_email_read(
    job: dict[str, object], job_repo: IJobRepository
) -> None:
    """Mark the source Gmail message read once no active queue jobs remain for it.

    Shared by routes/jobs.py (manual skip) and routes/applications.py (closed-req
    scan) per FLASK_STANDARDS.md — any helper used by two or more blueprints
    belongs in this shared module, not in one of the blueprints.
    """
    dedup_key = str(job.get(JobColumns.DEDUP_KEY) or "").strip()
    sender_email = str(job.get("sender_email") or "").strip()
    if not dedup_key or not sender_email:
        return

    base_dedup_key = dedup_key.split(":")[0]
    remaining_active = job_repo.count_by_base_dedup_key_and_statuses(
        base_dedup_key,
        list(ACTIVE_JOB_STATUSES),
    )
    if remaining_active > 0:
        return

    try:
        GmailClient().mark_as_read(base_dedup_key)
    except NotAuthenticatedError:
        logger.warning(
            "Could not mark email %s as read: Gmail is not authenticated", base_dedup_key
        )
    except Exception as exc:
        logger.warning("Could not mark email %s as read: %s", base_dedup_key, exc)
