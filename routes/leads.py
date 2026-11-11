# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Leads tab routes (recruiter lead tracking, phase 1): the leads board, creating
and editing a lead, the people attached to it, and the call/message/note log
(interactions) shared with the Contacts tab."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Annotated, Literal
from urllib.parse import urlparse

import psycopg2.errors
from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    model_validator,
)

import config
from backend.db import DuplicateJobUrlError
from backend.leads.lead_service import (
    DEFAULT_LEAD_STAGE,
    ContactNotFoundError,
    InvalidStageError,
    LeadService,
    NewContact,
    NewLead,
)
from backend.settings_service import get_setting_int
from backend.utils.request_fields import OptionalEmailAddress, UtcTimestamp
from backend.utils.rich_text import MAX_RICH_TEXT_LENGTH, sanitize_rich_text
from backend.utils.stage_status_normalization import normalize_stage_status
from backend.utils.utils import current_user_id, get_repos, invalid_request_response

logger = logging.getLogger(__name__)

leads_bp = Blueprint("leads", __name__)

# ai: claude-opus-5-5 | 2026-11-11
# Mirror backend/db/schema.py's CHECK constraints on interactions and job_contacts.
InteractionChannel = Literal["phone", "video", "linkedin", "email", "text", "in_person", "other"]
InteractionDirection = Literal["inbound", "outbound"]
InteractionKind = Literal["call", "message", "note", "transcript"]
ContactRole = Literal[
    "agency_recruiter", "internal_recruiter", "hiring_manager", "interviewer", "referrer", "other"
]

_MAX_SHORT_TEXT = 300
_MAX_URL_LENGTH = 2000
_MAX_SUMMARY_LENGTH = 1000
_MAX_JOB_DESCRIPTION_LENGTH = 100_000
_MAX_PHONE_LENGTH = 50
# Bounds a typo'd or hostile setting value; anything outside falls back to it.
_STALE_DAYS_RANGE = (1, 365)


# ai: claude-opus-5-5 | 2026-11-11
def _rich_text(value: str | None) -> str | None:
    return sanitize_rich_text(value)


def _http_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an http(s) link")
    return value


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    return date.fromisoformat(value).isoformat()


# Length limits sit on the inner str: a constraint on an Optional applies to the
# union, not the string inside it.
RichText = Annotated[
    Annotated[str, Field(max_length=MAX_RICH_TEXT_LENGTH)] | None, AfterValidator(_rich_text)
]
HttpUrl = Annotated[Annotated[str, Field(max_length=_MAX_URL_LENGTH)] | None, AfterValidator(_http_url)]
IsoDate = Annotated[str | None, AfterValidator(_iso_date)]
ShortText = Annotated[str, Field(max_length=_MAX_SHORT_TEXT)]
RequiredText = Annotated[str, Field(min_length=1, max_length=_MAX_SHORT_TEXT)]
Phone = Annotated[str, Field(max_length=_MAX_PHONE_LENGTH)] | None
Summary = Annotated[str, Field(max_length=_MAX_SUMMARY_LENGTH)] | None
JobDescription = Annotated[str, Field(max_length=_MAX_JOB_DESCRIPTION_LENGTH)] | None


# ai: claude-opus-5-5 | 2026-11-11
class NewContactBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: RequiredText
    company: ShortText = ""
    position: ShortText = ""
    profile_url: HttpUrl = None
    phone: Phone = None
    email_address: OptionalEmailAddress = None

    def to_new_contact(self) -> NewContact:
        return NewContact(
            full_name=self.full_name,
            company=self.company,
            position=self.position,
            profile_url=self.profile_url,
            phone=self.phone or None,
            email_address=self.email_address,
        )


# ai: claude-opus-5-5 | 2026-11-11
class InteractionFields(BaseModel):
    """The parts of an interaction the user describes (not what it's attached to)."""

    model_config = ConfigDict(extra="forbid")

    occurred_at: UtcTimestamp | None = None
    channel: InteractionChannel
    direction: InteractionDirection = "inbound"
    kind: InteractionKind = "call"
    summary: Summary = None
    body_html: RichText = None

    def to_fields(self, now_iso: str) -> dict[str, object]:
        return {
            "occurred_at": self.occurred_at or now_iso,
            "channel": self.channel,
            "direction": self.direction,
            "kind": self.kind,
            "summary": self.summary or None,
            "body_html": self.body_html,
        }


# ai: claude-opus-5-5 | 2026-11-11
class LeadCreate(BaseModel):
    """company is the hiring company, or "Undisclosed" while an agency keeps it
    back. Name the recruiter with contact_id (someone already in Contacts) or
    new_contact, not both."""

    model_config = ConfigDict(extra="forbid")

    company: RequiredText
    job_title: ShortText = ""
    url: HttpUrl = None
    job_description: JobDescription = None
    stage: str = DEFAULT_LEAD_STAGE
    stage_date: IsoDate = None
    next_follow_up: IsoDate = None
    summary_html: RichText = None
    contact_id: StrictInt | None = None
    new_contact: NewContactBody | None = None
    contact_role: ContactRole = "agency_recruiter"
    first_interaction: InteractionFields | None = None

    @model_validator(mode="after")
    def _one_way_to_name_the_recruiter(self) -> "LeadCreate":
        if self.contact_id is not None and self.new_contact is not None:
            raise ValueError("Give contact_id or new_contact, not both")
        return self


# ai: claude-opus-5-5 | 2026-11-11
class LeadUpdate(BaseModel):
    """Partial update (exclude_unset): only fields present are written; null clears
    an optional field."""

    model_config = ConfigDict(extra="forbid")

    company: RequiredText | None = None
    job_title: ShortText | None = None
    url: HttpUrl = None
    job_description: JobDescription = None
    next_follow_up: IsoDate = None
    summary_html: RichText = None


# ai: claude-opus-5-5 | 2026-11-11
class LeadContactLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_id: StrictInt
    role: ContactRole = "agency_recruiter"
    is_primary: StrictBool = False


# ai: claude-opus-5-5 | 2026-11-11
class InteractionCreate(InteractionFields):
    contact_id: StrictInt | None = None
    job_id: StrictInt | None = None
    response_received_at: UtcTimestamp | None = None

    @model_validator(mode="after")
    def _attached_to_something(self) -> "InteractionCreate":
        if self.contact_id is None and self.job_id is None:
            raise ValueError("An interaction needs a contact_id or a job_id")
        return self


# ai: claude-opus-5-5 | 2026-11-11
class InteractionUpdate(BaseModel):
    """Partial update (exclude_unset). What it is attached to can't change."""

    model_config = ConfigDict(extra="forbid")

    occurred_at: UtcTimestamp | None = None
    channel: InteractionChannel | None = None
    direction: InteractionDirection | None = None
    kind: InteractionKind | None = None
    summary: Summary = None
    body_html: RichText = None
    response_received_at: UtcTimestamp | None = None

    @model_validator(mode="after")
    def _required_fields_are_not_cleared(self) -> "InteractionUpdate":
        for name in ("occurred_at", "channel", "direction", "kind"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} can't be cleared")
        return self


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ai: claude-opus-5-5 | 2026-11-11
@leads_bp.route("/leads")
def get_leads_board() -> ResponseReturnValue:
    """Every recruiter lead with its stage, staleness and primary contact, plus
    conversations with recruiters that aren't tied to a role yet."""
    user_id = current_user_id()
    with get_repos() as repos:
        stale_after_days = get_setting_int(
            "lead_stale_after_days", user_id, repos, config.DEFAULT_LEAD_STALE_AFTER_DAYS
        )
        low, high = _STALE_DAYS_RANGE
        if not low <= stale_after_days <= high:
            stale_after_days = config.DEFAULT_LEAD_STALE_AFTER_DAYS
        board = LeadService(repos).board(user_id, datetime.now(UTC), stale_after_days)
    return jsonify(board)


# ai: claude-opus-5-5 | 2026-11-11
@leads_bp.route("/leads", methods=["POST"])
def create_lead() -> ResponseReturnValue:
    """Create a recruiter lead: the job, its first stage, the recruiter (existing or
    new) as its primary contact, and optionally the first call or message. A URL
    that is already tracked answers 409 with that job's id, so the recruiter can be
    attached to it instead."""
    try:
        body = LeadCreate.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return invalid_request_response(exc)

    now_iso = _now_iso()
    new_lead = NewLead(
        company=body.company,
        job_title=body.job_title,
        url=body.url,
        job_description=body.job_description or None,
        stage=normalize_stage_status(body.stage),
        stage_date=body.stage_date or datetime.now(UTC).date().isoformat(),
        next_follow_up=body.next_follow_up,
        summary_html=body.summary_html,
        contact_id=body.contact_id,
        new_contact=body.new_contact.to_new_contact() if body.new_contact else None,
        contact_role=body.contact_role,
        first_interaction=body.first_interaction.to_fields(now_iso) if body.first_interaction else None,
    )
    user_id = current_user_id()
    with get_repos() as repos:
        try:
            job_id = LeadService(repos).create_lead(user_id, new_lead, now_iso)
        except InvalidStageError as exc:
            repos["conn"].rollback()
            return jsonify({"error": str(exc)}), 400
        except ContactNotFoundError:
            repos["conn"].rollback()
            return jsonify({"error": "Contact not found"}), 404
        except DuplicateJobUrlError:
            # insert() already rolled back; look up who holds the URL.
            existing = repos["jobs"].get_by_url(user_id, body.url or "")
            return (
                jsonify(
                    {
                        "error": "A job with this URL is already tracked.",
                        "existing_job_id": existing["id"] if existing else None,
                    }
                ),
                409,
            )
        repos["conn"].commit()
    return jsonify({"id": job_id}), 201


# ai: claude-opus-5-5 | 2026-11-11
@leads_bp.route("/leads/<int:job_id>")
def get_lead(job_id: int) -> ResponseReturnValue:
    """One lead's drawer: the job, its people, its call/message/note log and its
    stage history. Any tracked application can be opened here."""
    with get_repos() as repos:
        detail = LeadService(repos).detail(current_user_id(), job_id)
    if detail is None:
        return jsonify({"error": "Lead not found"}), 404
    return jsonify(detail)


# ai: claude-opus-5-5 | 2026-11-11
@leads_bp.route("/leads/<int:job_id>", methods=["PATCH"])
def update_lead(job_id: int) -> ResponseReturnValue:
    """Edit a lead's company, title, link, JD, follow-up date or rich-text summary."""
    try:
        body = LeadUpdate.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return invalid_request_response(exc)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return jsonify({"error": "No fields to update"}), 400
    if "company" in fields and fields["company"] is None:
        return jsonify({"error": "company can't be cleared"}), 400

    user_id = current_user_id()
    with get_repos() as repos:
        if repos["leads"].get_lead(user_id, job_id) is None:
            return jsonify({"error": "Lead not found"}), 404
        try:
            repos["jobs"].update(user_id, job_id, fields)
        except psycopg2.errors.UniqueViolation:
            repos["conn"].rollback()
            return jsonify({"error": "A job with this URL is already tracked."}), 409
        repos["conn"].commit()
    return jsonify({"ok": True})


# ai: claude-opus-5-5 | 2026-11-11
@leads_bp.route("/leads/<int:job_id>/contacts", methods=["POST"])
def link_lead_contact(job_id: int) -> ResponseReturnValue:
    """Attach a contact to a lead (or change their role / make them primary)."""
    try:
        body = LeadContactLink.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return invalid_request_response(exc)
    with get_repos() as repos:
        linked = repos["job_contacts"].link(
            current_user_id(),
            job_id,
            body.contact_id,
            role=body.role,
            is_primary=body.is_primary,
            created_at=_now_iso(),
        )
        if linked is None:
            repos["conn"].rollback()
            return jsonify({"error": "Lead or contact not found"}), 404
        repos["conn"].commit()
    return jsonify(linked)


# ai: claude-opus-5-5 | 2026-11-11
@leads_bp.route("/leads/<int:job_id>/contacts/<int:contact_id>", methods=["DELETE"])
def unlink_lead_contact(job_id: int, contact_id: int) -> ResponseReturnValue:
    """Detach a contact from a lead. Their logged calls and messages stay."""
    with get_repos() as repos:
        if not repos["job_contacts"].unlink(current_user_id(), job_id, contact_id):
            return jsonify({"error": "Not linked"}), 404
        repos["conn"].commit()
    return jsonify({"ok": True})


# ai: claude-opus-5-5 | 2026-11-11
@leads_bp.route("/interactions", methods=["POST"])
def create_interaction() -> ResponseReturnValue:
    """Log a call, message, note or transcript against a contact, a lead, or both."""
    try:
        body = InteractionCreate.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return invalid_request_response(exc)
    now_iso = _now_iso()
    fields = {
        **body.to_fields(now_iso),
        "contact_id": body.contact_id,
        "job_id": body.job_id,
        "response_received_at": body.response_received_at,
    }
    with get_repos() as repos:
        created = repos["interactions"].add(current_user_id(), fields, now_iso)
        if created is None:
            return jsonify({"error": "Contact or lead not found"}), 404
        repos["conn"].commit()
    return jsonify(created), 201


# ai: claude-opus-5-5 | 2026-11-11 -- also replaces PATCH /contact-outreach/<id>
# (marking a reply received is response_received_at).
@leads_bp.route("/interactions/<int:interaction_id>", methods=["PATCH"])
def update_interaction(interaction_id: int) -> ResponseReturnValue:
    """Edit a logged interaction, or record (or clear) when a reply came back."""
    try:
        body = InteractionUpdate.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return invalid_request_response(exc)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return jsonify({"error": "No fields to update"}), 400
    with get_repos() as repos:
        updated = repos["interactions"].update(current_user_id(), interaction_id, fields)
        if updated is None:
            return jsonify({"error": "Interaction not found"}), 404
        repos["conn"].commit()
    return jsonify(updated)


# ai: claude-opus-5-5 | 2026-11-11 -- also replaces DELETE /contact-outreach/<id>
@leads_bp.route("/interactions/<int:interaction_id>", methods=["DELETE"])
def delete_interaction(interaction_id: int) -> ResponseReturnValue:
    """Delete a logged interaction."""
    with get_repos() as repos:
        if not repos["interactions"].delete(current_user_id(), interaction_id):
            return jsonify({"error": "Interaction not found"}), 404
        repos["conn"].commit()
    return jsonify({"ok": True})
