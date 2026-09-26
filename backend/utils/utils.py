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

import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from flask import session

import config
from backend.ai_provider import AIProviderError
from backend.db import open_repositories
from backend.settings_service import get_setting

ACTIVE_JOB_STATUSES: frozenset[str] = frozenset(
    {
        "pending_review",
        "triage",
        "possible_duplicate",
    }
)

FIT_PANEL_STATUSES: frozenset[str] = frozenset(
    {
        "pending_review",
        "triage",
        "skipped",
    }
)


@contextmanager
def get_repos() -> Generator[dict, None, None]:
    """Open a DB connection and yield repos; always closes the connection on exit."""
    repos = open_repositories(config.DATABASE_URL)
    try:
        yield repos
    finally:
        repos["conn"].close()


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


# ai: claude-sonnet-5 | 2026-09-01
def resolve_candidate_profile_path(user_id: int, repos: dict) -> Path:
    """Resolve the on-disk path for *user_id*'s candidate profile file,
    honoring an explicit candidate_profile_path setting override.

    Single source of truth for the "profile_path_str or
    DEFAULT_CANDIDATE_PROFILE_PATH" fallback every prompt-building call site
    needs before calling config.load_candidate_profile(path) -- previously
    duplicated at each call site, which let three of them drift into calling
    config.load_candidate_profile() with no argument at all.
    """
    profile_path_str = get_setting("candidate_profile_path", user_id, repos)
    return Path(profile_path_str) if profile_path_str else config.DEFAULT_CANDIDATE_PROFILE_PATH


# ai: claude-sonnet-5 | 2026-08-25
_CELERY_TASK_ACTIVE_STATES: frozenset[str] = frozenset({"STARTED", "PROGRESS", "RETRY"})
_CELERY_PENDING_GRACE_SECONDS = 30


def is_celery_task_active(
    state: str, submitted_at: float | None, *, grace_seconds: float = _CELERY_PENDING_GRACE_SECONDS
) -> bool:
    """True if a Celery task's *state* means it's genuinely still running.

    Every "is a scan/search/re-eval already running?" guard in this codebase
    tracks a task_id in an in-memory per-user dict and checks
    AsyncResult(task_id).state. That state string is ambiguous for PENDING:
    Celery reports PENDING both for a task that's genuinely queued and
    hasn't started yet, and for a task_id the result backend has no record
    of at all -- including one whose result already expired long after it
    actually finished (celery_app.py sets result_expires=3600). Treating
    every PENDING as active blocks new submissions forever once a stored
    task_id's result ages out past that TTL, even though nothing has
    actually been running for the last 59 minutes.

    submitted_at (time.monotonic() at dispatch time, stored alongside the
    task_id) resolves the ambiguity: a task_id that's still within
    grace_seconds of being dispatched is treated as active even at PENDING
    (Celery just hasn't picked it up yet); once that window passes, PENDING
    is treated as stale rather than active. STARTED/PROGRESS/RETRY are
    unambiguous and always active regardless of submitted_at.
    """
    if state in _CELERY_TASK_ACTIVE_STATES:
        return True
    if state == "PENDING":
        return submitted_at is not None and (time.monotonic() - submitted_at) < grace_seconds
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
