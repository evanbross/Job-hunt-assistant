# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# LICENSE: Personal Use Only
# This source code is made available for personal, non-commercial use only.
# You may read, study, and run this code for personal purposes.
# You may NOT:
#   - Use this code, in whole or in part, in any commercial product or service
#   - Sell, sublicense, or otherwise monetize this code or derivatives of it
#   - Redistribute this code without explicit written permission from ZombieTech LLC
#
# No warranty is provided, express or implied. Use at your own risk.
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

import app as app_module
import config
from backend.db.row_level_security import set_current_user
from backend.utils.utils import get_repos

_PASSWORD = "correct horse battery staple"  # noqa: S105
_MARKER = "TENANT-A-MARKER"
_TS = "2026-09-26T00:00:00+00:00"
_SEQUENCE = itertools.count(1)  # unique keys, since the owner-control phase re-seeds repeatedly

# Tables that are legitimately shared or are the tenant table itself.
_NOT_TENANT_SCOPED = frozenset({"application_stage_statuses", "system_settings", "user_accounts"})


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
    _c("DELETE", "/api/contact-emails/<int:email_id>", email_id="contact_email"),
    _c("PATCH", "/api/contact-emails/<int:email_id>", {"response_received_at": _TS}, email_id="contact_email"),
    _c("PATCH", "/api/contacts/<int:contact_id>", {"interested": True}, contact_id="contact"),
    _c("POST", "/api/contacts/<int:contact_id>/emails", {"sent_at": _TS}, contact_id="contact"),
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
    _c("GET", "/api/jobs/<int:job_id>", job_id="job"),
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
    _c("DELETE", "/api/penalties/<int:rule_id>", rule_id="penalty"),
    _c("PUT", "/api/penalties/<int:rule_id>", {"rule_text": "x"}, rule_id="penalty"),
    _c("DELETE", "/api/phrase-book/<int:phrase_id>", phrase_id="phrase"),
    _c("PUT", "/api/phrase-book/<int:phrase_id>", phrase_id="phrase"),
    _c("DELETE", "/api/sources/<int:source_id>", source_id="source"),
    _c("PUT", "/api/sources/<int:source_id>", {"name": "x"}, source_id="source"),
]

# Routes that answer B with something other than 403/404 and are still safe, with the reason.
# Every entry is checked by the same test: A's rows unchanged, and B creating nothing.
_SCOPED_NO_OP = "the DELETE/UPDATE is scoped by user_id, so it matches nothing and the route reports success anyway"
_UNUSED_JOB_ID = "job_id is not used: the route works on the caller's own candidate profile"
_OWN_GOOGLE = "authenticates to the caller's own Google account first; the id is a message id in that mailbox"
_NOT_REFUSED: dict[tuple[str, str], str] = {
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
    monkeypatch.setattr(config, "DATABASE_URL", pg_dsn)


@pytest.fixture(autouse=True)
def _no_outbound_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("routes.session_auth.send_verification_email", lambda *args, **kwargs: None)


def _register(email: str) -> int:
    client = app_module.app.test_client()
    assert client.post("/api/account", json={"email": email}).status_code == 202
    with get_repos() as repos:
        token = repos["user_accounts"].get_by_email(email)["verification_token"]
    done = client.post("/api/account/complete-registration", json={"token": token, "password": _PASSWORD})
    assert done.status_code == 200, done.get_json()
    with get_repos() as repos:
        return int(repos["user_accounts"].get_by_email(email)["id"])


def _login(email: str) -> Any:
    client = app_module.app.test_client()
    resp = client.post("/api/login", json={"email": email, "password": _PASSWORD})
    assert resp.status_code == 200, resp.get_json()
    return client


class Tenants:
    """Two registered accounts, each with its own logged-in client. Account A owns one row of
    every kind of resource, all carrying `_MARKER` wherever a text field allows it."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.a_id = _register("alice@example.com")
        self.b_id = _register("bob@example.com")
        self.a = _login("alice@example.com")
        self.b = _login("bob@example.com")
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
                "job": job_id,
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
                "contact_email": ins(cur, "contact_emails", contact_id=contact_id, sent_at=_TS),
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
def tenants(pg_dsn: str) -> Tenants:
    app_module.app.config["TESTING"] = True
    return Tenants(pg_dsn)


def _call(client: Any, case: Case, url: str) -> Any:
    kwargs: dict[str, Any] = {} if case.method == "GET" else {"json": case.payload or _PAYLOAD}
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


def test_every_id_route_is_in_the_isolation_matrix() -> None:
    covered = {(case.method, case.rule) for case in CASES}
    actual = {
        (method, rule.rule)
        for rule in app_module.app.url_map.iter_rules()
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
        response = _call(tenants.b, case, tenants.path(case))
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
        response = _call(tenants.a, case, tenants.path(case))
        if response.status_code in (403, 404):
            refused.append(f"{case.method} {case.rule} -> {response.status_code} {response.get_data(as_text=True)[:120]}")

    assert refused == [], "the owner was refused (the matrix is wrong, not the route):\n" + "\n".join(refused)


# ─── listings ───────────────────────────────────────────────────────────────────

_LIST_ROUTES_NOT_EXERCISED = frozenset({
    "/api/auth/status",  # talks to Google
    "/api/system/health",
})


def _parameterless_get_routes() -> list[str]:
    return sorted(
        rule.rule
        for rule in app_module.app.url_map.iter_rules()
        if rule.rule.startswith("/api/") and "<" not in rule.rule and "GET" in rule.methods
        and rule.rule not in _LIST_ROUTES_NOT_EXERCISED
    )


def test_listings_never_show_one_account_the_others_data(tenants: Tenants) -> None:
    leaks: list[str] = []
    owner_sees_marker = 0
    for route in _parameterless_get_routes():
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

