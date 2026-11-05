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
short-lived DB connection. Moved out of app.py on 2026-11-05 so the listener can later
run as its own process (docs/cloud-deployment-readiness-plan.md 1.3).
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.contacts.outreach_drafts import claim_draft_for_page
from backend.db import BROWSER_QUEUE_CLAIM_BATCH_SIZE
from backend.extension_auth.token_service import ExtensionTokenService
from backend.extension_auth.ws_tickets import WebSocketTicketStore
from backend.queue_websocket import QueueWebSocketServer, start_queue_websocket_server
from backend.resume.resume_file_service import ResumeFileService
from backend.scanning.import_listener import (
    JobImportListener,
    start_job_import_listener,
)
from backend.security.client_ip import Network
from backend.utils.utils import get_repos
from routes.applications import submit_closed_req_capture
from routes.import_ import queue_import_capture


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


# ai: claude-sonnet-5 | 2026-08-13
def _learn_application_answer(
    user_id: int, question_text: str, answer_text: str, platform: str | None
) -> dict:
    """Called when the extension's autofill content script learns a new
    free-text answer from the user."""
    with get_repos(user_id) as repos:
        answer = repos["application_answers"].upsert(
            user_id,
            question_text=question_text,
            answer_text=answer_text,
            platform=platform,
        )
        repos["conn"].commit()
    return answer


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
        learn_answer=_learn_application_answer,
        claim_outreach_draft=_claim_linkedin_outreach_draft,
        ws_tickets=ws_tickets,
        trusted_proxies=trusted_proxies,
    )
    queue_ws_server = start_queue_websocket_server(
        ws_tickets.redeem, _browser_capture_queue_has_pending
    )
    return import_listener, queue_ws_server
