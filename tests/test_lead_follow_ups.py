# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-18
"""Lead follow-up reminders (recruiter lead tracking, phase 3): which leads need
attention on the account's own date, the Leads tab badge's count, and the daily
follow-up email -- end to end against the real schema."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from flask import Flask

import config
from backend.celery_app import LEAD_FOLLOW_UP_DIGEST_TICK, celery_app
from backend.db import (
    PostgresApplicationStageRepository,
    PostgresUserAccountRepository,
    PostgresUserSettingsRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user
from backend.leads import follow_up_digest
from backend.leads.follow_up_digest import (
    format_follow_up_digest,
    lead_follow_up_digest_tick,
    send_lead_follow_up_digest,
)
from backend.leads.lead_service import LeadService, attention_rules, local_now
from backend.utils.utils import get_repos
from routes.leads import leads_bp
from tests.db_roles import runtime_database_url
from tests.login_session import sign_in

_RECRUITER = {"full_name": "Rita Recruiter", "company": "Agency Co", "position": "Recruiter"}
# 15:00 UTC on 2026-11-18: 09:00 in Chicago, already 2026-11-19 in Kiritimati (UTC+14).
_NOW = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)


@pytest.fixture
def client_and_user(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, int]:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        account = PostgresUserAccountRepository(conn).create_pending(
            "followups@example.com", "seed-followups", "2099-01-01"
        )
        conn.commit()
    finally:
        conn.close()
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(leads_bp, url_prefix="/api")
    client = app.test_client()
    with client.session_transaction() as sess:
        sign_in(sess, account["id"])
    return client, int(account["id"])


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Every email the follow-up digest hands to SMTP."""
    outbox: list[dict[str, str]] = []
    monkeypatch.setattr(follow_up_digest, "send_email", lambda **kwargs: outbox.append(kwargs))
    return outbox


def _set(pg_dsn: str, user_id: int, key: str, value: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        set_current_user(conn.cursor(), user_id)
        PostgresUserSettingsRepository(conn).set(user_id, key, value)
        conn.commit()
    finally:
        conn.close()


def _close(pg_dsn: str, user_id: int, job_id: int) -> None:
    conn = get_connection(pg_dsn)
    try:
        set_current_user(conn.cursor(), user_id)
        PostgresApplicationStageRepository(conn).insert(
            user_id, {"job_id": job_id, "status": "withdrew", "date": "2026-11-17"}
        )
        conn.commit()
    finally:
        conn.close()


def _lead(client: Any, company: str, *, follow_up: str | None, last_activity: str = "2026-11-17") -> int:
    body: dict[str, Any] = {
        "company": company,
        "job_title": "Staff Engineer",
        "new_contact": _RECRUITER,
        "stage_date": last_activity,
    }
    if follow_up:
        body["next_follow_up"] = follow_up
    response = client.post("/api/leads", json=body)
    assert response.status_code == 201, response.get_json()
    return int(response.get_json()["id"])


def _attention(user_id: int, now: datetime) -> list[str]:
    with get_repos(user_id) as repos:
        rules = attention_rules(user_id, repos, now)
        return [lead["company"] for lead in LeadService(repos).leads_needing_attention(user_id, rules)]


# --- the account's own date ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("zone", "expected"),
    [("America/Chicago", date(2026, 11, 18)), ("Pacific/Kiritimati", date(2026, 11, 19)),
     ("", date(2026, 11, 18)), ("Not/AZone", date(2026, 11, 18))],
)
def test_local_now_uses_the_account_zone_and_falls_back_to_utc(zone: str, expected: date) -> None:
    assert local_now(_NOW, zone).date() == expected


def test_a_follow_up_falls_due_at_the_accounts_own_midnight(
    client_and_user: tuple[Any, int], pg_dsn: str
) -> None:
    client, user_id = client_and_user
    _lead(client, "Tomorrow Co", follow_up="2026-11-19")

    _set(pg_dsn, user_id, "candidate_timezone", "America/Chicago")
    assert _attention(user_id, _NOW) == []
    _set(pg_dsn, user_id, "candidate_timezone", "Pacific/Kiritimati")
    assert _attention(user_id, _NOW) == ["Tomorrow Co"]


# --- the board and the badge --------------------------------------------------------------


def test_board_and_badge_count_the_same_leads(client_and_user: tuple[Any, int], pg_dsn: str) -> None:
    client, user_id = client_and_user
    today = datetime.now(UTC).date().isoformat()
    due = _lead(client, "Due Co", follow_up="2020-01-01", last_activity=today)
    later = _lead(client, "Later Co", follow_up="2099-01-01", last_activity=today)
    quiet = _lead(client, "Quiet Co", follow_up=None, last_activity="2020-01-01")
    # Withdrawn (2026-11-17) after its last activity, so the withdrawal is its latest stage.
    closed = _lead(client, "Closed Co", follow_up="2020-01-01", last_activity="2026-11-01")
    _close(pg_dsn, user_id, closed)

    rows = {row["id"]: row for row in client.get("/api/leads").get_json()["leads"]}
    assert (rows[due]["follow_up_due"], rows[due]["needs_attention"]) == (True, True)
    assert (rows[later]["follow_up_due"], rows[later]["needs_attention"]) == (False, False)
    assert (rows[quiet]["is_stale"], rows[quiet]["needs_attention"]) == (True, True)
    assert (rows[closed]["follow_up_due"], rows[closed]["needs_attention"]) == (False, False)

    assert client.get("/api/leads/attention").get_json() == {"count": 2}


def test_badge_count_is_zero_with_no_leads(client_and_user: tuple[Any, int]) -> None:
    client, _ = client_and_user
    assert client.get("/api/leads/attention").get_json() == {"count": 0}


# --- the daily email ----------------------------------------------------------------------


def test_the_email_is_off_until_turned_on(
    client_and_user: tuple[Any, int], sent: list[dict[str, str]]
) -> None:
    client, user_id = client_and_user
    _lead(client, "Due Co", follow_up="2026-11-18")

    assert send_lead_follow_up_digest(user_id, _NOW) is False
    assert sent == []


def test_the_email_waits_for_its_local_time_then_goes_once_a_day(
    client_and_user: tuple[Any, int], pg_dsn: str, sent: list[dict[str, str]]
) -> None:
    client, user_id = client_and_user
    _lead(client, "Due Co", follow_up="2026-11-18")
    _set(pg_dsn, user_id, "lead_follow_up_digest_enabled", "true")
    _set(pg_dsn, user_id, "candidate_timezone", "America/Chicago")  # 09:00 local at _NOW
    _set(pg_dsn, user_id, "lead_follow_up_digest_time", "09:30")

    assert send_lead_follow_up_digest(user_id, _NOW) is False
    assert sent == []

    later = datetime(2026, 11, 18, 15, 45, tzinfo=UTC)
    assert send_lead_follow_up_digest(user_id, later) is True
    assert send_lead_follow_up_digest(user_id, later) is False  # already claimed today
    [email] = sent
    assert email["to"] == "followups@example.com"
    assert email["subject"] == "1 lead to follow up — 2026-11-18"
    assert "Due Co — Staff Engineer (via Rita Recruiter, Agency Co)" in email["body_text"]
    assert "Follow-up today" in email["body_text"]
    assert "/?tab=leads" in email["body_text"]

    next_day = datetime(2026, 11, 19, 15, 45, tzinfo=UTC)
    assert send_lead_follow_up_digest(user_id, next_day) is True
    assert "overdue since 2026-11-18" in sent[-1]["body_text"]


def test_a_day_with_nothing_due_sends_nothing_and_stays_quiet(
    client_and_user: tuple[Any, int], pg_dsn: str, sent: list[dict[str, str]]
) -> None:
    client, user_id = client_and_user
    _set(pg_dsn, user_id, "lead_follow_up_digest_enabled", "true")

    assert send_lead_follow_up_digest(user_id, _NOW) is False
    # A follow-up added later that day doesn't trigger a second, mid-day email.
    _lead(client, "Afternoon Co", follow_up="2026-11-18")
    assert send_lead_follow_up_digest(user_id, _NOW) is False
    assert sent == []


def test_the_email_goes_to_the_configured_address_on_the_desktop(
    client_and_user: tuple[Any, int], pg_dsn: str, sent: list[dict[str, str]]
) -> None:
    client, user_id = client_and_user
    _lead(client, "Due Co", follow_up="2026-11-18")
    _set(pg_dsn, user_id, "lead_follow_up_digest_enabled", "true")
    _set(pg_dsn, user_id, "lead_follow_up_digest_notify_email", "me@example.org")

    assert send_lead_follow_up_digest(user_id, _NOW) is True
    assert sent[0]["to"] == "me@example.org"


def test_no_email_to_a_localhost_placeholder(
    client_and_user: tuple[Any, int], pg_dsn: str, sent: list[dict[str, str]]
) -> None:
    client, user_id = client_and_user
    _lead(client, "Due Co", follow_up="2026-11-18")
    _set(pg_dsn, user_id, "lead_follow_up_digest_enabled", "true")
    _set(pg_dsn, user_id, "lead_follow_up_digest_notify_email", "admin@localhost")

    assert send_lead_follow_up_digest(user_id, _NOW) is False
    assert sent == []


def test_digest_text_separates_due_from_gone_quiet() -> None:
    leads = [
        {"company": "Due Co", "job_title": "SRE", "primary_contact_name": "Rita", "primary_contact_company": "Due Co",
         "next_follow_up": "2026-11-15", "follow_up_due": True, "is_stale": True, "idle_days": 12, "stage": "recruiter_screen"},
        {"company": "Quiet Co", "job_title": "", "primary_contact_name": None, "primary_contact_company": None,
         "next_follow_up": None, "follow_up_due": False, "is_stale": True, "idle_days": 1, "stage": "unknown"},
    ]
    subject, body = format_follow_up_digest(
        leads, date(2026, 11, 18), 10, {"recruiter_screen": "Recruiter Screen"}, "https://jha.example"
    )
    assert subject == "2 leads to follow up — 2026-11-18"
    assert body.startswith("2 open leads need your attention today.\n")
    assert "- Due Co — SRE (via Rita)\n  Follow-up overdue since 2026-11-15 · Recruiter Screen · nothing logged for 12 days" in body
    assert "Gone quiet (nothing logged for over 10 days)" in body
    assert "- Quiet Co\n  Last activity 1 day ago\n" in body
    assert "Open the Leads tab: https://jha.example/?tab=leads" in body


# --- the beat schedule --------------------------------------------------------------------


def test_beat_schedules_the_registered_task() -> None:
    assert lead_follow_up_digest_tick.name == LEAD_FOLLOW_UP_DIGEST_TICK
    assert LEAD_FOLLOW_UP_DIGEST_TICK in celery_app.tasks
    tasks = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert LEAD_FOLLOW_UP_DIGEST_TICK in tasks
