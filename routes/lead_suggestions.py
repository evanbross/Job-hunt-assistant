# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Suggested leads routes (recruiter lead tracking, phase 2): import a LinkedIn
messages.csv, let the AI check the conversations Contacts can't rule on (a
background task, one per account at a time), and accept, dismiss or restore each
suggestion. See backend/leads/lead_suggestions.py for the flow."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, get_args

from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    model_validator,
)

from backend.active_tasks import get_active_tasks
from backend.ai_provider import AIProviderError
from backend.celery_app import celery_app
from backend.leads.lead_service import ContactNotFoundError, InvalidStageError
from backend.leads.lead_suggestions import (
    AI_BATCH_SIZE,
    AcceptChoice,
    LeadNotFoundError,
    SuggestionNotFoundError,
    accept_suggestion,
    classify_conversations,
    import_conversations,
)
from backend.leads.linkedin_messages import (
    MessagesFormatError,
    read_messages_upload,
    scan_export,
)
from backend.task_progress import TaskProgress, run_status
from backend.user_events import UserEventTopic, get_user_events
from backend.utils.utils import (
    current_user_id,
    get_repos,
    invalid_request_response,
    parse_bool,
    safe_error_message,
)

logger = logging.getLogger(__name__)

lead_suggestions_bp = Blueprint("lead_suggestions", __name__)

# ai: claude-opus-5-5 | 2026-11-11
LEAD_SUGGESTION_CHECK_TASK_KIND = "lead_suggestion_check"
# The inbox tabs; accepted rows have become contacts and leads, so they aren't one.
ListableStatus = Literal["suggested", "unclassified", "not_recruiter", "dismissed"]
_DEFAULT_ACTIVE_DAYS = 365
_MAX_ACTIVE_DAYS = 3650
_MAX_SHORT_TEXT = 300


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ai: claude-opus-5-5 | 2026-11-11
@dataclass
class SuggestionCheckState:
    """The AI check's progress, as GET /lead-suggestions reports it under "check"."""

    running: bool = False
    checked: int = 0
    suggested: int = 0
    error: str | None = None
    message: str = ""


def check_waiting_conversations(user_id: int, progress: TaskProgress[SuggestionCheckState]) -> None:
    """Classify the account's waiting conversations a batch at a time, holding no
    DB connection during each AI call. Every batch's rows leave 'unclassified', so
    the loop ends; a provider failure stops it with the rest still waiting, to be
    retried with "Check with AI" again."""
    with progress.update() as state:
        state.running = True
        state.message = "Checking conversations with AI..."
    events = get_user_events()
    while True:
        with get_repos(user_id) as repos:
            batch = repos["lead_suggestions"].list_unclassified(user_id, AI_BATCH_SIZE)
        if not batch:
            break
        try:
            verdicts = classify_conversations(user_id, batch)
        except (AIProviderError, ValueError) as exc:
            logger.warning("Lead-suggestion AI check stopped for user %s: %s", user_id, exc)
            with progress.update() as state:
                state.error = safe_error_message(exc, "The AI couldn't check those conversations.")
            break
        with get_repos(user_id) as repos:
            for suggestion_id, verdict in verdicts.items():
                repos["lead_suggestions"].record_verdict(user_id, suggestion_id, verdict)
            repos["conn"].commit()
        events.publish(user_id, UserEventTopic.LEAD_SUGGESTIONS)
        with progress.update() as state:
            state.checked += len(verdicts)
            state.suggested += sum(1 for v in verdicts.values() if v["status"] == "suggested")
    with progress.update() as state:
        state.running = False
        state.message = (
            f"Checked {state.checked} conversation{'' if state.checked == 1 else 's'};"
            f" {state.suggested} look like recruiters."
        )
    events.publish(user_id, UserEventTopic.LEAD_SUGGESTIONS)


# ai: claude-opus-5-5 | 2026-11-11
@celery_app.task(bind=True)
def run_lead_suggestion_check_task(self, user_id: int) -> dict:
    progress = TaskProgress.for_task(self, SuggestionCheckState())
    check_waiting_conversations(user_id, progress)
    return asdict(progress.state)


def _start_check(user_id: int) -> bool:
    """Start the AI check unless this account already has one running."""
    task_id = get_active_tasks().submit_unless_running(
        LEAD_SUGGESTION_CHECK_TASK_KIND, user_id, lambda: run_lead_suggestion_check_task.delay(user_id).id
    )
    return task_id is not None


# ai: claude-opus-5-5 | 2026-11-11
class MessagesImportForm(BaseModel):
    """The upload's form fields. active_within_days 0 means every conversation,
    however old."""

    model_config = ConfigDict(extra="forbid")

    active_within_days: Annotated[int, Field(ge=0, le=_MAX_ACTIVE_DAYS)] = _DEFAULT_ACTIVE_DAYS
    use_ai: bool = True


@lead_suggestions_bp.route("/lead-suggestions/linkedin-messages", methods=["POST"])
def import_linkedin_messages() -> ResponseReturnValue:
    """Import messages.csv (or the whole export zip): store the candidate
    conversations, rule on what Contacts already knows, and start the AI check of
    the rest when asked. Re-importing a newer export refreshes, never duplicates."""
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "Choose messages.csv or the export's .zip file"}), 400
    try:
        form = MessagesImportForm(
            active_within_days=request.form.get("active_within_days", _DEFAULT_ACTIVE_DAYS),
            use_ai=parse_bool(request.form.get("use_ai"), default=True),
        )
    except ValidationError as exc:
        return invalid_request_response(exc)
    try:
        text = read_messages_upload(upload.filename or "", upload.stream.read())
        active_since = (
            datetime.now(UTC) - timedelta(days=form.active_within_days) if form.active_within_days else None
        )
        scan = scan_export(text, active_since)
    except MessagesFormatError as exc:
        return jsonify({"error": str(exc)}), 400

    user_id = current_user_id()
    with get_repos() as repos:
        outcome = import_conversations(user_id, repos, scan, _now_iso())
        repos["conn"].commit()
        waiting = repos["lead_suggestions"].counts(user_id)["unclassified"]
    check_started = form.use_ai and waiting > 0 and _start_check(user_id)
    get_user_events().publish(user_id, UserEventTopic.LEAD_SUGGESTIONS)
    return jsonify({**asdict(outcome), "waiting_for_ai": waiting, "check_started": check_started}), 201


# ai: claude-opus-5-5 | 2026-11-11
@lead_suggestions_bp.route("/lead-suggestions")
def list_lead_suggestions() -> ResponseReturnValue:
    """One inbox tab (?status=, default suggested), the count in every status,
    and the AI check's progress."""
    status = request.args.get("status", "suggested")
    if status not in get_args(ListableStatus):
        return jsonify({"error": f"Unknown status: {status}"}), 400
    user_id = current_user_id()
    with get_repos() as repos:
        suggestions = repos["lead_suggestions"].list_by_status(user_id, status)
        counts = repos["lead_suggestions"].counts(user_id)
    check = run_status(LEAD_SUGGESTION_CHECK_TASK_KIND, user_id, asdict(SuggestionCheckState()))
    return jsonify({"suggestions": suggestions, "counts": counts, "check": check})


# ai: claude-opus-5-5 | 2026-11-11
@lead_suggestions_bp.route("/lead-suggestions/check", methods=["POST"])
def start_lead_suggestion_check() -> ResponseReturnValue:
    """Run the AI check over every conversation still waiting for one."""
    user_id = current_user_id()
    if not _start_check(user_id):
        return jsonify({"error": "A check is already running"}), 409
    return jsonify({"status": "started"}), 202


# ai: claude-opus-5-5 | 2026-11-11
class AcceptBody(BaseModel):
    """lead: 'none' (contact and conversation only), 'new' (company / job_title;
    a blank company is "Undisclosed"), or 'existing' (job_id)."""

    model_config = ConfigDict(extra="forbid")

    lead: Literal["none", "new", "existing"] = "none"
    company: Annotated[str, Field(max_length=_MAX_SHORT_TEXT)] = ""
    job_title: Annotated[str, Field(max_length=_MAX_SHORT_TEXT)] = ""
    job_id: StrictInt | None = None
    contact_company: Annotated[str, Field(max_length=_MAX_SHORT_TEXT)] = ""
    contact_position: Annotated[str, Field(max_length=_MAX_SHORT_TEXT)] = ""

    @model_validator(mode="after")
    def _existing_needs_a_job(self) -> "AcceptBody":
        if self.lead == "existing" and self.job_id is None:
            raise ValueError("job_id is required to add to an existing lead")
        return self


@lead_suggestions_bp.route("/lead-suggestions/<int:suggestion_id>/accept", methods=["POST"])
def accept_lead_suggestion(suggestion_id: int) -> ResponseReturnValue:
    """Accept a suggestion: contact (marked recruiter) + conversation logged, and
    optionally a new lead or a place on an existing one. One transaction."""
    try:
        body = AcceptBody.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return invalid_request_response(exc)
    choice = AcceptChoice(
        lead=body.lead,
        company=body.company,
        job_title=body.job_title,
        existing_job_id=body.job_id,
        contact_company=body.contact_company,
        contact_position=body.contact_position,
    )
    user_id = current_user_id()
    with get_repos() as repos:
        try:
            result = accept_suggestion(user_id, repos, suggestion_id, choice, _now_iso())
        except SuggestionNotFoundError:
            repos["conn"].rollback()
            return jsonify({"error": "Suggestion not found"}), 404
        except (LeadNotFoundError, ContactNotFoundError):
            repos["conn"].rollback()
            return jsonify({"error": "Lead not found"}), 404
        except InvalidStageError as exc:
            repos["conn"].rollback()
            return jsonify({"error": str(exc)}), 400
        repos["conn"].commit()
    get_user_events().publish(user_id, UserEventTopic.LEAD_SUGGESTIONS)
    return jsonify(result)


# ai: claude-opus-5-5 | 2026-11-11
def _move(suggestion_id: int, to_status: str, from_statuses: tuple[str, ...], decided: bool) -> ResponseReturnValue:
    user_id = current_user_id()
    with get_repos() as repos:
        moved = repos["lead_suggestions"].move(
            user_id, suggestion_id, to_status, from_statuses, _now_iso() if decided else None
        )
        if not moved:
            return jsonify({"error": "Suggestion not found"}), 404
        repos["conn"].commit()
    get_user_events().publish(user_id, UserEventTopic.LEAD_SUGGESTIONS)
    return jsonify({"ok": True, "status": to_status})


@lead_suggestions_bp.route("/lead-suggestions/<int:suggestion_id>/dismiss", methods=["POST"])
def dismiss_lead_suggestion(suggestion_id: int) -> ResponseReturnValue:
    """Dismiss a conversation from any open tab."""
    return _move(suggestion_id, "dismissed", ("suggested", "unclassified", "not_recruiter"), decided=True)


@lead_suggestions_bp.route("/lead-suggestions/<int:suggestion_id>/restore", methods=["POST"])
def restore_lead_suggestion(suggestion_id: int) -> ResponseReturnValue:
    """Put a dismissed or ruled-out conversation back among the suggestions."""
    return _move(suggestion_id, "suggested", ("dismissed", "not_recruiter"), decided=False)
