# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Formatting and delivery for scheduled career-site scan reports."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from backend.email.notification_recipient import notification_recipient
from backend.email.smtp_sender import send_email
from backend.settings_service import get_setting_bool
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
        # ai: claude-opus-5-5 | 2026-11-09 -- hosted: the account's own email only
        recipient = notification_recipient(
            user_id, "career_scan_digest_notify_email", repos, fall_back_to_account=True
        )
        # ai: claude-sonnet-5 | 2026-11-26 — claiming an attempt is not the same as
        # delivering: _mark_delivered() below is the only thing that may ever mark
        # this date done. See PostgresCareerScanDigestLogRepository.claim().
        claimed = repos["career_scan_digest_log"].claim(user_id, report_date)
        repos["conn"].commit()
    if not claimed:
        return

    if not recipient or recipient.endswith("@localhost"):
        logger.warning(
            "Career scan digest not sent for user %s: no deliverable email address",
            user_id,
        )
        _mark_delivered(user_id, report_date)
        return

    send_email(
        to=recipient,
        subject=f"Job-board scan report — {report_date}",
        body_text=format_career_scan_digest(state),
    )  # may raise -- leave undelivered, let the next tick retry
    _mark_delivered(user_id, report_date)
    logger.info("Sent career scan digest for user %s to %s", user_id, recipient)


def _mark_delivered(user_id: int, report_date: str) -> None:
    """Record that *report_date* is done -- either sent, or a date this account
    never had a deliverable address for. Only called after send_email() has
    already returned (or wasn't needed), in its own short connection: see
    PYTHON_STANDARDS.md's rule against holding a connection across slow I/O."""
    with get_repos(user_id) as repos:
        repos["career_scan_digest_log"].mark_delivered(user_id, report_date)
        repos["conn"].commit()
