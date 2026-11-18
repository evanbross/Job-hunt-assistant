# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Wiring for the browser extension's two servers: the HTTP import listener and the
capture-queue WebSocket.

These are separate servers on their own ports, not Flask routes, so they are not part
of `create_app()`. Every callback below receives the user id the listener already
authenticated from the extension's bearer token; none resolves a default account. They
run on listener/WebSocket threads with no Flask session, so each opens its own
short-lived DB connection. Moved out of app.py on 2026-11-05.

Two ways to run them:
- the desktop launcher (app.py) calls start_extension_servers() inside its own process;
- a hosted deployment runs them as their own process, `python -m web.extension_listener`
  (main() below), behind the reverse proxy (docs/cloud-deployment-readiness-plan.md 1.3).
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from datetime import UTC, datetime
from typing import Any

import config
from backend.application_answer_suggestions import suggest_application_answers
from backend.contacts.outreach_drafts import claim_draft_for_page
from backend.db import BROWSER_QUEUE_CLAIM_BATCH_SIZE
from backend.error_tracking import init_error_tracking
from backend.extension_auth.token_service import ExtensionTokenService
from backend.extension_auth.ws_tickets import WebSocketTicketStore
from backend.logging_setup import configure_logging
from backend.queue_websocket import QueueWebSocketServer, start_queue_websocket_server
from backend.resume.resume_file_service import ResumeFileService
from backend.scanning.import_listener import (
    JobImportListener,
    get_job_import_listener_config,
    start_job_import_listener,
)
from backend.security.client_ip import Network, parse_trusted_proxies
from backend.utils.utils import get_repos
from routes.applications import submit_closed_req_capture
from routes.import_ import queue_import_capture
from web.serving import check_startup_secrets, verify_database_ready

logger = logging.getLogger(__name__)


# ai: claude-sonnet-5 | 2026-09-26
def authenticate_extension_token(token: str) -> int | None:
    """Called from the import listener thread on every request: resolve a
    presented extension token to the user who owns it, or None."""
    with get_repos() as repos:
        identity = ExtensionTokenService(repos["extension_tokens"]).authenticate(token)
        repos["conn"].commit()
    return identity.user_id if identity else None


def _claim_browser_capture_queue(user_id: int) -> list[dict]:
    with get_repos(user_id) as repos:
        items = repos["browser_capture_queue"].claim_pending(
            user_id, limit=BROWSER_QUEUE_CLAIM_BATCH_SIZE
        )
        repos["conn"].commit()
    return items


# ai: claude-sonnet-5 | 2026-07-22
def _browser_capture_queue_has_pending(user_id: int) -> bool:
    """Called from the queue WebSocket server's watcher thread."""
    with get_repos(user_id) as repos:
        return repos["browser_capture_queue"].has_pending(user_id)


# ai: claude-sonnet-4-6 | 2026-07-02
def _handle_listener_capture(user_id: int, body: dict) -> tuple[dict, int]:
    """Routes captures from the browser extension by purpose.

    Closed-req checks (see routes.applications.run_req_closed_scan_task) post the
    raw captured HTML for classification rather than going through the job-import
    pipeline, which expects structured job listings.
    """
    if body.get("purpose") == "closed_req":
        return submit_closed_req_capture(user_id, body)
    return queue_import_capture(body, trigger="listener", user_id=user_id)


# ai: claude-sonnet-5 | 2026-08-13
def _get_application_profile_for_listener(user_id: int) -> dict | None:
    with get_repos(user_id) as repos:
        return repos["application_profile"].get(user_id)


# ai: claude-sonnet-5 | 2026-08-13
# ai: claude-sonnet-5 | 2026-09-26 -- the PDF now lives, encrypted, in the
# authenticated user's own resume row (backend/resume/resume_file_service.py),
# not in a shared file, so this can only ever return that user's resume.
def get_resume_file_bytes(user_id: int) -> tuple[bytes, str] | None:
    with get_repos(user_id) as repos:
        return ResumeFileService(repos["resume"]).load_file(user_id)


# ai: claude-sonnet-5 | 2026-08-13
def _get_application_answers_for_listener(user_id: int) -> list[dict]:
    with get_repos(user_id) as repos:
        return repos["application_answers"].get_all(user_id)


# ai: claude-opus-5-5 | 2026-11-17
def learn_application_answers(
    user_id: int, answers: list[tuple[str, str]], platform: str | None
) -> list[dict]:
    """Save the (question, answer) pairs the user ticked in the extension's
    autofill review panel. One transaction, so a failure part-way saves none
    of them rather than leaving the batch half-applied."""
    with get_repos(user_id) as repos:
        saved = [
            repos["application_answers"].upsert(
                user_id,
                question_text=question_text,
                answer_text=answer_text,
                platform=platform,
            )
            for question_text, answer_text in answers
        ]
        repos["conn"].commit()
    return saved


# ai: claude-opus-5-5 | 2026-11-17
def record_application_answer_use(user_id: int, answer_ids: list[int]) -> int:
    """Count a use of each saved answer the extension's autofill just filled."""
    with get_repos(user_id) as repos:
        recorded = repos["application_answers"].record_use(user_id, answer_ids)
        repos["conn"].commit()
    return recorded


# ai: claude-opus-5-5 | 2026-10-14
def _claim_linkedin_outreach_draft(user_id: int, page_url: str) -> dict | None:
    """Called when the extension finishes loading a LinkedIn profile page."""
    with get_repos(user_id) as repos:
        claimed = claim_draft_for_page(user_id, page_url, repos, datetime.now(UTC))
        repos["conn"].commit()
    if claimed is None:
        return None
    return {
        "contact_id": claimed.contact_id,
        "full_name": claimed.full_name,
        "message": claimed.message,
    }


# ai: claude-opus-5-5 | 2026-11-05
def start_extension_servers(
    trusted_proxies: tuple[Network, ...],
) -> tuple[JobImportListener | None, QueueWebSocketServer | None]:
    """Start the import listener and the queue WebSocket server, sharing one ticket
    store: the listener issues WebSocket tickets and the WebSocket server redeems them,
    so both must live in the same process. Either may be None if it is disabled or
    could not start (see each start_* function)."""
    ws_tickets = WebSocketTicketStore()
    import_listener = start_job_import_listener(
        authenticate_extension_token,
        _handle_listener_capture,
        claim_queue=_claim_browser_capture_queue,
        get_profile=_get_application_profile_for_listener,
        get_resume_file=get_resume_file_bytes,
        get_answers=_get_application_answers_for_listener,
        learn_answers=learn_application_answers,
        suggest_answers=suggest_application_answers,
        record_answer_use=record_application_answer_use,
        claim_outreach_draft=_claim_linkedin_outreach_draft,
        ws_tickets=ws_tickets,
        trusted_proxies=trusted_proxies,
    )
    queue_ws_server = start_queue_websocket_server(
        ws_tickets.redeem, _browser_capture_queue_has_pending
    )
    return import_listener, queue_ws_server


# ai: claude-opus-5-5 | 2026-11-06
def bound_port_problem(
    import_listener: JobImportListener | None,
    queue_ws_server: QueueWebSocketServer | None,
    listener_config: dict[str, Any],
) -> str | None:
    """Why a standalone listener process must not keep running, or None if it's fine.

    On the desktop each server falls back to an OS-assigned port when its configured one
    is taken, and app.py registers whatever it got with the dev Caddy. A hosted reverse
    proxy has fixed upstreams (deploy/Caddyfile), so a fallback there would look like a
    healthy process while every extension request failed: refuse it instead
    (COMMON_CODING_STANDARDS.md, auto-selected runtime values)."""
    if import_listener is None or queue_ws_server is None:
        return (
            "The extension listener or its WebSocket server did not start: it is disabled"
            " (Settings > System > Import Listener) or its host could not be used."
        )
    expected = (int(listener_config["port"]), int(listener_config["ws_port"]))
    actual = (import_listener.port, queue_ws_server.port)
    if actual != expected:
        return (
            f"The extension servers bound ports {actual} instead of the configured {expected},"
            " which the reverse proxy forwards to. Free the configured ports (or change the"
            " proxy and the setting together) and restart."
        )
    return None


# ai: claude-opus-5-5 | 2026-11-06
def main() -> int:
    """Run the extension servers as their own process until SIGTERM/SIGINT."""
    configure_logging()
    init_error_tracking("listener")  # ai: claude-opus-5-5 | 2026-11-08
    check_startup_secrets()  # resume files and tokens are decrypted here
    verify_database_ready()
    import_listener, queue_ws_server = start_extension_servers(
        parse_trusted_proxies(config.TRUSTED_PROXIES)
    )
    try:
        problem = bound_port_problem(
            import_listener, queue_ws_server, get_job_import_listener_config()
        )
        if problem:
            logger.critical("%s", problem)
            return 1
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        logger.info(
            "Extension listener serving on :%s, WebSocket on :%s.",
            import_listener.port if import_listener else None,
            queue_ws_server.port if queue_ws_server else None,
        )
        while not stop.wait(1.0):  # a timed wait, so signals are handled promptly on Windows too
            pass
        return 0
    finally:
        for server in (import_listener, queue_ws_server):
            if server is not None:
                server.stop()


if __name__ == "__main__":
    sys.exit(main())
