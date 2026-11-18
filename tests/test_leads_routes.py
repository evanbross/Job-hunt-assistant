# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Recruiter lead tracking routes (routes/leads.py, plus the contacts and
Applications-sheet routes it touches), exercised end to end against the real
schema: request -> validation -> LeadService -> repositories -> Postgres."""

from __future__ import annotations

import io
from typing import Any

import pytest
from flask import Flask

import config
from backend.ai_provider import AIProviderError
from backend.db import (
    PostgresApplicationStageRepository,
    PostgresUserAccountRepository,
    PostgresUserSettingsRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user
from backend.leads.call_notes import CallNotesProposal
from routes.applications import applications_bp
from routes.contacts import contacts_bp
from routes.leads import leads_bp
from tests.db_roles import runtime_database_url
from tests.login_session import sign_in

_RECRUITER = {"full_name": "Rita Recruiter", "company": "Agency Co", "position": "Recruiter", "phone": "555-0100"}


@pytest.fixture
def client_and_user(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, int]:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        account = PostgresUserAccountRepository(conn).create_pending(
            "leads@example.com", "seed-leads", "2099-01-01"
        )
        conn.commit()
    finally:
        conn.close()
    app = Flask(__name__)
    app.secret_key = "test"
    for blueprint in (leads_bp, contacts_bp, applications_bp):
        app.register_blueprint(blueprint, url_prefix="/api")
    client = app.test_client()
    with client.session_transaction() as sess:
        sign_in(sess, account["id"])

    return client, int(account["id"])


def _with_repo(pg_dsn: str, user_id: int, action: Any) -> Any:
    conn = get_connection(pg_dsn)
    try:
        set_current_user(conn.cursor(), user_id)
        result = action(conn)
        conn.commit()
        return result
    finally:
        conn.close()


def _create_lead(client: Any, **overrides: Any) -> int:
    body = {
        "company": "Client Co",
        "job_title": "Staff Engineer",
        "new_contact": _RECRUITER,
        "first_interaction": {
            "channel": "phone",
            "summary": "Intro call",
            "occurred_at": "2026-11-10T16:00:00Z",
            "body_html": "<p>Rate <strong>90/hr</strong></p><script>alert(1)</script>",
        },
        "summary_html": "<p>Contract-to-hire</p><img src=x onerror=alert(1)>",
    }
    body.update(overrides)
    response = client.post("/api/leads", json=body)
    assert response.status_code == 201, response.get_json()
    return int(response.get_json()["id"])


# ai: claude-opus-5-5 | 2026-11-11
def test_create_lead_builds_job_stage_contact_link_and_first_call(client_and_user: tuple[Any, int]) -> None:
    client, _ = client_and_user
    job_id = _create_lead(client)

    detail = client.get(f"/api/leads/{job_id}").get_json()
    lead = detail["lead"]
    assert (lead["company"], lead["job_title"], lead["origin"], lead["source"]) == (
        "Client Co", "Staff Engineer", "recruiter_contact", "Recruiter",
    )
    assert lead["stage"] == "recruiter_reached_out"
    assert lead["summary_html"] == "<p>Contract-to-hire</p>"  # the <img> is stripped
    assert [(p["full_name"], p["role"], p["is_primary"], p["phone"]) for p in detail["people"]] == [
        ("Rita Recruiter", "agency_recruiter", True, "555-0100")
    ]
    [call] = detail["interactions"]
    assert (call["channel"], call["kind"], call["direction"], call["summary"]) == ("phone", "call", "inbound", "Intro call")
    assert call["occurred_at"] == "2026-11-10T16:00:00+00:00"
    assert call["body_html"] == "<p>Rate <strong>90/hr</strong></p>"
    assert call["contact_name"] == "Rita Recruiter"
    assert [s["status"] for s in detail["stages"]] == ["recruiter_reached_out"]

    board = client.get("/api/leads").get_json()
    [row] = board["leads"]
    assert (row["id"], row["stage"], row["is_closed"], row["primary_contact_name"]) == (
        job_id, "recruiter_reached_out", False, "Rita Recruiter",
    )
    assert board["stale_after_days"] == config.DEFAULT_LEAD_STALE_AFTER_DAYS

    # It is tracked like any application: on the sheet, with its origin and "via".
    sheet = client.get("/api/applications").get_json()
    [sheet_row] = [r for r in sheet["rows"] if r["row_number"] == job_id]
    assert (sheet_row["origin"], sheet_row["via"]) == ("recruiter_contact", "Rita Recruiter")


# ai: claude-opus-5-5 | 2026-11-11
def test_a_second_lead_from_the_same_recruiter_reuses_their_contact(client_and_user: tuple[Any, int]) -> None:
    client, _ = client_and_user
    first = _create_lead(client)
    second = _create_lead(client, job_title="Principal Engineer", first_interaction=None)

    first_people = client.get(f"/api/leads/{first}").get_json()["people"]
    second_people = client.get(f"/api/leads/{second}").get_json()["people"]
    assert first_people[0]["id"] == second_people[0]["id"]

    activity = client.get(f"/api/contacts/{first_people[0]['id']}/activity").get_json()
    assert sorted(j["job_id"] for j in activity["jobs"]) == sorted([first, second])
    assert {j["stage"] for j in activity["jobs"]} == {"recruiter_reached_out"}
    assert [i["summary"] for i in activity["interactions"]] == ["Intro call"]


# ai: claude-opus-5-5 | 2026-11-11
@pytest.mark.parametrize(
    ("overrides", "status"),
    [
        ({"stage": "not_a_stage"}, 400),
        ({"contact_id": 999_999, "new_contact": None}, 404),
        ({"contact_id": 1}, 400),  # both contact_id and new_contact
        ({"company": ""}, 400),
        ({"url": "javascript:alert(1)"}, 400),
        ({"next_follow_up": "next week"}, 400),
    ],
)
def test_create_lead_rejects_bad_input_without_writing_anything(
    client_and_user: tuple[Any, int], overrides: dict[str, Any], status: int
) -> None:
    client, _ = client_and_user
    body = {"company": "Client Co", "new_contact": _RECRUITER, **overrides}
    assert client.post("/api/leads", json=body).status_code == status
    assert client.get("/api/leads").get_json()["leads"] == []
    assert client.get("/api/contacts").get_json()["contacts"] == []


# ai: claude-opus-5-5 | 2026-11-11
def test_create_lead_with_an_already_tracked_url_points_at_that_job(client_and_user: tuple[Any, int]) -> None:
    client, _ = client_and_user
    existing = _create_lead(client, url="https://jobs.example.com/123")
    response = client.post(
        "/api/leads", json={"company": "Client Co", "url": "https://jobs.example.com/123", "new_contact": _RECRUITER}
    )
    assert response.status_code == 409
    assert response.get_json()["existing_job_id"] == existing


# ai: claude-opus-5-5 | 2026-11-11
def test_update_lead_sanitizes_summary_and_sets_or_clears_follow_up(client_and_user: tuple[Any, int]) -> None:
    client, _ = client_and_user
    job_id = _create_lead(client)

    response = client.patch(
        f"/api/leads/{job_id}",
        json={"summary_html": '<p onclick="x()">Final round <a href="javascript:x">here</a></p>', "next_follow_up": "2026-11-20"},
    )
    assert response.status_code == 200
    lead = client.get(f"/api/leads/{job_id}").get_json()["lead"]
    assert lead["summary_html"] == '<p>Final round <a rel="noopener noreferrer">here</a></p>'
    assert lead["next_follow_up"] == "2026-11-20"

    assert client.patch(f"/api/leads/{job_id}", json={"next_follow_up": None}).status_code == 200
    assert client.get(f"/api/leads/{job_id}").get_json()["lead"]["next_follow_up"] is None
    assert client.patch(f"/api/leads/{job_id}", json={"company": None}).status_code == 400
    assert client.patch(f"/api/leads/{job_id}", json={}).status_code == 400
    assert client.patch("/api/leads/999999", json={"job_title": "x"}).status_code == 404


# ai: claude-opus-5-5 | 2026-11-11
def test_board_marks_closed_and_stale_leads(
    client_and_user: tuple[Any, int], pg_dsn: str
) -> None:
    client, user_id = client_and_user
    quiet = _create_lead(client, stage_date="2026-01-05", first_interaction=None)
    withdrawn = _create_lead(client, job_title="Other role", stage_date="2026-01-05", first_interaction=None)
    _with_repo(
        pg_dsn, user_id,
        lambda conn: PostgresApplicationStageRepository(conn).insert(
            user_id, {"job_id": withdrawn, "status": "withdrew", "date": "2026-01-06"}
        ),
    )
    _with_repo(
        pg_dsn, user_id,
        lambda conn: PostgresUserSettingsRepository(conn).set(user_id, "lead_stale_after_days", "3"),
    )

    board = client.get("/api/leads").get_json()
    rows = {row["id"]: row for row in board["leads"]}
    assert board["stale_after_days"] == 3
    assert (rows[quiet]["is_closed"], rows[quiet]["is_stale"]) == (False, True)
    assert rows[quiet]["last_activity_date"] == "2026-01-05"
    assert (rows[withdrawn]["stage"], rows[withdrawn]["is_closed"], rows[withdrawn]["is_stale"]) == (
        "withdrew", True, False,
    )


# ai: claude-opus-5-5 | 2026-11-11
def test_interactions_log_edit_mark_reply_and_delete(client_and_user: tuple[Any, int]) -> None:
    client, _ = client_and_user
    contact_id = client.post("/api/contacts", json=_RECRUITER).get_json()["id"]

    created = client.post(
        "/api/interactions",
        json={"contact_id": contact_id, "channel": "linkedin", "direction": "inbound", "kind": "message",
              "summary": "Has a few roles", "occurred_at": "2026-11-09T12:00:00-05:00"},
    )
    assert created.status_code == 201
    interaction = created.get_json()
    assert interaction["occurred_at"] == "2026-11-09T17:00:00+00:00"

    # A role-less conversation shows on the board until it is tied to a lead.
    board = client.get("/api/leads").get_json()
    assert [c["full_name"] for c in board["open_conversations"]] == ["Rita Recruiter"]

    patched = client.patch(
        f"/api/interactions/{interaction['id']}",
        json={"summary": "Two roles, both remote", "response_received_at": "2026-11-10T09:00:00Z"},
    )
    assert patched.status_code == 200
    assert patched.get_json()["summary"] == "Two roles, both remote"
    assert patched.get_json()["response_received_at"] == "2026-11-10T09:00:00+00:00"
    assert client.patch(f"/api/interactions/{interaction['id']}", json={"channel": None}).status_code == 400
    assert client.patch(f"/api/interactions/{interaction['id']}", json={"contact_id": 1}).status_code == 400

    assert client.post("/api/interactions", json={"channel": "phone"}).status_code == 400
    assert client.post("/api/interactions", json={"channel": "fax", "contact_id": contact_id}).status_code == 400
    assert client.post("/api/interactions", json={"channel": "phone", "job_id": 999_999}).status_code == 404

    assert client.delete(f"/api/interactions/{interaction['id']}").status_code == 200
    assert client.delete(f"/api/interactions/{interaction['id']}").status_code == 404


# ai: claude-opus-5-5 | 2026-11-11
def test_link_a_second_contact_as_primary_then_unlink(client_and_user: tuple[Any, int]) -> None:
    client, _ = client_and_user
    job_id = _create_lead(client)
    hank = client.post("/api/contacts", json={"full_name": "Hank Manager", "company": "Client Co"}).get_json()["id"]

    linked = client.post(f"/api/leads/{job_id}/contacts", json={"contact_id": hank, "role": "hiring_manager", "is_primary": True})
    assert linked.status_code == 200
    people = client.get(f"/api/leads/{job_id}").get_json()["people"]
    assert [(p["full_name"], p["is_primary"]) for p in people] == [("Hank Manager", True), ("Rita Recruiter", False)]

    assert client.delete(f"/api/leads/{job_id}/contacts/{hank}").status_code == 200
    assert client.delete(f"/api/leads/{job_id}/contacts/{hank}").status_code == 404
    assert client.post(f"/api/leads/{job_id}/contacts", json={"contact_id": 999_999}).status_code == 404


# ai: claude-opus-5-5 | 2026-11-11
def test_contacts_added_by_hand_can_be_renamed_and_deduplicate(client_and_user: tuple[Any, int], pg_dsn: str) -> None:
    client, user_id = client_and_user
    created = client.post("/api/contacts", json={**_RECRUITER, "full_name": "Rita Recrutier", "keep_informed": True})
    assert created.status_code == 201
    contact_id = created.get_json()["id"]

    renamed = client.patch(f"/api/contacts/{contact_id}", json={"full_name": "Rita Recruiter", "phone": "555-0199"})
    assert renamed.status_code == 200, renamed.get_json()
    [contact] = client.get("/api/contacts").get_json()["contacts"]
    assert (contact["full_name"], contact["phone"], contact["keep_informed"], contact["source"]) == (
        "Rita Recruiter", "555-0199", True, "manual",
    )

    duplicate = client.post("/api/contacts", json=_RECRUITER)
    assert duplicate.status_code == 409
    assert duplicate.get_json()["contact_id"] == contact_id
    assert client.patch(f"/api/contacts/{contact_id}", json={"keep_informed": None}).status_code == 400

    # A LinkedIn-imported contact keeps LinkedIn's details.
    _with_repo(pg_dsn, user_id, lambda conn: conn.cursor().execute(
        "UPDATE contacts SET source = 'linkedin_import' WHERE id = %s", (contact_id,)
    ))
    assert client.patch(f"/api/contacts/{contact_id}", json={"full_name": "Someone Else"}).status_code == 400


# ai: claude-opus-5-5 | 2026-11-11
def test_logged_outreach_is_an_outbound_message_in_the_contacts_list(client_and_user: tuple[Any, int]) -> None:
    client, _ = client_and_user
    contact_id = client.post("/api/contacts", json=_RECRUITER).get_json()["id"]

    logged = client.post(f"/api/contacts/{contact_id}/outreach", json={"channel": "linkedin", "sent_at": "2026-11-01T12:00:00Z"})
    assert logged.status_code == 201
    entry = logged.get_json()
    assert (entry["sent_at"], entry["channel"], entry["response_received_at"]) == (
        "2026-11-01T12:00:00+00:00", "linkedin", None,
    )
    # Marking the reply goes through the shared interactions route.
    assert client.patch(f"/api/interactions/{entry['id']}", json={"response_received_at": "2026-11-02T08:00:00Z"}).status_code == 200

    [contact] = client.get("/api/contacts").get_json()["contacts"]
    assert contact["outreach"] == [
        {"id": entry["id"], "sent_at": "2026-11-01T12:00:00+00:00",
         "response_received_at": "2026-11-02T08:00:00+00:00", "channel": "linkedin"}
    ]
    assert contact["last_outbound_at"] == "2026-11-01T12:00:00+00:00"


# ai: claude-opus-5-5 | 2026-11-11
def test_a_row_added_on_the_applications_sheet_is_a_manual_origin_job(client_and_user: tuple[Any, int]) -> None:
    client, _ = client_and_user
    assert client.post("/api/applications", json={"company": "Hand Added Co", "job_title": "Engineer"}).status_code == 201
    [row] = client.get("/api/applications").get_json()["rows"]
    assert (row["origin"], row["via"]) == ("manual", None)


# ai: claude-opus-5-5 | 2026-11-11 -- phase 2: call-notes import. The AI call is
# faked at routes.leads.extract_call_notes; the DB writes are real.
def test_call_notes_are_kept_verbatim_and_the_proposal_is_only_returned(
    client_and_user: tuple[Any, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = client_and_user
    job_id = _create_lead(client, first_interaction=None)
    seen: dict[str, Any] = {}

    def fake_extract(user_id: int, notes: str, lead_context: dict[str, Any], today: Any) -> CallNotesProposal:
        seen.update(notes=notes, context=lead_context)
        return CallNotesProposal(job_title="Principal Engineer", next_follow_up="2026-11-20", summary_bullets=["Rate 95/hr"])

    monkeypatch.setattr("routes.leads.extract_call_notes", fake_extract)
    response = client.post(f"/api/leads/{job_id}/call-notes", json={"text": "Rate <95>/hr\nPrincipal level"})

    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["ai_error"] is None
    assert body["proposal"]["job_title"] == "Principal Engineer"
    assert seen["notes"] == "Rate <95>/hr\nPrincipal level"
    assert seen["context"] == {"hiring company": "Client Co", "role": "Staff Engineer", "current stage": "recruiter_reached_out"}
    # Saved as a transcript entry, escaped; the lead itself is untouched.
    detail = client.get(f"/api/leads/{job_id}").get_json()
    [entry] = detail["interactions"]
    assert (entry["kind"], entry["source"]) == ("transcript", "notetaker")
    assert entry["body_html"] == "<p>Rate &lt;95&gt;/hr<br>Principal level</p>"
    assert (detail["lead"]["job_title"], detail["lead"]["next_follow_up"]) == ("Staff Engineer", None)


def test_call_notes_upload_and_an_ai_failure_still_keep_the_notes(
    client_and_user: tuple[Any, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = client_and_user
    job_id = _create_lead(client, first_interaction=None)

    def failing_extract(*args: Any) -> CallNotesProposal:
        raise AIProviderError("Rate limited")

    monkeypatch.setattr("routes.leads.extract_call_notes", failing_extract)
    response = client.post(
        f"/api/leads/{job_id}/call-notes",
        data={"file": (io.BytesIO(b"Recruiter: send your resume"), "notes.txt"), "channel": "phone"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    assert response.get_json()["proposal"] is None
    assert response.get_json()["ai_error"] == "Rate limited"
    [entry] = client.get(f"/api/leads/{job_id}").get_json()["interactions"]
    assert (entry["channel"], entry["body_html"]) == ("phone", "<p>Recruiter: send your resume</p>")


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({"json": {"text": "   "}}, 400),
        ({"json": {"text": "x", "channel": "fax"}}, 400),
        ({"data": {"file": (io.BytesIO(b"%PDF"), "notes.pdf")}, "content_type": "multipart/form-data"}, 400),
    ],
    ids=["blank", "bad-channel", "pdf"],
)
def test_call_notes_rejects_bad_input_without_saving(
    client_and_user: tuple[Any, int], kwargs: dict[str, Any], status: int
) -> None:
    client, _ = client_and_user
    job_id = _create_lead(client, first_interaction=None)
    assert client.post(f"/api/leads/{job_id}/call-notes", **kwargs).status_code == status
    assert client.get(f"/api/leads/{job_id}").get_json()["interactions"] == []
    assert client.post("/api/leads/999999/call-notes", json={"text": "x"}).status_code == 404
