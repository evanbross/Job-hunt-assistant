# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-18 -- moved out of test_lead_suggestions_routes.py
"""Shared by the suggested-leads tests (test_lead_suggestions_routes.py,
test_lead_conversation_capture.py): an export's worth of invented conversations,
a signed-in client for the routes, and helpers to upload and read the inbox."""

from __future__ import annotations

import io
from typing import Any

import pytest
from flask import Flask

import config
from backend.db import PostgresUserAccountRepository, get_connection
from routes.contacts import contacts_bp
from routes.lead_suggestions import lead_suggestions_bp
from routes.leads import leads_bp
from tests.db_roles import runtime_database_url
from tests.linkedin_export_fixtures import (
    ME,
    message_row,
    messages_csv,
    profile_url,
    zip_of,
)
from tests.login_session import sign_in

# Each other person started a conversation with the user; Pat (the user) also
# started one, which is never a candidate.
ROWS = [
    message_row("c-rita", "Rita Recruiter", profile_url("rita-r") + "/", ME, "2026-11-01 09:00:00", "Open to a Staff role?"),
    message_row("c-rita", "Pat Seeker", ME, profile_url("rita-r"), "2026-11-01 10:00:00", "Tell me more"),
    message_row("c-tom", "Tom Titled", profile_url("tom-t"), ME, "2026-11-02 09:00:00", "Hi Pat"),
    message_row("c-nora", "Nora Notone", profile_url("nora-n"), ME, "2026-11-03 09:00:00", "Coffee?"),
    message_row("c-sam", "Sam Stranger", profile_url("sam-s"), ME, "2026-11-04 09:00:00", "We're hiring a Principal", subject="Principal role"),
    message_row("c-sam", "Pat Seeker", ME, profile_url("sam-s"), "2026-11-04 12:00:00", "Interested"),
    message_row("c-mine", "Pat Seeker", ME, profile_url("friend"), "2026-11-05 09:00:00", "Hey"),
]


def suggestions_client(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, int]:
    """A signed-in test client for the suggestions, leads and contacts routes, and its user id."""
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        account = PostgresUserAccountRepository(conn).create_pending(
            "suggestions@example.com", "seed-suggestions", "2099-01-01"
        )
        conn.commit()
    finally:
        conn.close()
    app = Flask(__name__)
    app.secret_key = "test"
    for blueprint in (lead_suggestions_bp, leads_bp, contacts_bp):
        app.register_blueprint(blueprint, url_prefix="/api")
    client = app.test_client()
    with client.session_transaction() as sess:
        sign_in(sess, account["id"])

    return client, int(account["id"])


def upload(client: Any, rows: list[dict[str, str]], *, use_ai: bool = False, days: int = 0, name: str = "messages.csv") -> Any:
    raw = zip_of({"messages.csv": messages_csv(rows)}) if name.endswith(".zip") else messages_csv(rows).encode()
    return client.post(
        "/api/lead-suggestions/linkedin-messages",
        data={"file": (io.BytesIO(raw), name), "use_ai": "true" if use_ai else "false", "active_within_days": str(days)},
        content_type="multipart/form-data",
    )


def inbox_tab(client: Any, status: str = "suggested") -> dict[str, Any]:
    response = client.get(f"/api/lead-suggestions?status={status}")
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def suggestion_named(client: Any, name: str, status: str = "suggested") -> dict[str, Any]:
    return next(s for s in inbox_tab(client, status)["suggestions"] if s["counterpart_name"] == name)
