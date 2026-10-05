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

"""Formatting and delivery for scheduled career-site scan reports."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from backend.email.smtp_sender import send_email
from backend.settings_service import get_setting, get_setting_bool
from backend.utils.utils import get_repos

logger = logging.getLogger(__name__)


# ai: gpt-5 | 2026-10-04
def format_career_scan_digest(state: dict[str, Any]) -> str:
    """Build the plain-text report for one scheduled daily scan."""
    site_reports = list(state.get("site_reports") or [])
    successful = sum(1 for report in site_reports if report.get("success"))
    failed = len(site_reports) - successful
    lines = [
        "Daily job-board scan report",
        "",
        f"Finished: {state.get('last_run_at') or 'unknown'} UTC",
        f"Boards: {len(site_reports)} total, {successful} succeeded, {failed} failed",
        f"Postings found: {state.get('jobs_found', 0)}",
        f"New postings: {sum(int(report.get('new', 0)) for report in site_reports)}",
        f"Duplicates: {sum(int(report.get('duplicates', 0)) for report in site_reports)}",
        f"Ignored: {state.get('listings_filtered', 0)}",
        "",
        "Board details",
        "-------------",
    ]

    for report in site_reports:
        status = "SUCCESS" if report.get("success") else "FAILED"
        lines.extend(
            [
                "",
                f"{report.get('site_name') or 'Unknown board'} — {status}",
                f"URL: {report.get('site_url') or 'unknown'}",
                (
                    f"Postings: {report.get('postings', 0)}; "
                    f"new: {report.get('new', 0)}; "
                    f"duplicates: {report.get('duplicates', 0)}; "
                    f"ignored: {report.get('ignored', 0)}"
                ),
            ]
        )
        error_message = str(report.get("error") or "").strip()
        if error_message:
            lines.append(f"Error: {error_message}")

        ignored_reasons = Counter(report.get("ignored_reasons") or {})
        if ignored_reasons:
            lines.append("Ignored breakdown:")
            lines.extend(
                f"  - {reason}: {count}"
                for reason, count in sorted(ignored_reasons.items())
            )

        parse_failures = list(report.get("parse_failures") or [])
        if parse_failures:
            lines.append("Parse failures:")
            lines.extend(f"  - {failure}" for failure in parse_failures)

    if not site_reports:
        lines.append("No enabled automatic job boards were available to scan.")
    run_errors = list(state.get("errors") or [])
    if run_errors:
        lines.extend(["", "Run errors:"])
        lines.extend(f"  - {error}" for error in run_errors)
    return "\n".join(lines) + "\n"


# ai: gpt-5 | 2026-10-04
# ai: claude-sonnet-5 | 2026-10-05
def send_career_scan_digest(user_id: int, state: dict[str, Any]) -> None:
    """Send a completed scheduled scan report to its account owner.

    Idempotent per (user_id, report_date) via career_scan_digest_log.claim():
    the old version checked a "last sent date" setting, released the DB
    connection, sent the email, then wrote the setting back -- two scheduled
    runs (e.g. an orphaned app process plus a fresh one) could both pass the
    check before either wrote the mark, and both send. claim() is a single
    atomic INSERT guarded by a UNIQUE(user_id, report_date) constraint, so
    only the first caller for a given date ever reaches send_email().
    """
    last_run_at = str(state.get("last_run_at") or "")
    report_date = last_run_at[:10] if last_run_at else "today"
    with get_repos(user_id) as repos:
        if not get_setting_bool(
            "career_scan_digest_enabled", user_id, repos, default=True
        ):
            return
        configured_recipient = get_setting(
            "career_scan_digest_notify_email", user_id, repos
        ).strip()
        account = repos["user_accounts"].get(user_id)
        claimed = repos["career_scan_digest_log"].claim(user_id, report_date)
        repos["conn"].commit()
    if not claimed:
        return

    recipient = configured_recipient or str((account or {}).get("email") or "").strip()
    if not recipient or recipient.endswith("@localhost"):
        logger.warning(
            "Career scan digest not sent for user %s: no deliverable email address",
            user_id,
        )
        return

    send_email(
        to=recipient,
        subject=f"Job-board scan report — {report_date}",
        body_text=format_career_scan_digest(state),
    )
    logger.info("Sent career scan digest for user %s to %s", user_id, recipient)
