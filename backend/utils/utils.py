# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-08-13
"""Generic, no-domain-owner helpers shared across every blueprint.

This file used to hold every cross-blueprint helper regardless of domain
(scanning, scoring, email, cover letter). Those have been split out into
their own domain packages (see backend/utils/company_urls.py,
backend/scanning/, backend/scoring/, backend/email/, backend/cover_letter/).
What remains here is genuinely domain-less: DB connection handling, generic
type coercion, AI-provider error formatting, and the CORS/CSRF guard — used
by nearly every route file, owned by none of them. Keeping this file free of
domain imports is deliberate: several domain packages already import from
this module, so it must stay a leaf to avoid import cycles (see
backend/utils/url_safety.py and backend/utils/company_urls.py for the same
pattern applied to other shared helpers).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Literal
from urllib.parse import urlparse

import psycopg2
from flask import has_request_context, jsonify, session
from flask.typing import ResponseReturnValue
from psycopg2 import pool as psycopg2_pool
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

import config
from backend.ai_provider import AIProviderError
from backend.db import open_repositories
from backend.db.job_queue_query import QUEUE_STATUSES, TRIAGE_STATUSES
from backend.db.row_level_security import set_current_user, set_current_user_if_active

logger = logging.getLogger(__name__)

_MAX_CHAT_MESSAGES = 12
_MAX_CHAT_MESSAGE_LENGTH = 4000
_MAX_RAW_CHAT_MESSAGES = 200

# ai: claude-opus-5-5 | 2026-10-23 — derived from the Job Queue's own view
# definitions (backend/db/job_queue_query.py) instead of a second copy.
ACTIVE_JOB_STATUSES: frozenset[str] = frozenset(QUEUE_STATUSES + TRIAGE_STATUSES)

FIT_PANEL_STATUSES: frozenset[str] = frozenset(
    {
        "pending_review",
        "triage",
        "skipped",
    }
)

# ai: claude-sonnet-5 | 2026-10-01
# Statuses set by a direct, deliberate user action (Mark Applied / Skip) that a
# later request must never silently overwrite with a status recomputed from a
# stale pre-action snapshot (duplicate-dismiss re-scoring, bulk re-evaluate).
# See dismiss_duplicate and run_re_evaluate_task for the race this guards.
USER_COMMITTED_JOB_STATUSES: frozenset[str] = frozenset(
    {
        "applied",
        "skipped",
    }
)


# ai: claude-sonnet-5 | 2026-10-18
class DatabaseUnavailableError(RuntimeError):
    """The RLS identity for this connection could not be established, even
    after one reconnect-and-retry. Raised instead of yielding repos, because a
    pooled connection (backend/db/connection.py) whose identity setup failed
    must never be handed to a caller -- there is no way to tell, from the
    caller's side, whether it is still carrying a previous request's tenant
    identity."""


# ai: claude-opus-5-5 | 2026-11-09
class AccountUnavailableError(RuntimeError):
    """The login session names an account that has been disabled by an admin or no
    longer exists (plan 4.5). Raised by get_repos() for a session-derived identity, so
    every request that touches data ends that session (web/factory.py's handler)."""


@contextmanager
def get_repos(user_id: int | None = None) -> Generator[dict, None, None]:
    """Open a DB connection and yield repos; always closes the connection on exit.

    Also sets the Postgres row-level-security identity (backend/db/row_level_security.py)
    for this connection, so every tenant table's own RLS policy -- not just each
    repository's WHERE clause -- is scoped to one account:

    - *user_id*, when the caller already knows it (a Celery task, a scheduler tick, a
      background listener callback -- anything running outside a Flask request).
    - Otherwise the current Flask session's user_id, when called from inside a request
      (nearly every route). This covers the whole routes/ surface with no per-route change.
    - Otherwise left unset, which is fail-closed: policies deny every tenant-table row
      rather than defaulting to unrestricted.

    Set outside any transaction (conn.outside_transaction(), autocommit), before the
    caller's own queries run, so a later `repos["conn"].rollback()` elsewhere in this
    connection's lifetime (e.g. an IntegrityError retry) can't silently undo it -- only
    a SET made inside a still-uncommitted transaction is rolled back with it. (This used
    to be BEGIN + SET + COMMIT; autocommit gives the same guarantee in one round trip.)

    Raises DatabaseUnavailableError instead of yielding if the identity cannot be
    established even after a reconnect-and-retry (see backend/db/connection.py's
    discard(), called here on that path): with connection pooling, a connection
    returned to the pool in this state could otherwise be handed to a later,
    different caller still carrying this request's (or an even earlier request's)
    tenant identity -- fail-open, not fail-closed. 2026-10-18 full code review.

    Raises AccountUnavailableError when the identity comes from the Flask session and
    that account is disabled or gone: checked in the same statement that sets the
    identity (set_current_user_if_active), so it costs nothing extra per request, and a
    disabled account's open sessions stop working on their next request, not at expiry.
    An explicit *user_id* is not checked: a worker finishing a task, or an admin reading
    a disabled account's task failures, act on the account deliberately.

    Also raises DatabaseUnavailableError (instead of a raw psycopg2.pool.PoolError)
    if every pooled connection is checked out past the configured acquisition wait
    (config.DB_POOL_ACQUIRE_TIMEOUT_SECONDS) -- a traffic burst against a healthy
    database should look the same to a caller as any other transient unavailability.
    """
    try:
        repos = open_repositories(config.DATABASE_URL)
    except psycopg2_pool.PoolError as exc:
        raise DatabaseUnavailableError("No database connection available") from exc
    conn = repos["conn"]
    effective_user_id = user_id
    if effective_user_id is None and has_request_context():
        effective_user_id = session.get("user_id")
    # ai: claude-opus-5-5 | 2026-11-09 -- a session's account must still be active
    from_session = user_id is None and effective_user_id is not None

    def establish_identity() -> bool:
        with conn.outside_transaction():  # ai: claude-opus-5-5 | 2026-10-23
            if from_session:
                return set_current_user_if_active(conn.cursor(), effective_user_id)
            set_current_user(conn.cursor(), effective_user_id)
            return True

    try:
        active = establish_identity()
    except psycopg2.OperationalError:
        # ai: claude-sonnet-5 | 2026-10-13
        # A pooled connection (see backend/db/connection.py) can go stale while
        # idle between requests -- e.g. Neon suspends its compute after a few
        # minutes with no activity and drops every connection sitting in the
        # pool. Reconnect and retry once before giving up, so the common case
        # (first request after the user has been away) doesn't fail just
        # because the connection it happened to check out was stale.
        conn.reconnect()
        try:
            active = establish_identity()
        except psycopg2.Error as exc:
            conn.discard()
            raise DatabaseUnavailableError(
                "Could not establish the database session identity"
            ) from exc
    except psycopg2.Error as exc:
        conn.discard()
        raise DatabaseUnavailableError(
            "Could not establish the database session identity"
        ) from exc
    if not active:
        conn.close()  # its identity is already cleared, so it is safe to reuse
        raise AccountUnavailableError(f"account {effective_user_id} is disabled or deleted")
    try:
        yield repos
    finally:
        conn.close()


# ai: claude-sonnet-5 | 2026-08-20
def current_user_id() -> int:
    """The logged-in user's user_accounts.id, read from the session.

    Only call this from a route already covered by app.py's
    _require_app_login before_request guard — every route not on that
    guard's allowlist is, so any handler that reaches get_repos() for a
    per-user table can call this safely. Raises if called with no session,
    which means the guard isn't actually covering this route — a bug to
    surface loudly, not paper over with a default.
    """
    user_id = session.get("user_id")
    if user_id is None:
        raise RuntimeError(
            "current_user_id() called with no user_id in session — this "
            "route is not behind app.py's _require_app_login guard"
        )
    return user_id


# ai: claude-sonnet-5 | 2026-09-26
class CandidateProfileMissingError(RuntimeError):
    """The user has not saved a candidate profile yet."""


# ai: claude-sonnet-5 | 2026-09-26
def get_candidate_profile(user_id: int, repos: dict) -> str:
    """The user's own candidate profile text. Replaces the old file-path
    resolver: the profile is per-user data in the database, so there is no
    shared default that one account's prompts could accidentally read from
    another's. Raises CandidateProfileMissingError if none is saved."""
    row = repos["candidate_profiles"].get(user_id)
    content = str((row or {}).get("content") or "").strip()
    if not content:
        raise CandidateProfileMissingError(
            "No candidate profile is saved for this account. Add one under "
            "Settings > Career Profile > Profile Settings."
        )
    return content


# ai: claude-sonnet-5 | 2026-08-25
_CELERY_TASK_ACTIVE_STATES: frozenset[str] = frozenset({"STARTED", "PROGRESS", "RETRY"})
_CELERY_PENDING_GRACE_SECONDS = 30


# ai: claude-opus-5-5 | 2026-11-06 -- wall clock: submitted_at is shared between processes.
def is_celery_task_active(
    state: str,
    submitted_at: float | None,
    *,
    grace_seconds: float = _CELERY_PENDING_GRACE_SECONDS,
) -> bool:
    """True if a Celery task's *state* means it's genuinely still running.

    Every "is a scan/search/re-eval already running?" guard in this codebase
    records the task_id it dispatched (backend/active_tasks.py) and checks
    AsyncResult(task_id).state. That state string is ambiguous for PENDING:
    Celery reports PENDING both for a task that's genuinely queued and
    hasn't started yet, and for a task_id the result backend has no record
    of at all -- including one whose result already expired long after it
    actually finished (celery_app.py sets result_expires=3600). Treating
    every PENDING as active blocks new submissions forever once a stored
    task_id's result ages out past that TTL, even though nothing has
    actually been running for the last 59 minutes.

    submitted_at (time.time() at dispatch time, stored alongside the
    task_id) resolves the ambiguity: a task_id that's still within
    grace_seconds of being dispatched is treated as active even at PENDING
    (Celery just hasn't picked it up yet); once that window passes, PENDING
    is treated as stale rather than active. STARTED/PROGRESS/RETRY are
    unambiguous and always active regardless of submitted_at.

    Wall clock, not time.monotonic(): the record is written by one process
    (a web worker) and read by another (a Celery worker), and monotonic
    readings mean nothing outside the process that took them.
    """
    if state in _CELERY_TASK_ACTIVE_STATES:
        return True
    if state == "PENDING":
        return submitted_at is not None and (time.time() - submitted_at) < grace_seconds
    return False


def parse_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    raise ValueError("Invalid boolean value")


def parse_positive_int(value: object, *, default: int, field_name: str) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return parsed


def safe_error_message(exc: BaseException, fallback: str) -> str:
    if isinstance(exc, AIProviderError):
        return str(exc) or fallback
    return fallback


def ai_error_http_status(exc: BaseException, fallback_status: int = 500) -> int:
    if isinstance(exc, AIProviderError):
        status_code = exc.status_code
        if status_code == 429:
            return 429
        if isinstance(status_code, int) and 400 <= status_code <= 599:
            return 502
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return 429
    if isinstance(status_code, int) and 400 <= status_code <= 599:
        return 502
    return fallback_status


# ai: claude-sonnet-5 | 2026-09-29
class ChatMessage(BaseModel):
    """One well-formed turn in an AI chat transcript."""

    model_config = ConfigDict(extra="ignore")

    role: Literal["user", "assistant"]
    content: str

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content must not be blank")
        if len(value) > _MAX_CHAT_MESSAGE_LENGTH:
            raise ValueError(
                f"content exceeds the maximum length of {_MAX_CHAT_MESSAGE_LENGTH} characters"
            )
        return value


# ai: claude-sonnet-5 | 2026-09-29
def parse_chat_messages(raw: object) -> list[ChatMessage]:
    """Validate a chat request's raw `messages` list into well-formed
    ChatMessage turns, keeping only the most recent _MAX_CHAT_MESSAGES.

    Intended as a pydantic `field_validator(..., mode="before")` for every AI
    chat route's `messages` field (fit-chat, pitch/cover-letter/resume-tailor
    chat, Toptal bio chat). A malformed entry -- wrong shape, wrong role,
    blank content -- is dropped rather than rejecting the whole request (the
    frontend always sends a well-formed transcript, so this tolerates a
    stray bad entry, not attacker input), but an oversized or entirely
    invalid payload is still rejected outright rather than silently reaching
    the AI provider as an empty conversation.
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("No messages provided")
    if len(raw) > _MAX_RAW_CHAT_MESSAGES:
        raise ValueError(f"Too many messages (maximum {_MAX_RAW_CHAT_MESSAGES})")
    result: list[ChatMessage] = []
    for item in raw[-_MAX_CHAT_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        try:
            result.append(ChatMessage.model_validate(item))
        except ValidationError:
            continue
    if not result:
        raise ValueError("No valid messages provided")
    return result


# ai: claude-sonnet-5 | 2026-09-29
def invalid_request_response(exc: ValidationError) -> ResponseReturnValue:
    """Sanitized 400 for a route's pydantic.ValidationError.

    A single hand-written business-rule check (a field_validator or
    model_validator that raises ValueError("some friendly message")) surfaces
    that exact message -- it was written for the end user, same as the
    hand-parsed checks this replaces. Anything else (missing/extra fields,
    type coercion failures, multiple errors at once) falls back to field
    paths only; exc.errors() also echoes the rejected input values, which
    isn't safe to expose as-is."""
    errors = exc.errors()
    if len(errors) == 1 and errors[0]["type"] == "value_error":
        original = errors[0].get("ctx", {}).get("error")
        return jsonify({"error": str(original) if original else errors[0]["msg"]}), 400
    fields = sorted({".".join(str(part) for part in err["loc"]) for err in errors})
    return jsonify({"error": "Invalid request", "fields": fields}), 400


def is_trusted_api_write_origin(
    source: str | None, self_origin: str, cors_origins: list[str]
) -> bool:
    """True if *source* (an Origin or Referer header value) is safe to accept a write from.

    *source* is same-origin (matches *self_origin*) or explicitly CORS-allowlisted.
    A missing *source* is always trusted: non-browser clients (tests, curl, internal
    tooling) never send a forgeable Origin/Referer, so there is nothing to check, and
    a malicious cross-origin browser request always sets one of the two.
    """
    if not source:
        return True
    parsed = urlparse(source)
    source_origin = f"{parsed.scheme}://{parsed.netloc}"
    return source_origin == self_origin or source_origin in cors_origins
