# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-09
"""Admin account tooling (docs/cloud-deployment-readiness-plan.md 4.5), end to end through
the real routes, the real database as the runtime role (so row-level security applies) and
the real Celery signal: list accounts, disable and re-enable one, resend a registration
link, see an account's failed background tasks."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import psycopg2
import psycopg2.extras
import pytest
from celery.signals import task_failure
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.security import generate_password_hash

import config
import routes.admin_accounts as admin_accounts_module
from backend.extension_auth.token_service import ExtensionTokenService
from backend.security.account_tokens import hash_account_token
from backend.security.data_lifecycle import (
    data_lifecycle_sweep_task,
    run_data_lifecycle_sweep,
)
from backend.task_failures import record_task_failure, task_user_id
from backend.utils.utils import get_repos
from routes.scan import run_scan_task
from tests.db_roles import runtime_database_url

_PASSWORD = "correct horse battery staple"  # noqa: S105
_ACCOUNTS = "/api/settings/system/accounts"


@pytest.fixture(autouse=True)
def _test_db(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))


@pytest.fixture
def sent_emails(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(
        admin_accounts_module,
        "send_verification_email",
        lambda to, link, purpose: calls.append({"to": to, "link": link, "purpose": purpose}),
    )
    return calls


def _owner_query(pg_dsn: str, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()] if cur.description else []
    finally:
        conn.close()


def _create(pg_dsn: str, email: str, role: str = "user", *, password: bool = True) -> int:
    rows = _owner_query(
        pg_dsn,
        "INSERT INTO user_accounts (email, password_hash, role, verification_token_hash, verification_purpose,"
        " verification_expires_at) VALUES (%s, %s, %s, %s, %s, NOW() + interval '1 day') RETURNING id",
        (
            email,
            generate_password_hash(_PASSWORD) if password else None,
            role,
            None if password else hash_account_token(f"first-token-{email}"),
            None if password else "registration",
        ),
    )
    return int(rows[0]["id"])


def _login(flask_app: Flask, email: str) -> Any:
    client = flask_app.test_client()
    return client, client.post("/api/login", json={"email": email, "password": _PASSWORD})


def _logged_in(flask_app: Flask, email: str) -> FlaskClient:
    client, response = _login(flask_app, email)
    assert response.status_code == 200, response.get_json()
    return client


@pytest.fixture
def admin_id(pg_dsn: str) -> int:
    return _create(pg_dsn, "boss@example.com", "admin")


@pytest.fixture
def admin(admin_id: int, flask_app: Flask) -> FlaskClient:
    return _logged_in(flask_app, "boss@example.com")


@pytest.fixture
def user_id(pg_dsn: str) -> int:
    return _create(pg_dsn, "worker@example.com")


def _audit(pg_dsn: str, user: int) -> list[str]:
    rows = _owner_query(pg_dsn, "SELECT event_type FROM audit_log WHERE user_id = %s ORDER BY id", (user,))
    return [row["event_type"] for row in rows]


# ── listing ───────────────────────────────────────────────────────────────────


def test_the_list_shows_each_accounts_state_and_never_its_secrets(
    pg_dsn: str, admin: FlaskClient, admin_id: int, user_id: int
) -> None:
    pending_id = _create(pg_dsn, "newbie@example.com", password=False)

    response = admin.get(_ACCOUNTS)

    assert response.status_code == 200
    body = response.get_json()
    by_id = {account["id"]: account for account in body["accounts"]}
    assert body["you"] == admin_id
    assert by_id[admin_id]["status"] == "active" and by_id[admin_id]["last_login_at"] is not None
    assert by_id[user_id]["status"] == "active" and by_id[user_id]["last_login_at"] is None
    assert by_id[pending_id]["status"] == "pending_verification"
    text = response.get_data(as_text=True)
    assert "password" not in text and "first-token" not in text and "verification" not in text.replace(
        "pending_verification", ""
    )


def test_only_an_admin_can_use_the_account_tools(flask_app: Flask, user_id: int) -> None:
    client = _logged_in(flask_app, "worker@example.com")

    assert client.get(_ACCOUNTS).status_code == 403
    assert client.post(f"{_ACCOUNTS}/{user_id}/disable").status_code == 403


# ── disabling ─────────────────────────────────────────────────────────────────


def test_disabling_ends_open_sessions_and_refuses_login_until_re_enabled(
    pg_dsn: str, flask_app: Flask, admin: FlaskClient, admin_id: int, user_id: int
) -> None:
    session = _logged_in(flask_app, "worker@example.com")
    assert session.get("/api/account").status_code == 200

    assert admin.post(f"{_ACCOUNTS}/{user_id}/disable").get_json() == {"status": "disabled"}

    ended = session.get("/api/account")
    assert ended.status_code == 401 and ended.get_json()["code"] == "account_unavailable"
    assert session.get("/api/account").get_json() == {"error": "Not authenticated"}  # session cleared
    _, refused = _login(flask_app, "worker@example.com")
    assert refused.status_code == 403 and refused.get_json()["code"] == "account_disabled"
    statuses = {a["id"]: a["status"] for a in admin.get(_ACCOUNTS).get_json()["accounts"]}
    assert statuses[user_id] == "disabled"

    assert admin.post(f"{_ACCOUNTS}/{user_id}/enable").get_json() == {"status": "enabled"}
    assert _logged_in(flask_app, "worker@example.com").get("/api/account").status_code == 200
    assert _audit(pg_dsn, admin_id)[-2:] == ["account_disabled", "account_enabled"]


def test_a_wrong_password_for_a_disabled_account_says_nothing_about_it(
    flask_app: Flask, admin: FlaskClient, user_id: int
) -> None:
    admin.post(f"{_ACCOUNTS}/{user_id}/disable")
    client = flask_app.test_client()

    response = client.post("/api/login", json={"email": "worker@example.com", "password": "not it at all"})

    assert response.status_code == 401 and response.get_json() == {"error": "Invalid email or password"}


def test_a_deleted_accounts_session_ends_too(pg_dsn: str, flask_app: Flask, user_id: int) -> None:
    session = _logged_in(flask_app, "worker@example.com")
    _owner_query(pg_dsn, "DELETE FROM user_accounts WHERE id = %s", (user_id,))

    assert session.get("/api/account").status_code == 401


def test_a_disabled_accounts_extension_tokens_and_scheduled_work_stop(
    admin: FlaskClient, user_id: int
) -> None:
    with get_repos(user_id) as repos:
        issued = ExtensionTokenService(repos["extension_tokens"]).issue(user_id, "laptop")
        repos["conn"].commit()

    def authenticates() -> bool:
        with get_repos() as repos:
            return ExtensionTokenService(repos["extension_tokens"]).authenticate(issued.plaintext) is not None

    def scheduled() -> bool:
        with get_repos() as repos:
            return user_id in repos["user_accounts"].list_active_user_ids()

    assert authenticates() and scheduled()
    admin.post(f"{_ACCOUNTS}/{user_id}/disable")
    assert not authenticates() and not scheduled()
    admin.post(f"{_ACCOUNTS}/{user_id}/enable")
    assert authenticates() and scheduled()


def test_an_admin_cannot_disable_their_own_account(admin: FlaskClient, admin_id: int) -> None:
    response = admin.post(f"{_ACCOUNTS}/{admin_id}/disable")

    assert response.status_code == 400
    assert admin.get(_ACCOUNTS).status_code == 200


def test_an_unknown_account_is_a_404(admin: FlaskClient) -> None:
    for path in ("disable", "enable", "resend-verification"):
        assert admin.post(f"{_ACCOUNTS}/999999/{path}").status_code == 404
    assert admin.get(f"{_ACCOUNTS}/999999/task-failures").status_code == 404


# ── resending a registration link ─────────────────────────────────────────────


def test_a_resent_link_replaces_the_old_one_and_completes_registration(
    pg_dsn: str, flask_app: Flask, admin: FlaskClient, admin_id: int, sent_emails: list[dict[str, str]]
) -> None:
    pending_id = _create(pg_dsn, "newbie@example.com", password=False)
    _owner_query(pg_dsn, "INSERT INTO registration_invites (email) VALUES ('newbie@example.com')")

    response = admin.post(f"{_ACCOUNTS}/{pending_id}/resend-verification")

    assert response.status_code == 200, response.get_json()
    assert [(e["to"], e["purpose"]) for e in sent_emails] == [("newbie@example.com", "registration")]
    token = parse_qs(urlparse(sent_emails[0]["link"]).query)["token"][0]
    client = flask_app.test_client()
    old = client.post(
        "/api/account/complete-registration", json={"token": "first-token-newbie@example.com", "password": _PASSWORD}
    )
    assert old.status_code == 400
    new = client.post("/api/account/complete-registration", json={"token": token, "password": _PASSWORD})
    assert new.status_code == 200, new.get_json()
    assert _audit(pg_dsn, admin_id)[-1] == "registration_link_resent"


def test_no_link_is_resent_to_a_finished_or_disabled_registration(
    pg_dsn: str, admin: FlaskClient, user_id: int, sent_emails: list[dict[str, str]]
) -> None:
    pending_id = _create(pg_dsn, "newbie@example.com", password=False)
    admin.post(f"{_ACCOUNTS}/{pending_id}/disable")

    assert admin.post(f"{_ACCOUNTS}/{user_id}/resend-verification").status_code == 409
    assert admin.post(f"{_ACCOUNTS}/{pending_id}/resend-verification").status_code == 409
    assert sent_emails == []
    token = _owner_query(pg_dsn, "SELECT verification_token_hash FROM user_accounts WHERE id = %s", (pending_id,))
    assert token == [{"verification_token_hash": hash_account_token("first-token-newbie@example.com")}]  # rolled back


# ── task failures ─────────────────────────────────────────────────────────────


def test_the_user_id_of_a_real_task_call_is_found_however_it_was_passed() -> None:
    assert task_user_id(run_scan_task, (7,), {}) == 7
    assert task_user_id(run_scan_task, (), {"user_id": 7, "rescan_all": True}) == 7
    assert task_user_id(data_lifecycle_sweep_task, (), {}) is None
    assert task_user_id(run_scan_task, ("7",), {}) is None


def test_a_failed_task_shows_on_its_account_redacted_and_only_there(
    pg_dsn: str, admin: FlaskClient, user_id: int
) -> None:
    other_id = _create(pg_dsn, "other@example.com")
    secret = "jha_" + "Q1w2E3r4" * 6
    # The real signal a worker sends, so the connected handler is what records it.
    task_failure.send(
        sender=run_scan_task,
        task_id="task-1",
        exception=ValueError(f"could not parse mail for recruiter@example.com with {secret}"),
        args=(user_id,),
        kwargs={},
    )

    failures = admin.get(f"{_ACCOUNTS}/{user_id}/task-failures").get_json()["failures"]

    assert [(f["task_name"], f["task_id"], f["error_type"]) for f in failures] == [
        (run_scan_task.name, "task-1", "ValueError")
    ]
    message = failures[0]["error_message"]
    assert "recruiter@example.com" not in message and secret not in message
    assert "[email]" in message and "[redacted]" in message
    assert admin.get(f"{_ACCOUNTS}/{other_id}/task-failures").get_json() == {"failures": []}


def test_a_task_with_no_account_records_nothing(pg_dsn: str) -> None:
    assert not record_task_failure(data_lifecycle_sweep_task, "t", RuntimeError("boom"), (), {})
    assert _owner_query(pg_dsn, "SELECT id FROM task_failures") == []


def test_old_task_failures_are_pruned_by_the_lifecycle_sweep(pg_dsn: str, user_id: int) -> None:
    _owner_query(
        pg_dsn,
        "INSERT INTO task_failures (user_id, task_name, error_type, error_message, failed_at)"
        " VALUES (%s, 'old', 'E', 'm', NOW() - interval '400 days'), (%s, 'new', 'E', 'm', NOW())",
        (user_id, user_id),
    )
    run_data_lifecycle_sweep(pg_dsn)

    assert [r["task_name"] for r in _owner_query(pg_dsn, "SELECT task_name FROM task_failures")] == ["new"]
