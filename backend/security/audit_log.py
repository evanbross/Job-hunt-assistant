# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""A per-account security log: credential and account-identity events (login,
password/email changes, extension tokens, Google connect/disconnect). Read via
GET /api/audit-log (routes/session_auth.py); written only through record_event()
below, never directly from a route, so the set of event types that can appear is
exactly EVENTS -- a route cannot introduce an ad hoc, unreviewed event name.

Not a general application activity log (job imports, scans, etc.) -- this is
specifically the "could someone else be in my account" trail a user or an
investigation would want, kept small and readable on purpose.

Best-effort: a failure to record must never fail (or roll back) the request that
triggered it. Losing one log entry is a much smaller problem than a broken login."""

from __future__ import annotations

import json
import logging
from typing import Any, Final

import psycopg2
from flask import has_request_context, request

logger = logging.getLogger(__name__)

# ai: claude-sonnet-5 | 2026-09-27
LOGIN_SUCCESS: Final = "login_success"
LOGIN_FAILURE: Final = "login_failure"
PASSWORD_CHANGED: Final = "password_changed"
PASSWORD_RESET_REQUESTED: Final = "password_reset_requested"
PASSWORD_RESET_COMPLETED: Final = "password_reset_completed"
EMAIL_CHANGE_REQUESTED: Final = "email_change_requested"
EMAIL_CHANGE_CONFIRMED: Final = "email_change_confirmed"
ACCOUNT_REGISTERED: Final = "account_registered"
EXTENSION_TOKEN_CREATED: Final = "extension_token_created"
EXTENSION_TOKEN_REVOKED: Final = "extension_token_revoked"
GOOGLE_CONNECTED: Final = "google_connected"
GOOGLE_DISCONNECTED: Final = "google_disconnected"
ACCOUNT_DELETION_REQUESTED: Final = "account_deletion_requested"
ACCOUNT_DELETION_CANCELLED: Final = "account_deletion_cancelled"
DATA_EXPORTED: Final = "data_exported"
# ai: claude-opus-5-5 | 2026-11-07 -- recorded on the admin who did it (plan 4.6)
REGISTRATION_INVITE_CREATED: Final = "registration_invite_created"
REGISTRATION_INVITE_REVOKED: Final = "registration_invite_revoked"

EVENTS: Final[frozenset[str]] = frozenset(
    {
        LOGIN_SUCCESS,
        LOGIN_FAILURE,
        PASSWORD_CHANGED,
        PASSWORD_RESET_REQUESTED,
        PASSWORD_RESET_COMPLETED,
        EMAIL_CHANGE_REQUESTED,
        EMAIL_CHANGE_CONFIRMED,
        ACCOUNT_REGISTERED,
        EXTENSION_TOKEN_CREATED,
        EXTENSION_TOKEN_REVOKED,
        GOOGLE_CONNECTED,
        GOOGLE_DISCONNECTED,
        ACCOUNT_DELETION_REQUESTED,
        ACCOUNT_DELETION_CANCELLED,
        DATA_EXPORTED,
        REGISTRATION_INVITE_CREATED,
        REGISTRATION_INVITE_REVOKED,
    }
)


def record_event(
    repos: dict[str, Any], user_id: int, event_type: str, detail: dict[str, Any] | None = None
) -> None:
    """Record one audit_log row for user_id. Swallows its own errors (logged as a
    warning) -- the caller's own operation (a login, a token revoke) must complete
    regardless of whether this write succeeds."""
    if event_type not in EVENTS:
        raise ValueError(f"Not a recognized audit event type: {event_type!r}")
    ip_address = request.remote_addr if has_request_context() else None
    try:
        repos["audit_log"].insert(
            user_id, event_type, json.dumps(detail) if detail is not None else None, ip_address
        )
    except (psycopg2.Error, KeyError) as exc:
        # KeyError: a caller's repos dict doesn't have "audit_log" wired in (e.g. a test
        # fixture that fakes only the repos it needs) -- degrade the same as a DB failure.
        logger.warning("Could not record audit event %s for user %s: %s", event_type, user_id, exc)
