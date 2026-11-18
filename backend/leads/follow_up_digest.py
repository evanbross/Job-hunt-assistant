# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-18
"""The daily lead follow-up email (recruiter lead tracking, phase 3).

Once a day, at the time the account picked in its own timezone, it lists the
open leads that need attention: a follow-up date that has come, or nothing
logged for longer than the "gone quiet" setting. "Needs attention" is
backend/leads/lead_service.py's rule, the same one behind the Leads tab and its
badge, so the email never disagrees with the app.

Beat asks every 15 minutes; each account's day is claimed in
lead_follow_up_digest_log before anything is sent, so two workers (or a worker
restarted mid-morning) can't send it twice. A day is claimed even when nothing
needs attention: the email is a morning summary, and a follow-up date added in
the afternoon shouldn't trigger a second, mid-day email."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from datetime import time as time_of_day
from typing import Any

import config
from backend.celery_app import celery_app
from backend.email.notification_recipient import notification_recipient
from backend.email.smtp_sender import send_email
from backend.leads.lead_service import LeadService, attention_rules, local_now
from backend.public_urls import public_base_url
from backend.scanning.schedulers import for_each_active_account
from backend.settings_service import get_setting, get_setting_bool
from backend.utils.utils import get_repos

logger = logging.getLogger(__name__)


def send_time(value: str) -> time_of_day:
    """The configured HH:MM, or the default when it isn't a valid 24-hour time."""
    try:
        hour, minute = (int(part) for part in value.strip().split(":", 1))
        return time_of_day(hour=hour, minute=minute)
    except (ValueError, TypeError):
        logger.warning("Invalid lead_follow_up_digest_time %r; using %s", value, config.DEFAULT_LEAD_FOLLOW_UP_DIGEST_TIME)
        hour, minute = (int(part) for part in config.DEFAULT_LEAD_FOLLOW_UP_DIGEST_TIME.split(":", 1))
        return time_of_day(hour=hour, minute=minute)


def _lead_name(lead: dict[str, Any]) -> str:
    company = (lead.get("company") or "").strip() or "Unknown company"
    title = (lead.get("job_title") or "").strip()
    name = f"{company} — {title}" if title else company
    contact = (lead.get("primary_contact_name") or "").strip()
    if contact:
        agency = (lead.get("primary_contact_company") or "").strip()
        via = f"{contact}, {agency}" if agency and agency.lower() != company.lower() else contact
        name += f" (via {via})"
    return name


def _days(count: int) -> str:
    return f"{count} day{'' if count == 1 else 's'}"


def format_follow_up_digest(
    leads: list[dict[str, Any]],
    today: date,
    stale_after_days: int,
    stage_labels: dict[str, str],
    app_url: str,
) -> tuple[str, str]:
    """The (subject, plain-text body) for *leads*, every one of which needs attention."""
    due = [lead for lead in leads if lead["follow_up_due"]]
    quiet = [lead for lead in leads if not lead["follow_up_due"]]
    count = len(leads)
    subject = f"{count} lead{'' if count == 1 else 's'} to follow up — {today.isoformat()}"
    lines = [f"{count} open lead{' needs' if count == 1 else 's need'} your attention today.", ""]

    if due:
        lines += ["Follow-up due", "-------------"]
        for lead in due:
            follow_up = date.fromisoformat(str(lead["next_follow_up"])[:10])
            when = "today" if follow_up == today else f"overdue since {follow_up.isoformat()}"
            lines.append(f"- {_lead_name(lead)}")
            detail = f"  Follow-up {when}"
            stage = stage_labels.get(lead.get("stage") or "")
            if stage:
                detail += f" · {stage}"
            if lead["is_stale"]:
                detail += f" · nothing logged for {_days(lead['idle_days'])}"
            lines.append(detail)
        lines.append("")

    if quiet:
        heading = f"Gone quiet (nothing logged for over {_days(stale_after_days)})"
        lines += [heading, "-" * len(heading)]
        for lead in quiet:
            lines.append(f"- {_lead_name(lead)}")
            detail = f"  Last activity {_days(lead['idle_days'])} ago"
            stage = stage_labels.get(lead.get("stage") or "")
            if stage:
                detail += f" · {stage}"
            lines.append(detail)
        lines.append("")

    lines += [
        f"Open the Leads tab: {app_url}/?tab=leads",
        "",
        "Turn this email off or change its time in Settings > Leads.",
    ]
    return subject, "\n".join(lines) + "\n"


def send_lead_follow_up_digest(user_id: int, now: datetime) -> bool:
    """Send *user_id*'s follow-up email if today's is due and not yet claimed.
    True when an email went out."""
    with get_repos(user_id) as repos:
        if not get_setting_bool("lead_follow_up_digest_enabled", user_id, repos, default=False):
            return False
        account_now = local_now(now, get_setting("candidate_timezone", user_id, repos))
        if account_now.time() < send_time(get_setting("lead_follow_up_digest_time", user_id, repos)):
            return False
        rules = attention_rules(user_id, repos, now)
        if not repos["lead_follow_up_digest_log"].claim(user_id, rules.today.isoformat()):
            return False
        leads = LeadService(repos).leads_needing_attention(user_id, rules)
        recipient = notification_recipient(
            user_id, "lead_follow_up_digest_notify_email", repos, fall_back_to_account=True
        )
        stage_labels = {s["key"]: s["label"] for s in repos["stage_statuses"].get_all()}
        app_url = public_base_url(repos)
        repos["conn"].commit()

    if not leads:
        return False
    if not recipient or recipient.endswith("@localhost"):
        logger.warning("Lead follow-up email not sent for user %s: no deliverable email address", user_id)
        return False
    subject, body = format_follow_up_digest(leads, rules.today, rules.stale_after_days, stage_labels, app_url)
    send_email(to=recipient, subject=subject, body_text=body)
    logger.info("Sent lead follow-up email for user %s (%d leads)", user_id, len(leads))
    return True


@celery_app.task
def lead_follow_up_digest_tick() -> None:
    """Celery beat's tick (backend/celery_app.py): send each account's follow-up
    email once its time has come today, catching up after downtime the same day."""
    now = datetime.now(UTC)
    for_each_active_account("lead follow-up email", lambda user_id: send_lead_follow_up_digest(user_id, now))
