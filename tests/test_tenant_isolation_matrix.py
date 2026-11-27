# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Cross-tenant isolation, checked systematically rather than by example.

tests/test_multi_tenant_isolation.py proves a few resources end to end. This file covers the
whole surface:

* every table is tenant-scoped (a schema check, so a new table cannot forget `user_id`);
* every API route that takes a resource id is in the matrix below (so a new route cannot be
  added without deciding how it is tested);
* for each of those routes, a second account acting on the first account's resource is
  refused (403/404) and leaves the first account's rows byte-for-byte unchanged;
* no parameterless GET route shows one account's data to another.

The matrix is deliberately explicit (method, route, which resource each path parameter
names) instead of derived: an isolation test that guesses is one that passes vacuously."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from typing import Any

import psycopg2
import psycopg2.extras
import pytest
from flask import Flask

import config
from backend.db.row_level_security import set_current_user
from backend.security.account_tokens import REGISTRATION_TOKEN_LIFETIME, new_token
from backend.utils.utils import get_repos
from tests.db_roles import runtime_database_url

_PASSWORD = "correct horse battery staple"  # noqa: S105
_MARKER = "TENANT-A-MARKER"
_TS = "2026-09-26T00:00:00+00:00"
_SEQUENCE = itertools.count(1)  # unique keys, since the owner-control phase re-seeds repeatedly

# Tables that are legitimately shared or are the tenant table itself.
# schema_state: one row recording which code last migrated the database (backend/db/schema_version.py).
# registration_invites: emails invited to register, read before any account exists (plan 4.6).
_NOT_TENANT_SCOPED = frozenset(
    {
        "application_stage_statuses", "backup_runs", "registration_invites", "schema_state",
        "system_settings", "user_accounts",
    }
)


# ai: claude-opus-5-5 | 2026-11-11
@dataclass(frozen=True)
class _Id:
    """A payload value standing for one of account A's seeded ids (Tenants.ids[kind]),
    for a body that has to name a resource, resolved when the request is made."""

    kind: str


@dataclass(frozen=True)
class Case:
    method: str
    rule: str
    params: dict[str, str] = field(default_factory=dict)  # path parameter -> resource kind
    control: bool = True  # also prove the route works for the owner (skip where that would call out)
    payload: dict[str, Any] | None = None  # body override, for routes that validate strictly


def _c(method: str, rule: str, payload: dict[str, Any] | None = None, **params: str) -> Case:
    return Case(method, rule, params, payload=payload)


def _c_no_control(method: str, rule: str, payload: dict[str, Any] | None = None, **params: str) -> Case:
    return Case(method, rule, params, control=False, payload=payload)


CASES: list[Case] = [
    _c("DELETE", "/api/application-answers/<int:answer_id>", answer_id="answer"),
    # ai: claude-opus-5-5 | 2026-11-17 -- POST /api/application-answers/merge names its ids
    # in the body (no path id, so not listable here); see
    # test_merge_rejects_another_users_answer_and_changes_nothing.
    _c("PUT", "/api/application-answers/<int:answer_id>", {"question_text": "x?", "answer_text": "y"}, answer_id="answer"),
    _c("DELETE", "/api/application-answers/aliases/<int:alias_id>", alias_id="answer_alias"),
    _c("PUT", "/api/applications/<int:job_id>", {"company": "x", "job_title": "y"}, job_id="job"),
    _c("POST", "/api/applications/<int:job_id>/stages", job_id="job"),
    _c("DELETE", "/api/browser-capture-queue/<int:item_id>", item_id="queue_item"),
    _c("DELETE", "/api/career-rejection-rules/<int:rule_id>", rule_id="career_rule"),
    _c("PUT", "/api/career-rejection-rules/<int:rule_id>", {"rule_text": "x"}, rule_id="career_rule"),
    _c("POST", "/api/career-scan/results/<int:row_id>/reject", {"reason": "not a fit"}, row_id="scan_result"),
    _c("POST", "/api/career-scan/results/<int:row_id>/tracking-status", {"status": "Applied"}, row_id="scan_result"),
    _c("DELETE", "/api/career-sites/<int:source_id>", source_id="career_site"),
    _c("PUT", "/api/career-sites/<int:source_id>", {"name": "x"}, source_id="career_site"),
    _c("POST", "/api/career-sites/<int:source_id>/dismiss-recapture", source_id="career_site"),
    _c_no_control("POST", "/api/career-sites/<int:source_id>/scan", source_id="career_site"),
    _c("DELETE", "/api/company-aliases/<int:alias_id>", alias_id="company_alias"),
    # ai: claude-sonnet-5 | 2026-11-26 -- typed contact emails (2026-11-26 full review:
    # these three routes were added without a matching CASES entry).
    _c("DELETE", "/api/contact-emails/<int:email_id>", email_id="contact_email"),
    _c("PUT", "/api/contact-emails/<int:email_id>", {"address": "x@example.com", "email_type": "home"}, email_id="contact_email"),
    _c("DELETE", "/api/contact-employment-history/<int:entry_id>", entry_id="contact_employment_history"),
    _c("PATCH", "/api/contact-employment-history/<int:entry_id>", {"company": "x"}, entry_id="contact_employment_history"),
    _c("PATCH", "/api/contacts/<int:contact_id>", {"interested": True}, contact_id="contact"),
    _c("GET", "/api/contacts/<int:contact_id>/activity", contact_id="contact"),  # ai: claude-opus-5-5 | 2026-11-11
    _c("POST", "/api/contacts/<int:contact_id>/emails", {"address": "y@example.com", "email_type": "home"}, contact_id="contact"),
    _c("GET", "/api/contacts/<int:contact_id>/employment-history", contact_id="contact"),
    _c("POST", "/api/contacts/<int:contact_id>/employment-history", {"company": "x"}, contact_id="contact"),
    _c("POST", "/api/contacts/<int:contact_id>/outreach", {"sent_at": _TS, "channel": "linkedin"}, contact_id="contact"),
    _c(
        "POST",
        "/api/contacts/<int:contact_id>/outreach-draft/<int:template_id>",
        {"use_ai": False},
        contact_id="contact",
        template_id="outreach_template",
    ),
    _c("POST", "/api/contacts/<int:contact_id>/outreach-draft/stage", {"draft": _MARKER}, contact_id="contact"),
    _c("DELETE", "/api/email-subject-rules/<int:rule_id>", rule_id="subject_rule"),
    _c("PUT", "/api/email-subject-rules/<int:rule_id>", {"pattern": "x"}, rule_id="subject_rule"),
    _c("POST", "/api/emails/<email_id>/hide", email_id="scanned_email"),
    _c_no_control("POST", "/api/emails/<email_id>/mark-read", email_id="scanned_email"),
    _c_no_control("POST", "/api/emails/<email_id>/rescan", email_id="scanned_email"),
    _c("DELETE", "/api/extension-tokens/<int:token_id>", token_id="token"),
    _c("DELETE", "/api/feedback/<int:feedback_id>", feedback_id="feedback"),
    _c("GET", "/api/jd-health-check/run/<task_id>/status", task_id="task"),
    _c("DELETE", "/api/jd-health-check/targets/<int:target_id>", target_id="health_target"),
    _c("PUT", "/api/jd-health-check/targets/<int:target_id>", {"label": "x"}, target_id="health_target"),
    # ai: claude-opus-5-5 | 2026-11-11 -- recruiter lead tracking. The contact_id in the
    # link body is A's own contact (_Id), so B is refused on the job, not on the body.
    _c("PATCH", "/api/interactions/<int:interaction_id>", {"summary": "x"}, interaction_id="interaction"),
    _c("DELETE", "/api/interactions/<int:interaction_id>", interaction_id="interaction"),
    _c("GET", "/api/leads/<int:job_id>", job_id="lead"),
    _c("PATCH", "/api/leads/<int:job_id>", {"next_follow_up": "2026-12-01"}, job_id="lead"),
    _c("POST", "/api/leads/<int:job_id>/contacts", {"contact_id": _Id("contact")}, job_id="lead"),
    # ai: claude-opus-5-5 | 2026-11-11 -- calls the AI provider for the owner
    _c_no_control("POST", "/api/leads/<int:job_id>/call-notes", {"text": "Call notes"}, job_id="lead"),
    _c("DELETE", "/api/leads/<int:job_id>/contacts/<int:contact_id>", job_id="lead", contact_id="contact"),
    # ai: claude-opus-5-5 | 2026-11-11 -- suggested leads (LinkedIn messages import)
    _c("POST", "/api/lead-suggestions/<int:suggestion_id>/accept", {"lead": "none"}, suggestion_id="suggestion"),
    _c("POST", "/api/lead-suggestions/<int:suggestion_id>/dismiss", suggestion_id="suggestion"),
    _c("POST", "/api/lead-suggestions/<int:suggestion_id>/restore", suggestion_id="dismissed_suggestion"),
    _c("GET", "/api/jobs/<int:job_id>", job_id="job"),
    _c("GET", "/api/jobs/<int:job_id>/sightings", job_id="job"),  # ai: claude-opus-5-5 | 2026-11-15
    _c("PATCH", "/api/jobs/<int:job_id>", {"company": "x"}, job_id="job"),
    _c("POST", "/api/jobs/<int:job_id>/apply", job_id="job"),
    _c("POST", "/api/jobs/<int:job_id>/approve", {"subject_pattern": "x"}, job_id="job"),
    _c("POST", "/api/jobs/<int:job_id>/clear-unreachable", job_id="job"),
    _c_no_control("POST", "/api/jobs/<int:job_id>/cover-letter", job_id="job"),
    _c_no_control("POST", "/api/jobs/<int:job_id>/cover-letter/chat", job_id="job"),
    _c("POST", "/api/jobs/<int:job_id>/cover-letter/learn", {"draft": "a draft", "final": "a final"}, job_id="job"),
    _c("POST", "/api/jobs/<int:job_id>/description", {"description": "d" * 200}, job_id="job"),
    _c("POST", "/api/jobs/<int:job_id>/dismiss-duplicate", job_id="job"),
    _c_no_control("POST", "/api/jobs/<int:job_id>/fit-chat", {"messages": [{"role": "user", "content": "hi"}]}, job_id="job"),
    _c("POST", "/api/jobs/<int:job_id>/fit-classification", {"fit_score": 50, "status": "pending_review", "fit_reasoning": "because"}, job_id="job"),
    _c_no_control("POST", "/api/jobs/<int:job_id>/pitch", {"type": "why_fit"}, job_id="job"),
    _c_no_control("POST", "/api/jobs/<int:job_id>/pitch/chat", {"type": "why_fit", "messages": [{"role": "user", "content": "hi"}]}, job_id="job"),
    _c_no_control("POST", "/api/jobs/<int:job_id>/re-evaluate-fit", job_id="job"),
    _c_no_control("POST", "/api/jobs/<int:job_id>/refresh-from-url", job_id="job"),
    _c("POST", "/api/jobs/<int:job_id>/reject", {"subject_pattern": "x"}, job_id="job"),
    _c_no_control("POST", "/api/jobs/<int:job_id>/reprocess", job_id="job"),
    _c_no_control("POST", "/api/jobs/<int:job_id>/resume-tailor", job_id="job"),
    _c_no_control("POST", "/api/jobs/<int:job_id>/resume-tailor/chat", job_id="job"),
    _c("POST", "/api/jobs/<int:job_id>/return-to-queue", job_id="job"),
    _c("POST", "/api/jobs/<int:job_id>/skip", {"reason": "other"}, job_id="job"),
    _c("POST", "/api/jobs/<int:job_id>/snooze", {"days": 3}, job_id="job"),
    _c("POST", "/api/jobs/<int:job_id>/stages", {"status": "applied", "date": "2026-09-26"}, job_id="job"),
    _c("DELETE", "/api/jobs/<int:job_id>/stages/<int:stage_id>", job_id="job", stage_id="stage"),
    _c("POST", "/api/jobs/status-history/<int:history_id>/undo", history_id="history"),
    _c("DELETE", "/api/outreach-templates/<int:template_id>", template_id="outreach_template"),
    _c("PUT", "/api/outreach-templates/<int:template_id>", {"title": "x", "body": "y"}, template_id="outreach_template"),
    _c("DELETE", "/api/penalties/<int:rule_id>", rule_id="penalty"),
    _c("PUT", "/api/penalties/<int:rule_id>", {"rule_text": "x"}, rule_id="penalty"),
    _c("DELETE", "/api/phrase-book/<int:phrase_id>", phrase_id="phrase"),
    _c("PUT", "/api/phrase-book/<int:phrase_id>", phrase_id="phrase"),
    _c("DELETE", "/api/sources/<int:source_id>", source_id="source"),
    _c("PUT", "/api/sources/<int:source_id>", {"name": "x"}, source_id="source"),
    # ai: claude-opus-5-5 | 2026-11-09 -- admin account tooling (plan 4.5): act on another
    # account by design, so only an admin may; neither tenant is one, so both are refused
    # (no owner control). The admin side is tested in tests/test_admin_accounts.py.
    _c_no_control("POST", "/api/settings/system/accounts/<int:account_id>/disable", account_id="account"),
    _c_no_control("POST", "/api/settings/system/accounts/<int:account_id>/enable", account_id="account"),
    _c_no_control("POST", "/api/settings/system/accounts/<int:account_id>/resend-verification", account_id="account"),
    _c_no_control("GET", "/api/settings/system/accounts/<int:account_id>/task-failures", account_id="account"),
]

# Routes that answer B with something other than 403/404 and are still safe, with the reason.
# Every entry is checked by the same test: A's rows unchanged, and B creating nothing.
_SCOPED_NO_OP = "the DELETE/UPDATE is scoped by user_id, so it matches nothing and the route reports success anyway"
_UNUSED_JOB_ID = "job_id is not used: the route works on the caller's own candidate profile"
_OWN_GOOGLE = "authenticates to the caller's own Google account first; the id is a message id in that mailbox"
# ai: claude-sonnet-5 | 2026-10-15
_SCOPED_EMPTY_LIST = (
    "the list is scoped by user_id AND contact_id, so a foreign contact_id"
    " matches no rows and the route returns an empty list rather than erroring"
)
_NOT_REFUSED: dict[tuple[str, str], str] = {
    ("GET", "/api/contacts/<int:contact_id>/employment-history"): _SCOPED_EMPTY_LIST,
    ("DELETE", "/api/application-answers/<int:answer_id>"): _SCOPED_NO_OP,
    ("DELETE", "/api/browser-capture-queue/<int:item_id>"): _SCOPED_NO_OP,
    ("DELETE", "/api/company-aliases/<int:alias_id>"): _SCOPED_NO_OP,
    ("DELETE", "/api/feedback/<int:feedback_id>"): _SCOPED_NO_OP,
    ("PUT", "/api/applications/<int:job_id>"): _SCOPED_NO_OP,
    ("POST", "/api/jobs/<int:job_id>/cover-letter/learn"): _UNUSED_JOB_ID,
    ("POST", "/api/emails/<email_id>/hide"): _SCOPED_NO_OP,
    ("POST", "/api/emails/<email_id>/mark-read"): _OWN_GOOGLE,
    ("POST", "/api/emails/<email_id>/rescan"): _OWN_GOOGLE,
}

# A body broad enough to satisfy every route's validation, so a refusal is the ownership
# check and not a 400 for a missing field.
_PAYLOAD: dict[str, Any] = {
    "status": "applied",
    "date": "2026-09-26",
    "note": "x",
    "message": "hello",
    "messages": [{"role": "user", "content": "hello"}],
    "description": "A job description long enough to be accepted.",
    "text": "x",
    "days": 3,
    "classification": "good",
    "rule_text": "x",
    "name": "x",
    "url": "https://example.com/x",
    "pattern": "x",
    "label": "x",
    "tag": "x",
    "phrase_text": "x",
    "notes": "x",
    "sent_at": _TS,
    "enabled": 1,
    "keyword": "x",
    "penalty": 5,
    "points": 5,
    "email": "x@example.com",
}


# ─── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _test_db(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))


@pytest.fixture(autouse=True)
def _no_outbound_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("routes.session_auth.send_verification_email", lambda *args, **kwargs: None)


def _register(app: Flask, email: str) -> int:
    client = app.test_client()
    assert client.post("/api/account", json={"email": email}).status_code == 202
    # Only a digest of the emailed token is stored, so issue a known one instead of
    # reading it back. ai: claude-opus-5-5 | 2026-11-18
    token, expires_at = new_token(REGISTRATION_TOKEN_LIFETIME)
    with get_repos() as repos:
        pending_id = repos["user_accounts"].get_by_email(email)["id"]
        repos["user_accounts"].renew_registration_token(pending_id, token, expires_at)
        repos["conn"].commit()
    done = client.post("/api/account/complete-registration", json={"token": token, "password": _PASSWORD})
    assert done.status_code == 200, done.get_json()
    with get_repos() as repos:
        return int(repos["user_accounts"].get_by_email(email)["id"])


def _login(app: Flask, email: str) -> Any:
    client = app.test_client()
    resp = client.post("/api/login", json={"email": email, "password": _PASSWORD})
    assert resp.status_code == 200, resp.get_json()
    return client


class Tenants:
    """Two registered accounts, each with its own logged-in client. Account A owns one row of
    every kind of resource, all carrying `_MARKER` wherever a text field allows it."""

    def __init__(self, dsn: str, app: Flask) -> None:
        self.dsn = dsn
        self.app = app
        self.a_id = _register(app, "alice@example.com")
        self.b_id = _register(app, "bob@example.com")
        self.a = _login(app, "alice@example.com")
        self.b = _login(app, "bob@example.com")
        self.ids: dict[str, Any] = {}
        for client, name in ((self.a, f"{_MARKER} profile"), (self.b, "Bob's profile")):
            saved = client.put("/api/candidate-profile", json={"content": f"{name}: a candidate profile."})
            assert saved.status_code == 200, saved.get_json()
        put = self.a.put("/api/application-profile", json={"legal_name": f"{_MARKER} Name"})
        assert put.status_code == 200, put.get_json()
        self.seed_rows()

    def _insert(self, cur: Any, table: str, **columns: Any) -> int:
        names = ", ".join(["user_id", *columns])
        marks = ", ".join(["%s"] * (len(columns) + 1))
        cur.execute(f"INSERT INTO {table} ({names}) VALUES ({marks}) RETURNING id", (self.a_id, *columns.values()))  # noqa: S608 - fixed table names from this file
        return int(cur.fetchone()[0])

    def seed_rows(self) -> None:
        """Give A a fresh row of every kind. The owner-control phase calls this before each
        request so an earlier control (a delete, a rejection) cannot fake a refusal in a later one."""
        with get_repos(self.a_id) as repos:
            job_id = repos["jobs"].insert(
                self.a_id,
                {
                    "dedup_key": f"{_MARKER}-job-{next(_SEQUENCE)}",
                    "company": f"{_MARKER} Corp",
                    "job_title": f"{_MARKER} Engineer",
                    "url": f"https://example.com/{_MARKER}-{next(_SEQUENCE)}",
                    "source": "email",
                    "status": "pending_review",
                    "date_found": "2026-09-26",
                },
            )
            # ai: claude-opus-5-5 | 2026-11-11 -- a tracked recruiter lead: the /leads
            # routes only open jobs on the sheet, which the queue job above is not.
            lead_id = repos["jobs"].insert(
                self.a_id,
                {
                    "company": f"{_MARKER} Client",
                    "job_title": f"{_MARKER} Lead",
                    "status": "applied",
                    "on_sheet": 1,
                    "origin": "recruiter_contact",
                    "date_found": "2026-09-26",
                },
            )
            repos["conn"].commit()
        conn = psycopg2.connect(self.dsn)
        conn.autocommit = True
        try:
            cur = conn.cursor()
            set_current_user(cur, self.a_id)
            email_id = f"{_MARKER}-gmail-{next(_SEQUENCE)}"
            cur.execute("SELECT key FROM application_stage_statuses ORDER BY key LIMIT 1")
            stage_status = cur.fetchone()[0]
            ins = self._insert
            contact_id = ins(cur, "contacts", full_name=f"{_MARKER} Person", imported_at=_TS, dedup_key=f"{_MARKER}-c-{next(_SEQUENCE)}")
            self.ids = {
                "account": self.a_id,  # ai: claude-opus-5-5 | 2026-11-09
                "job": job_id,
                "lead": lead_id,  # ai: claude-opus-5-5 | 2026-11-11
                "answer": ins(cur, "application_answers", question_text=f"{_MARKER}?", question_normalized=f"{_MARKER.lower()}-{next(_SEQUENCE)}", answer_text=_MARKER),
                "stage": ins(cur, "application_stages", job_id=job_id, status=stage_status, date="2026-09-26"),
                "history": ins(cur, "job_status_history", job_id=job_id, from_status="pending_review", to_status="applied", changed_at=_TS, trigger="test"),
                "queue_item": ins(cur, "browser_capture_queue", url=f"https://example.com/{_MARKER}-{next(_SEQUENCE)}", created_at=_TS, updated_at=_TS),
                "career_rule": ins(cur, "career_rejection_rules", rule_text=_MARKER, created_at=_TS),
                "scan_result": ins(cur, "career_scan_results", site_name=_MARKER, site_url="https://example.com", job_title=_MARKER, job_url=f"https://example.com/j-{next(_SEQUENCE)}", scanned_at=_TS),
                "career_site": ins(cur, "career_site_sources", name=_MARKER, url=f"https://example.com/careers-{next(_SEQUENCE)}"),
                "company_alias": ins(
                    cur,
                    "company_aliases",
                    alias_company=f"{_MARKER} Alias",
                    alias_normalized=f"{_MARKER.lower()}alias{next(_SEQUENCE)}",
                    canonical_company=f"{_MARKER} Canonical",
                    canonical_normalized=f"{_MARKER.lower()}canonical{next(_SEQUENCE)}",
                    created_at=_TS,
                ),
                "contact": contact_id,
                # ai: claude-sonnet-5 | 2026-11-26
                "contact_email": ins(
                    cur, "contact_email_addresses", contact_id=contact_id,
                    address=f"{_MARKER.lower()}@example.com", email_type="home", created_at=_TS,
                ),
                # ai: claude-opus-5-5 | 2026-11-11 -- was contact_outreach
                "interaction": ins(
                    cur, "interactions", contact_id=contact_id, occurred_at=_TS, channel="phone",
                    direction="inbound", kind="call", summary=_MARKER, created_at=_TS,
                ),
                "contact_employment_history": ins(
                    cur, "contact_employment_history", contact_id=contact_id, company=_MARKER, captured_at=_TS
                ),
                "outreach_template": ins(
                    cur, "outreach_templates", title=f"{_MARKER}-{next(_SEQUENCE)}", body=_MARKER, created_at=_TS, updated_at=_TS
                ),
                "subject_rule": ins(cur, "ack_patterns", pattern=_MARKER),
                "scanned_email": None,
                "token": ins(cur, "extension_tokens", token_hash=f"{_MARKER}-hash-{next(_SEQUENCE)}", token_prefix="jha_A", label=_MARKER, created_at=_TS),
                "feedback": ins(cur, "relevance_feedback"),
                "health_target": ins(cur, "jd_health_check_targets", platform="greenhouse", label=_MARKER, url=f"https://example.com/h-{next(_SEQUENCE)}"),
                "penalty": ins(cur, "penalty_rules"),
                "phrase": ins(cur, "cover_letter_phrases", tag="t", label=f"{_MARKER}-{next(_SEQUENCE)}", phrase_text=_MARKER),
                "source": ins(cur, "email_sources"),
            }
            cur.execute(
                "INSERT INTO scanned_emails (user_id, email_id) VALUES (%s, %s)", (self.a_id, email_id)
            )
            ins(cur, "job_contacts", job_id=lead_id, contact_id=contact_id, created_at=_TS)  # ai: claude-opus-5-5 | 2026-11-11
            ins(cur, "job_sightings", job_id=job_id, seen_at=_TS, source=_MARKER, channel="email")  # ai: claude-opus-5-5 | 2026-11-15
            self.ids["answer_alias"] = ins(  # ai: claude-opus-5-5 | 2026-11-17
                cur, "application_answer_aliases", answer_id=self.ids["answer"],
                question_text=f"{_MARKER} alias?", question_normalized=f"{_MARKER.lower()}-alias-{next(_SEQUENCE)}",
            )
            # ai: claude-opus-5-5 | 2026-11-11 -- one suggestion per status the routes start from
            for kind, status in (("suggestion", "suggested"), ("dismissed_suggestion", "dismissed")):
                self.ids[kind] = ins(
                    cur, "lead_suggestions", source="linkedin_messages",
                    external_ref=f"{_MARKER}-conv-{next(_SEQUENCE)}", status=status,
                    counterpart_name=f"{_MARKER} Recruiter", first_message_at=_TS, last_message_at=_TS,
                    message_count=0, messages="[]", imported_at=_TS,
                )
            self.ids["scanned_email"] = email_id
        finally:
            conn.close()
        # A Celery task id belongs to whichever account submitted it; the ownership
        # record is written where the submitting route writes it.
        from backend.security.task_ownership import get_task_ownership

        self.ids["task"] = f"{_MARKER.lower()}-task-{next(_SEQUENCE)}"
        get_task_ownership().record(self.ids["task"], self.a_id)

    def path(self, case: Case) -> str:
        path = case.rule
        for name, kind in case.params.items():
            for converter in ("<int:", "<"):
                token = f"{converter}{name}>"
                if token in path:
                    path = path.replace(token, str(self.ids[kind]))
                    break
        return path

    def snapshot(self, user_id: int) -> dict[str, list[str]]:
        """Every row the account owns, table by table, as canonical JSON."""
        conn = psycopg2.connect(self.dsn)
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'user_id' ORDER BY table_name"
            )
            result: dict[str, list[str]] = {}
            for (table,) in cur.fetchall():
                cur.execute(f"SELECT to_jsonb(t) FROM {table} t WHERE user_id = %s", (user_id,))  # noqa: S608 - names come from information_schema
                result[table] = sorted(json.dumps(row[0], sort_keys=True, default=str) for row in cur.fetchall())
            return result
        finally:
            conn.close()


@pytest.fixture
def tenants(pg_dsn: str, flask_app: Flask, open_registration: None) -> Tenants:
    return Tenants(pg_dsn, flask_app)


def _call(client: Any, case: Case, url: str, ids: dict[str, Any]) -> Any:
    # ai: claude-opus-5-5 | 2026-11-11 -- _Id payload values resolve to A's seeded ids.
    payload = {
        key: ids[value.kind] if isinstance(value, _Id) else value
        for key, value in (case.payload or _PAYLOAD).items()
    }
    kwargs: dict[str, Any] = {} if case.method == "GET" else {"json": payload}
    return client.open(url, method=case.method, **kwargs)


# ─── the schema ─────────────────────────────────────────────────────────────────


def test_every_table_is_tenant_scoped(pg_dsn: str) -> None:
    """A new table must carry a NOT NULL `user_id` that references user_accounts and cascades on
    delete (so deleting an account removes its data), or be added to the shared list with a reason."""
    conn = psycopg2.connect(pg_dsn)
    try:
        cur = conn.cursor()
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")
        tables = {row[0] for row in cur.fetchall()} - _NOT_TENANT_SCOPED
        cur.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema='public' AND column_name='user_id' AND is_nullable='NO'"
        )
        with_user_id = {row[0] for row in cur.fetchall()}
        cur.execute(
            "SELECT conrelid::regclass::text FROM pg_constraint WHERE contype='f' "
            "AND pg_get_constraintdef(oid) LIKE '%%(user_id) REFERENCES user_accounts(id) ON DELETE CASCADE%%'"
        )
        cascading = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()

    assert tables - with_user_id == set(), "tables without a NOT NULL user_id"
    assert tables - cascading == set(), "tables whose user_id does not cascade from user_accounts"


# ─── route coverage ─────────────────────────────────────────────────────────────


def test_every_id_route_is_in_the_isolation_matrix(flask_app: Flask) -> None:
    covered = {(case.method, case.rule) for case in CASES}
    actual = {
        (method, rule.rule)
        for rule in flask_app.url_map.iter_rules()
        if rule.rule.startswith("/api/") and "<" in rule.rule
        for method in rule.methods - {"HEAD", "OPTIONS"}
    }

    assert actual - covered == set(), "routes with a resource id that are not in CASES"
    assert covered - actual == set(), "CASES entries for routes that no longer exist"


# ─── acting on someone else's resource ──────────────────────────────────────────


def test_no_route_lets_one_account_act_on_anothers_resource(tenants: Tenants) -> None:
    a_before, b_before = tenants.snapshot(tenants.a_id), tenants.snapshot(tenants.b_id)
    unrefused: list[str] = []

    for case in CASES:
        response = _call(tenants.b, case, tenants.path(case), tenants.ids)
        if response.status_code not in (403, 404) and (case.method, case.rule) not in _NOT_REFUSED:
            unrefused.append(f"{case.method} {case.rule} -> {response.status_code} {response.get_data(as_text=True)[:100]!r}")

    def changed(before: dict[str, list[str]], user_id: int) -> list[str]:
        after = tenants.snapshot(user_id)
        return sorted(table for table in before if before[table] != after[table])

    # A's rows must be untouched, and B must not have created anything either: a row B owns
    # that points at A's job or contact is a cross-tenant write even if A's own rows are intact.
    assert (unrefused, changed(a_before, tenants.a_id), changed(b_before, tenants.b_id)) == ([], [], []), (
        "not refused:\n" + "\n".join(unrefused)
    )


def test_the_owner_is_not_refused_on_the_same_routes(tenants: Tenants) -> None:
    """Guards against the test above passing vacuously (a path that is wrong for everyone 404s
    for everyone). Routes that would call an AI provider or the network are exempt."""
    refused = []
    for case in CASES:
        if not case.control:
            continue
        tenants.seed_rows()
        response = _call(tenants.a, case, tenants.path(case), tenants.ids)
        if response.status_code in (403, 404):
            refused.append(f"{case.method} {case.rule} -> {response.status_code} {response.get_data(as_text=True)[:120]}")

    assert refused == [], "the owner was refused (the matrix is wrong, not the route):\n" + "\n".join(refused)


# ─── listings ───────────────────────────────────────────────────────────────────

_LIST_ROUTES_NOT_EXERCISED = frozenset({
    "/api/auth/status",  # talks to Google
    "/api/system/health",
    # ai: claude-opus-5-5 | 2026-10-28
    # An endless event stream carrying no data, so reading its body never
    # returns; its per-user channel is covered in test_user_events.py.
    "/api/events",
})


def _parameterless_get_routes(app: Flask) -> list[str]:
    return sorted(
        rule.rule
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/api/") and "<" not in rule.rule and "GET" in rule.methods
        and rule.rule not in _LIST_ROUTES_NOT_EXERCISED
    )


def test_listings_never_show_one_account_the_others_data(tenants: Tenants) -> None:
    leaks: list[str] = []
    owner_sees_marker = 0
    for route in _parameterless_get_routes(tenants.app):
        as_a = tenants.a.get(route)
        as_b = tenants.b.get(route)
        if _MARKER in as_a.get_data(as_text=True):
            owner_sees_marker += 1
        if _MARKER in as_b.get_data(as_text=True):
            leaks.append(f"GET {route} showed account A's data to account B")

    assert leaks == [], "\n".join(leaks)
    # If almost nothing in A's own listings carries the marker, this test proves nothing.
    assert owner_sees_marker >= 8, f"only {owner_sees_marker} listings showed A's own seeded data"


def test_deleting_an_account_removes_all_of_its_rows_and_only_its_rows(tenants: Tenants) -> None:
    b_before = tenants.snapshot(tenants.b_id)
    conn = psycopg2.connect(tenants.dsn)
    conn.autocommit = True
    try:
        conn.cursor().execute("DELETE FROM user_accounts WHERE id = %s", (tenants.a_id,))
    finally:
        conn.close()

    remaining = {table: rows for table, rows in tenants.snapshot(tenants.a_id).items() if rows}

    assert remaining == {}
    assert tenants.snapshot(tenants.b_id) == b_before

