# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-08
"""Error tracking for the hosted processes, with PII kept out (cloud plan 4.3).

Off unless SENTRY_DSN is set. The DSN can point at Sentry's hosted service or anything
that speaks its protocol (a self-hosted GlitchTip or Sentry); the SDK is the same.

What reaches the error tracker is deliberately thin, because this app's data is job
descriptions, resumes, cover letters, email bodies and the user's own credentials:

* no request bodies, cookies, query strings or headers beyond a short allowlist;
* no local variables in stack frames (they hold exactly that data);
* no user identity beyond the numeric account id;
* every message (exception text, log line, breadcrumb) has email addresses and long
  token-like strings redacted and is cut to a few hundred characters, so a whole JD
  pasted into an exception message never leaves the server.

Stack traces (file, line, function, surrounding source) still go, which is what makes
an error fixable.

Celery beat tasks are reported as cron monitors, so the tracker alerts when a
scheduled task stops running -- a dead worker or beat shows up as missed check-ins.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from backend.app_mode import is_hosted

logger = logging.getLogger(__name__)

DSN_ENV = "SENTRY_DSN"
ENVIRONMENT_ENV = "SENTRY_ENVIRONMENT"
RELEASE_ENV = "SENTRY_RELEASE"

MAX_MESSAGE_LENGTH = 300
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Long runs of key/token alphabet that include a digit: API keys, session tokens,
# Fernet keys, JWT parts -- but not long snake_case names or file paths.
_TOKEN = re.compile(r"(?=[A-Za-z_\-=]*\d)[A-Za-z0-9_\-=]{32,}")
_KEPT_HEADERS = frozenset({"user-agent", "content-type", "content-length", "accept", "host"})
# Event keys that can carry arbitrary application data; dropped outright.
_DROPPED_EVENT_KEYS = ("extra", "server_name")


def redact(text: str) -> str:
    """Emails and token-like strings replaced, then cut to MAX_MESSAGE_LENGTH."""
    cleaned = _TOKEN.sub("[redacted]", _EMAIL.sub("[email]", text))
    if len(cleaned) > MAX_MESSAGE_LENGTH:
        return cleaned[:MAX_MESSAGE_LENGTH] + "...[truncated]"
    return cleaned


def _scrub_request(request: dict[str, Any]) -> dict[str, Any]:
    url = request.get("url")
    headers = request.get("headers") or {}
    return {
        "method": request.get("method"),
        "url": url.split("?", 1)[0] if isinstance(url, str) else None,
        "headers": {k: v for k, v in headers.items() if k.lower() in _KEPT_HEADERS},
    }


def _scrub_breadcrumb_fields(crumb: dict[str, Any]) -> dict[str, Any]:
    if isinstance(crumb.get("message"), str):
        crumb["message"] = redact(crumb["message"])
    data = crumb.get("data")
    if isinstance(data, dict):
        # HTTP breadcrumbs: keep method/status, strip query strings from URLs.
        kept = {k: data[k] for k in ("method", "status_code") if k in data}
        if isinstance(data.get("url"), str):
            kept["url"] = data["url"].split("?", 1)[0]
        crumb["data"] = kept
    return crumb


def scrub_event(event: dict[str, Any], _hint: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """before_send: reduce an event to what is safe to send (see the module docstring)."""
    for key in _DROPPED_EVENT_KEYS:
        event.pop(key, None)
    if isinstance(event.get("request"), dict):
        event["request"] = _scrub_request(event["request"])
    user = event.get("user")
    event["user"] = {"id": user["id"]} if isinstance(user, dict) and "id" in user else {}
    if isinstance(event.get("message"), str):
        event["message"] = redact(event["message"])
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        # The formatted message only: params are the raw values that went into it.
        formatted = logentry.get("formatted") or logentry.get("message") or ""
        event["logentry"] = {"message": redact(str(formatted))}
    for exc in (event.get("exception") or {}).get("values") or []:
        if isinstance(exc.get("value"), str):
            exc["value"] = redact(exc["value"])
        for frame in (exc.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    for thread in (event.get("threads") or {}).get("values") or []:
        for frame in (thread.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict):
        breadcrumbs["values"] = [_scrub_breadcrumb_fields(b) for b in breadcrumbs.get("values") or []]
    return event


def scrub_breadcrumb(crumb: dict[str, Any], _hint: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """before_breadcrumb: the same redaction, applied as each breadcrumb is recorded."""
    return _scrub_breadcrumb_fields(crumb)


def init_error_tracking(component: str, environ: Mapping[str, str] | None = None) -> bool:
    """Start reporting errors for this process (`component` is web, listener, worker
    or beat). True if enabled; False when SENTRY_DSN is unset, which a hosted process
    warns about, since errors would then only be in the logs."""
    env = os.environ if environ is None else environ
    dsn = env.get(DSN_ENV, "").strip()
    if not dsn:
        if is_hosted():
            logger.warning(
                "%s is not set: errors are only in this process's logs. Set it to report"
                " them to an error tracker (deploy/README.md \"Logs, errors and monitoring\").",
                DSN_ENV,
            )
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=env.get(ENVIRONMENT_ENV, "").strip() or "production",
        release=env.get(RELEASE_ENV, "").strip() or None,
        # What the SDK collects in the first place; scrub_event is the second layer.
        data_collection={
            "user_info": False,
            "cookies": {"mode": "off"},
            "http_headers": {"request": {"mode": "allowlist", "terms": sorted(_KEPT_HEADERS)}},
            "http_bodies": [],
            "url_query_params": {"mode": "off"},
            "gen_ai": {"inputs": False, "outputs": False},
            "database_query_data": False,
            "queues": False,
            "stack_frame_variables": False,
        },
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
        traces_sample_rate=0.0,  # errors only: no performance tracing
        integrations=[
            FlaskIntegration(),
            CeleryIntegration(monitor_beat_tasks=True),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
    )
    sentry_sdk.set_tag("component", component)
    logger.info("Error tracking on for %s.", component)
    return True
