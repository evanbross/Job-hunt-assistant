# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Recruiter lead tracking, phase 1: the interactions, job_contacts and leads
repositories, plus hand-added contacts -- every query run against the real schema
(TESTING_STANDARDS.md: no mocked databases for repository code)."""

from __future__ import annotations

from typing import Any

import psycopg2
import pytest

from backend.contacts.contact_identity import contact_identity_columns
from backend.db import (
    PostgresApplicationStageRepository,
    PostgresApplicationStageStatusRepository,
    PostgresContactRepository,
    PostgresInteractionRepository,
    PostgresJobContactRepository,
    PostgresJobRepository,
    PostgresLeadRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user

_TS = "2026-11-01T12:00:00+00:00"


def _new_user(conn: Any, email: str = "lead-owner@example.com") -> int:
    account = PostgresUserAccountRepository(conn).create_pending(email, f"seed-{email}", "2099-01-01")
    set_current_user(conn.cursor(), account["id"])
    return int(account["id"])


def _manual_contact(conn: Any, user_id: int, name: str, company: str = "Agency Co") -> int:
    contact_id, created = PostgresContactRepository(conn).create_manual(
        user_id, contact_identity_columns(name, company, "Recruiter", None), {}, _TS
    )
    assert created
    return contact_id


def _job(conn: Any, user_id: int, **fields: Any) -> int:
    row = {"company": "Client Co", "job_title": "Engineer", "status": "applied", "on_sheet": 1}
    row.update(fields)
    return PostgresJobRepository(conn).insert(user_id, row)


def _interaction(**fields: Any) -> dict[str, Any]:
    row = {"occurred_at": _TS, "channel": "phone", "direction": "inbound", "kind": "call"}
    row.update(fields)
    return row


# ai: claude-opus-5-5 | 2026-11-11
def test_interaction_add_list_update_delete(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        contact_id = _manual_contact(conn, user_id, "Rita Recruiter")
        job_id = _job(conn, user_id)
        repo = PostgresInteractionRepository(conn)

        call = repo.add(user_id, _interaction(contact_id=contact_id, job_id=job_id, summary="Screen"), _TS)
        note = repo.add(
            user_id,
            _interaction(job_id=job_id, kind="note", channel="other", direction="outbound",
                         occurred_at="2026-11-02T09:00:00+00:00", body_html="<p>Rate is 90/hr</p>"),
            _TS,
        )
        assert call is not None and note is not None
        assert call["summary"] == "Screen" and call["source"] == "manual"

        listed = repo.list_for_job(user_id, job_id)
        assert [row["id"] for row in listed] == [note["id"], call["id"]]  # newest first
        assert listed[1]["contact_name"] == "Rita Recruiter"
        assert listed[0]["contact_name"] is None

        by_contact = repo.list_for_contact(user_id, contact_id)
        assert [row["id"] for row in by_contact] == [call["id"]]
        assert by_contact[0]["job_company"] == "Client Co"

        updated = repo.update(user_id, call["id"], {"summary": "Recruiter screen", "channel": "video"})
        assert updated is not None
        assert (updated["summary"], updated["channel"]) == ("Recruiter screen", "video")
        with pytest.raises(ValueError):
            repo.update(user_id, call["id"], {"job_id": None})
        assert repo.delete(user_id, call["id"]) is True
        assert repo.get_by_id(user_id, call["id"]) is None
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-11
def test_interaction_refuses_another_users_contact_or_job(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        owner_id = _new_user(conn)
        contact_id = _manual_contact(conn, owner_id, "Owner's Recruiter")
        job_id = _job(conn, owner_id)
        other_id = _new_user(conn, "lead-other@example.com")
        repo = PostgresInteractionRepository(conn)

        assert repo.add(other_id, _interaction(contact_id=contact_id), _TS) is None
        assert repo.add(other_id, _interaction(job_id=job_id), _TS) is None
        with pytest.raises(ValueError):
            repo.add(other_id, _interaction(), _TS)
        conn.commit()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM interactions")
        assert cur.fetchone()["n"] == 0
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-11
def test_interaction_needs_a_contact_or_job_at_the_db(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        with pytest.raises(psycopg2.errors.CheckViolation):
            conn.cursor().execute(
                "INSERT INTO interactions (user_id, occurred_at, channel, direction, kind, created_at)"
                " VALUES (%s, %s, 'phone', 'inbound', 'call', %s)",
                (user_id, _TS, _TS),
            )
    finally:
        conn.rollback()
        conn.close()


# ai: claude-opus-5-5 | 2026-11-11
def test_job_contacts_link_relink_primary_and_unlink(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        rita = _manual_contact(conn, user_id, "Rita Recruiter")
        hank = _manual_contact(conn, user_id, "Hank Manager", "Client Co")
        job_id = _job(conn, user_id)
        repo = PostgresJobContactRepository(conn)

        assert repo.link(user_id, job_id, rita, role="agency_recruiter", is_primary=True, created_at=_TS)
        assert repo.link(user_id, job_id, hank, role="hiring_manager", is_primary=False, created_at=_TS)
        assert [(p["full_name"], p["is_primary"]) for p in repo.list_for_job(user_id, job_id)] == [
            ("Rita Recruiter", True),
            ("Hank Manager", False),
        ]
        assert repo.primary_names_by_job(user_id) == {job_id: "Rita Recruiter"}

        # Relinking updates in place, and a new primary demotes the old one.
        assert repo.link(user_id, job_id, hank, role="interviewer", is_primary=True, created_at=_TS)
        people = {p["full_name"]: p for p in repo.list_for_job(user_id, job_id)}
        assert people["Hank Manager"]["role"] == "interviewer" and people["Hank Manager"]["is_primary"]
        assert people["Rita Recruiter"]["is_primary"] is False

        jobs_for_rita = repo.list_for_contact(user_id, rita)
        assert [j["job_id"] for j in jobs_for_rita] == [job_id]
        assert jobs_for_rita[0]["job_status"] == "applied"
        assert jobs_for_rita[0]["latest_stage_status"] is None

        assert repo.unlink(user_id, job_id, rita) is True
        assert repo.unlink(user_id, job_id, rita) is False
        assert [p["full_name"] for p in repo.list_for_job(user_id, job_id)] == ["Hank Manager"]
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-11
def test_job_contacts_refuses_another_users_job_or_contact(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        owner_id = _new_user(conn)
        owner_contact = _manual_contact(conn, owner_id, "Owner's Recruiter")
        owner_job = _job(conn, owner_id)
        other_id = _new_user(conn, "lead-other@example.com")
        other_contact = _manual_contact(conn, other_id, "Other's Recruiter")
        other_job = _job(conn, other_id)
        repo = PostgresJobContactRepository(conn)

        assert repo.link(other_id, owner_job, other_contact, role="other", is_primary=True, created_at=_TS) is None
        assert repo.link(other_id, other_job, owner_contact, role="other", is_primary=False, created_at=_TS) is None
        assert repo.list_for_job(other_id, other_job) == []
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-11
def test_list_leads_shows_recruiter_and_linked_jobs_with_latest_stage(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        rita = _manual_contact(conn, user_id, "Rita Recruiter")
        recruiter_lead = _job(conn, user_id, origin="recruiter_contact", next_follow_up="2026-11-20")
        linked_scraped = _job(conn, user_id, company="Scraped Co", next_follow_up="2026-11-15")
        _job(conn, user_id, company="Plain Application")  # on the sheet, nobody attached
        _job(conn, user_id, company="Queue Only", status="pending_review", on_sheet=0,
             origin="recruiter_contact")
        PostgresJobContactRepository(conn).link(
            user_id, recruiter_lead, rita, role="agency_recruiter", is_primary=True, created_at=_TS
        )
        PostgresJobContactRepository(conn).link(
            user_id, linked_scraped, rita, role="agency_recruiter", is_primary=False, created_at=_TS
        )
        stages = PostgresApplicationStageRepository(conn)
        stages.insert(user_id, {"job_id": recruiter_lead, "status": "recruiter_reached_out", "date": "2026-11-01"})
        stages.insert(user_id, {"job_id": recruiter_lead, "status": "recruiter_screen", "date": "2026-11-03"})
        PostgresInteractionRepository(conn).add(
            user_id, _interaction(contact_id=rita, job_id=recruiter_lead, occurred_at="2026-11-03T15:00:00+00:00"), _TS
        )
        conn.commit()

        leads = PostgresLeadRepository(conn).list_leads(user_id)
        assert [lead["id"] for lead in leads] == [linked_scraped, recruiter_lead]  # follow-up order
        lead = leads[1]
        assert lead["latest_stage_status"] == "recruiter_screen"
        assert lead["latest_stage_date"] == "2026-11-03"
        assert lead["primary_contact_name"] == "Rita Recruiter"
        assert lead["primary_contact_company"] == "Agency Co"
        assert lead["last_interaction_at"] == "2026-11-03T15:00:00+00:00"
        assert lead["contact_count"] == 1
        assert leads[0]["primary_contact_name"] is None
        assert leads[0]["latest_stage_status"] is None and leads[0]["job_status"] == "applied"
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-11
def test_get_lead_only_returns_the_users_tracked_jobs(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        tracked = _job(conn, user_id, summary_html="<p>Summary</p>")
        untracked = _job(conn, user_id, on_sheet=0, status="pending_review")
        other_id = _new_user(conn, "lead-other@example.com")
        repo = PostgresLeadRepository(conn)

        lead = repo.get_lead(user_id, tracked)
        assert lead is not None and lead["summary_html"] == "<p>Summary</p>"
        assert lead["origin"] == "scraped"
        assert repo.get_lead(user_id, untracked) is None
        assert repo.get_lead(other_id, tracked) is None
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-11
def test_open_conversations_are_recent_role_less_and_more_than_outreach(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        talking = _manual_contact(conn, user_id, "Talking Tina")
        outreach_only = _manual_contact(conn, user_id, "Messaged Mo")
        moved_on = _manual_contact(conn, user_id, "Linked Lou")
        stale = _manual_contact(conn, user_id, "Stale Sam")
        job_id = _job(conn, user_id)
        repo = PostgresInteractionRepository(conn)
        repo.add(user_id, _interaction(contact_id=talking, occurred_at="2026-11-05T10:00:00+00:00",
                                       summary="Has a few roles"), _TS)
        repo.add(user_id, _interaction(contact_id=outreach_only, channel="linkedin", direction="outbound",
                                       kind="message", occurred_at="2026-11-05T10:00:00+00:00"), _TS)
        # Lou's latest interaction is about a specific job, so it's a lead now.
        repo.add(user_id, _interaction(contact_id=moved_on, occurred_at="2026-11-01T10:00:00+00:00"), _TS)
        repo.add(user_id, _interaction(contact_id=moved_on, job_id=job_id,
                                       occurred_at="2026-11-04T10:00:00+00:00"), _TS)
        repo.add(user_id, _interaction(contact_id=stale, occurred_at="2026-08-01T10:00:00+00:00"), _TS)
        conn.commit()

        open_conversations = PostgresLeadRepository(conn).list_open_conversations(
            user_id, "2026-10-12T00:00:00+00:00"
        )
        assert [c["full_name"] for c in open_conversations] == ["Talking Tina"]
        assert open_conversations[0]["last_summary"] == "Has a few roles"
        assert open_conversations[0]["last_channel"] == "phone"
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-11
def test_create_manual_contact_returns_existing_person_instead_of_duplicating(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        identity = contact_identity_columns("Rita Recruiter", "Agency Co, Inc.", "Recruiter", None)
        rita, created = repo.create_manual(user_id, identity, {"phone": "555-0100", "keep_informed": True}, _TS)
        assert created
        again, created_again = repo.create_manual(user_id, identity, {"phone": "555-9999"}, _TS)
        assert (again, created_again) == (rita, False)

        stored = repo.get_by_id(user_id, rita)
        assert stored is not None
        assert stored["source"] == "manual"
        assert stored["phone"] == "555-0100"
        assert stored["keep_informed"] is True
        assert stored["company_normalized"] == identity["company_normalized"]
        assert stored["dedup_key"] == f"rita recruiter|{identity['company_normalized']}"
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-11
def test_linkedin_reimport_never_removes_hand_added_contacts(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        _manual_contact(conn, user_id, "Phone-only Pat")
        repo = PostgresContactRepository(conn)
        result = repo.sync_import(user_id, [])
        conn.commit()
        assert result.removed == 0
        assert [c["full_name"] for c in repo.get_all(user_id)] == ["Phone-only Pat"]
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-11
def test_update_identity_only_changes_hand_added_contacts(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        manual_id = _manual_contact(conn, user_id, "Rita Recrutier")
        identity = contact_identity_columns("Rita Recruiter", "Agency Co", "Senior Recruiter", None)
        assert repo.update_identity(user_id, manual_id, identity) is True
        stored = repo.get_by_id(user_id, manual_id)
        assert stored is not None
        assert (stored["full_name"], stored["position"]) == ("Rita Recruiter", "Senior Recruiter")

        conn.cursor().execute("UPDATE contacts SET source = 'linkedin_import' WHERE id = %s", (manual_id,))
        assert repo.update_identity(user_id, manual_id, identity) is False
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-11
def test_lead_stage_statuses_are_seeded_with_terminal_flags(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        statuses = {s["key"]: s for s in PostgresApplicationStageStatusRepository(conn).get_all()}
        ordered = [s["key"] for s in PostgresApplicationStageStatusRepository(conn).get_all()]
        assert ordered[:4] == ["recruiter_reached_out", "recruiter_screen", "submitted_to_client", "applied"]
        assert ordered.index("code_eval") < ordered.index("offer") < ordered.index("counter_offer")
        assert ordered.index("counter_offer") < ordered.index("accepted_offer") < ordered.index("rejected")
        terminal = {key for key, s in statuses.items() if s["is_terminal"]}
        assert {"accepted_offer", "withdrew", "declined_offer", "req_closed"} <= terminal
        assert not {"offer", "counter_offer", "recruiter_screen"} & terminal
    finally:
        conn.close()
