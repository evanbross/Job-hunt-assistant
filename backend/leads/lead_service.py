# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Recruiter lead tracking (docs: "Recruiter Lead Tracking -- Plan", phase 1).

A lead is a tracked job (jobs.on_sheet = 1, jobs.status = 'applied' -- the same
pair every other part of the app already reads as "a job the user is tracking")
that came from a recruiter (jobs.origin = 'recruiter_contact') or has people
attached through job_contacts. Where it stands is its latest application stage,
read through effective_application_status() like everywhere else.

This service only orchestrates the repositories it is handed; it never commits.
The route commits once, so creating a lead (job, first stage, contact, link and
first call) is one transaction."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import config
from backend.contacts.contact_emails import ContactEmailInput
from backend.contacts.contact_identity import contact_identity_columns
from backend.settings_service import get_setting, get_setting_int
from backend.utils.stage_status_normalization import effective_application_status

logger = logging.getLogger(__name__)

# ai: claude-opus-5-5 | 2026-11-11
# How far back a role-less conversation still counts as open on the Leads tab.
OPEN_CONVERSATION_WINDOW_DAYS = 30
# Where a new recruiter lead starts unless the caller says otherwise.
DEFAULT_LEAD_STAGE = "recruiter_reached_out"
# jobs.source for a lead created here, shown in the Applications sheet's Source column.
LEAD_SOURCE_LABEL = "Recruiter"
# ai: claude-opus-5-5 | 2026-11-18 -- moved from routes/leads.py: the follow-up
# email reads the same setting. Bounds a typo'd or hostile value; anything
# outside falls back to the default.
STALE_AFTER_DAYS_RANGE = (1, 365)


class ContactNotFoundError(LookupError):
    """A contact named by id doesn't exist or belongs to another user."""


class InvalidStageError(ValueError):
    """A stage key that isn't in application_stage_statuses."""


# ai: claude-opus-5-5 | 2026-11-11
@dataclass(frozen=True)
class NewContact:
    full_name: str
    company: str
    position: str
    profile_url: str | None
    phone: str | None
    emails: tuple[ContactEmailInput, ...]  # ai: claude-opus-5-5 | 2026-11-22


# ai: claude-opus-5-5 | 2026-11-11
@dataclass(frozen=True)
class NewLead:
    """A lead to create. Text fields arrive validated and sanitized by the route."""

    company: str
    job_title: str
    url: str | None
    job_description: str | None
    stage: str
    stage_date: str
    next_follow_up: str | None
    summary_html: str | None
    contact_id: int | None
    new_contact: NewContact | None
    contact_role: str
    first_interaction: dict[str, Any] | None


# ai: claude-opus-5-5 | 2026-11-18
def local_now(now: datetime, timezone_name: str) -> datetime:
    """*now* (UTC-aware) on the account's own clock: the IANA zone from Settings >
    Your Timezone, or UTC when that is blank or not a zone this machine knows."""
    name = timezone_name.strip()
    if name:
        try:
            return now.astimezone(ZoneInfo(name))
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("Unknown candidate_timezone %r; using UTC for lead dates", name)
    return now.astimezone(UTC)


# ai: claude-opus-5-5 | 2026-11-18
@dataclass(frozen=True)
class AttentionRules:
    """What makes a lead need attention, for one account: its own calendar date
    (a follow-up dated today is due from local midnight, not from UTC's) and how
    many idle days make a lead "gone quiet"."""

    today: date
    stale_after_days: int


# ai: claude-opus-5-5 | 2026-11-18
def attention_rules(user_id: int, repos: dict[str, Any], now: datetime) -> AttentionRules:
    """The account's AttentionRules as of *now*, from its settings. The Leads tab,
    its badge and the follow-up email all use this, so they always agree."""
    stale_after_days = get_setting_int(
        "lead_stale_after_days", user_id, repos, config.DEFAULT_LEAD_STALE_AFTER_DAYS
    )
    low, high = STALE_AFTER_DAYS_RANGE
    if not low <= stale_after_days <= high:
        stale_after_days = config.DEFAULT_LEAD_STALE_AFTER_DAYS
    today = local_now(now, get_setting("candidate_timezone", user_id, repos)).date()
    return AttentionRules(today=today, stale_after_days=stale_after_days)


# ai: claude-opus-5-5 | 2026-11-11
def _activity_date(value: str | None) -> date | None:
    """The calendar date of a stored timestamp or YYYY-MM-DD date (both stored as
    text), or None when absent or unparseable."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


# ai: claude-opus-5-5 | 2026-11-11
class LeadService:
    def __init__(self, repos: dict[str, Any]) -> None:
        self._repos = repos

    def _stage_catalog(self) -> list[dict[str, Any]]:
        return self._repos["stage_statuses"].get_all()

    def _validate_stage(self, stage: str) -> None:
        if stage not in {s["key"] for s in self._stage_catalog()}:
            raise InvalidStageError(f"Unknown stage: {stage}")

    # ai: claude-opus-5-5 | 2026-11-18 -- takes AttentionRules (the account's own
    # date) instead of a bare stale_after_days.
    def board(self, user_id: int, now: datetime, rules: AttentionRules) -> dict[str, Any]:
        """Everything the Leads tab shows: every lead (see _lead_rows), plus the
        role-less open conversations."""
        since = (now - timedelta(days=OPEN_CONVERSATION_WINDOW_DAYS)).isoformat(timespec="seconds")
        return {
            "leads": self._lead_rows(user_id, rules),
            "open_conversations": self._repos["leads"].list_open_conversations(user_id, since),
            "stale_after_days": rules.stale_after_days,
            "open_conversation_window_days": OPEN_CONVERSATION_WINDOW_DAYS,
        }

    # ai: claude-opus-5-5 | 2026-11-18
    def leads_needing_attention(self, user_id: int, rules: AttentionRules) -> list[dict[str, Any]]:
        """Open leads whose follow-up date has come or that have gone quiet, in the
        board's order (soonest follow-up first)."""
        return [lead for lead in self._lead_rows(user_id, rules) if lead["needs_attention"]]

    def _lead_rows(self, user_id: int, rules: AttentionRules) -> list[dict[str, Any]]:
        """Every lead with its effective stage, whether that stage is terminal
        (closed), days since anything happened and whether that makes it stale,
        whether its follow-up date has come, and so whether it needs attention."""
        terminal = {s["key"] for s in self._stage_catalog() if s["is_terminal"]}
        today = rules.today
        leads = []
        for row in self._repos["leads"].list_leads(user_id):
            latest = row.pop("latest_stage_status")
            stage = effective_application_status(
                row.pop("job_status"), [{"status": latest}] if latest else []
            )
            activity = [
                d
                for d in (
                    _activity_date(row["last_interaction_at"]),
                    _activity_date(row["latest_stage_date"]),
                    _activity_date(row["date_found"]),
                )
                if d is not None
            ]
            last_activity = max(activity) if activity else None
            idle_days = (today - last_activity).days if last_activity else None
            is_closed = stage in terminal
            is_stale = not is_closed and idle_days is not None and idle_days > rules.stale_after_days
            # ai: claude-opus-5-5 | 2026-11-18
            follow_up = _activity_date(row["next_follow_up"])
            follow_up_due = not is_closed and follow_up is not None and follow_up <= today
            leads.append(
                {
                    **row,
                    "stage": stage,
                    "is_closed": is_closed,
                    "last_activity_date": last_activity.isoformat() if last_activity else None,
                    "idle_days": idle_days,
                    "is_stale": is_stale,
                    "follow_up_due": follow_up_due,  # ai: claude-opus-5-5 | 2026-11-18
                    "needs_attention": is_stale or follow_up_due,  # ai: claude-opus-5-5 | 2026-11-18
                }
            )
        return leads

    def detail(self, user_id: int, job_id: int) -> dict[str, Any] | None:
        """One lead's drawer: the job, its people, its timeline (interactions newest
        first), its stage history (oldest first, as everywhere else) and every later
        capture of its posting (sightings, newest first)."""
        lead = self._repos["leads"].get_lead(user_id, job_id)
        if lead is None:
            return None
        stages = self._repos["stages"].get_by_job_id(user_id, job_id)
        lead["stage"] = effective_application_status(lead.pop("job_status"), stages)
        return {
            "lead": lead,
            "people": self._repos["job_contacts"].list_for_job(user_id, job_id),
            "interactions": self._repos["interactions"].list_for_job(user_id, job_id),
            "stages": stages,
            "sightings": self._repos["job_sightings"].list_for_job(user_id, job_id),  # ai: claude-opus-5-5 | 2026-11-15
        }

    def create_contact(
        self, user_id: int, contact: NewContact, now_iso: str, keep_informed: bool = False
    ) -> tuple[int, bool]:
        """Add a contact by hand; (id, created) -- see create_manual()."""
        identity = contact_identity_columns(
            contact.full_name, contact.company, contact.position, contact.profile_url
        )
        details: dict[str, Any] = {"keep_informed": keep_informed}
        if contact.phone:
            details["phone"] = contact.phone
        contact_id, created = self._repos["contacts"].create_manual(user_id, identity, details, now_iso)
        # ai: claude-opus-5-5 | 2026-11-22 -- like phone, the addresses are only
        # written on a new contact; an existing one keeps what it has.
        if created:
            for email in contact.emails:
                self._repos["contact_emails"].add(
                    user_id,
                    contact_id,
                    address=email.address,
                    email_type=email.email_type,
                    custom_label=email.custom_label,
                    created_at=now_iso,
                )
        return contact_id, created

    def create_lead(self, user_id: int, new: NewLead, now_iso: str) -> int:
        """Create a recruiter lead: the job (tracked, origin recruiter_contact), its
        first stage, the recruiter (an existing contact, or a new one -- reusing
        whoever already matches), the link making them its primary contact, and the
        first call or message if given. Raises InvalidStageError, ContactNotFoundError,
        or the jobs repository's DuplicateJobUrlError when the URL is already tracked."""
        self._validate_stage(new.stage)
        job_fields: dict[str, Any] = {
            "company": new.company,
            "job_title": new.job_title,
            "source": LEAD_SOURCE_LABEL,
            "status": "applied",
            "on_sheet": 1,
            "origin": "recruiter_contact",
            "date_found": new.stage_date,
        }
        optional = {
            "url": new.url,
            "job_description": new.job_description,
            "next_follow_up": new.next_follow_up,
            "summary_html": new.summary_html,
        }
        job_fields.update({key: value for key, value in optional.items() if value is not None})
        job_id = self._repos["jobs"].insert(user_id, job_fields)
        self._repos["stages"].insert(
            user_id, {"job_id": job_id, "status": new.stage, "date": new.stage_date, "notes": None}
        )

        contact_id = new.contact_id
        if new.new_contact is not None:
            contact_id, _ = self.create_contact(user_id, new.new_contact, now_iso)
        if contact_id is not None:
            linked = self._repos["job_contacts"].link(
                user_id, job_id, contact_id, role=new.contact_role, is_primary=True, created_at=now_iso
            )
            if linked is None:
                raise ContactNotFoundError(contact_id)

        if new.first_interaction is not None:
            self._repos["interactions"].add(
                user_id, {**new.first_interaction, "job_id": job_id, "contact_id": contact_id}, now_iso
            )
        return job_id

    def contact_activity(self, user_id: int, contact_id: int) -> dict[str, Any] | None:
        """A contact's jobs (with each job's effective stage) and interaction history."""
        if self._repos["contacts"].get_by_id(user_id, contact_id) is None:
            return None
        jobs = []
        for row in self._repos["job_contacts"].list_for_contact(user_id, contact_id):
            latest = row.pop("latest_stage_status")
            row["stage"] = effective_application_status(
                row.pop("job_status"), [{"status": latest}] if latest else []
            )
            jobs.append(row)
        return {
            "jobs": jobs,
            "interactions": self._repos["interactions"].list_for_contact(user_id, contact_id),
        }
