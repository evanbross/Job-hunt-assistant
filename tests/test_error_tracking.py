# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-08
"""backend/error_tracking.py (cloud plan 4.3): off without a DSN, and what it sends
carries no request data, local variables, emails or tokens. The last test runs a real
Flask error through the real Sentry SDK into a capturing transport."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
import sentry_sdk
from flask import Flask
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

import config
from backend import error_tracking
from backend.error_tracking import (
    MAX_MESSAGE_LENGTH,
    init_error_tracking,
    redact,
    scrub_event,
)

_EMAIL = "jane.doe@example.com"
_TOKEN = "sk-ant-api03-" + "Ab1" * 16
_JD = "Senior Platform Engineer responsible for Kubernetes clusters " * 20
_DSN = "https://public@errors.example.invalid/1"


def test_redact_removes_emails_and_tokens_and_caps_length() -> None:
    text = redact(f"failed for {_EMAIL} with key {_TOKEN}: {_JD}")

    assert _EMAIL not in text and _TOKEN not in text
    assert "[email]" in text and "[redacted]" in text
    assert len(text) <= MAX_MESSAGE_LENGTH + len("...[truncated]")


def test_redact_keeps_code_names_and_paths_readable() -> None:
    text = "repair_existing_job_company_from_url in /app/backend/scanning/import_repair.py"

    assert redact(text) == text


def test_scrub_event_drops_request_data_locals_and_extras() -> None:
    event: dict[str, Any] = {
        "request": {
            "method": "POST", "url": f"https://jobs.example.com/api/x?token={_TOKEN}",
            "data": {"jd": _JD}, "cookies": {"session": "abc"}, "query_string": f"token={_TOKEN}",
            "headers": {"User-Agent": "ua", "Cookie": "session=abc", "Authorization": "Bearer x"},
        },
        "user": {"id": 7, "email": _EMAIL, "ip_address": "203.0.113.9"},
        "extra": {"payload": _JD},
        "server_name": "host-1",
        "logentry": {"message": "failed for %s", "params": [_EMAIL], "formatted": f"failed for {_EMAIL}"},
        "exception": {"values": [{"value": f"bad {_EMAIL}", "stacktrace": {"frames": [
            {"function": "handler", "vars": {"jd": _JD}},
        ]}}]},
        "breadcrumbs": {"values": [
            {"message": f"sent to {_EMAIL}", "data": {"url": "https://api.example/x?k=1", "method": "GET", "body": _JD}},
        ]},
    }

    scrubbed = json.dumps(scrub_event(event))

    for leaked in (_EMAIL, _TOKEN, _JD[:40], "session=abc", "Bearer", "203.0.113.9", "host-1"):
        assert leaked not in scrubbed
    assert event["request"] == {"method": "POST", "url": "https://jobs.example.com/api/x", "headers": {"User-Agent": "ua"}}
    assert event["user"] == {"id": 7}
    assert event["exception"]["values"][0]["stacktrace"]["frames"] == [{"function": "handler"}]
    assert event["breadcrumbs"]["values"][0]["data"] == {"method": "GET", "url": "https://api.example/x"}


def test_without_a_dsn_nothing_starts_and_only_a_hosted_process_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    started: list[dict[str, Any]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: started.append(kwargs))

    monkeypatch.setattr(config, "APP_MODE", "desktop")
    with caplog.at_level(logging.WARNING, logger=error_tracking.__name__):
        assert init_error_tracking("web", {}) is False
    assert not caplog.records

    monkeypatch.setattr(config, "APP_MODE", "hosted")
    with caplog.at_level(logging.WARNING, logger=error_tracking.__name__):
        assert init_error_tracking("web", {"SENTRY_DSN": "  "}) is False
    assert "SENTRY_DSN is not set" in caplog.text
    assert started == []


def _without_source_lines(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Events minus each frame's surrounding source code. That is application code, so
    it is fine to send -- but here it is this test's own source, which spells out the
    very values the test checks never leave."""
    source_keys = {"pre_context", "context_line", "post_context"}
    copied = json.loads(json.dumps(events))
    for event in copied:
        for exc in (event.get("exception") or {}).get("values") or []:
            for frame in (exc.get("stacktrace") or {}).get("frames") or []:
                for key in source_keys:
                    frame.pop(key, None)
    return copied


class _CapturingTransport(Transport):
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        for item in envelope.items:
            event = item.get_event()
            if event is not None:
                self.events.append(event)


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Iterator[_CapturingTransport]:
    """init_error_tracking with its real options, sending to a capturing transport."""
    capturing = _CapturingTransport()
    real_init = sentry_sdk.init
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: real_init(transport=capturing, **kwargs))
    assert init_error_tracking("web", {"SENTRY_DSN": _DSN}) is True
    yield capturing
    real_init()  # no DSN: a disabled client for the rest of the session


def test_a_real_flask_error_reaches_the_tracker_without_the_users_data(transport: _CapturingTransport) -> None:
    app = Flask(__name__)

    @app.post("/api/cover-letter")
    def generate() -> str:
        job_description = _JD  # a local the SDK must not capture  # noqa: F841
        logging.getLogger("routes.cover_letter").error("generation failed for %s", _EMAIL)
        raise ValueError(f"model rejected the prompt for {_EMAIL} using {_TOKEN}: {_JD}")

    app.test_client().post(
        f"/api/cover-letter?token={_TOKEN}", json={"jd": _JD, "email": _EMAIL},
        headers={"Cookie": "session=secret-session", "Authorization": f"Bearer {_TOKEN}"},
    )
    sentry_sdk.flush()

    assert len(transport.events) == 2  # the logged error and the exception
    sent = json.dumps(_without_source_lines(transport.events))
    # The request body never goes; a message keeps at most MAX_MESSAGE_LENGTH characters,
    # so no run of the JD that long can appear anywhere.
    for leaked in (_EMAIL, _TOKEN, _JD[:MAX_MESSAGE_LENGTH], "secret-session", "Bearer"):
        assert leaked not in sent
    [exc_event] = [e for e in transport.events if e.get("exception")]
    [exc] = exc_event["exception"]["values"]
    assert exc["type"] == "ValueError"
    frames = exc["stacktrace"]["frames"]
    assert any(f["function"] == "generate" for f in frames)
    assert all("vars" not in f for f in frames)
    assert exc_event["request"]["url"].endswith("/api/cover-letter")
    assert exc_event["tags"]["component"] == "web"
