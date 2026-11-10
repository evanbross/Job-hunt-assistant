# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-07
"""Invite-only registration (docs/cloud-deployment-readiness-plan.md 4.6), end to end
through the real routes and database: who may register, when an invite is checked and
used up, and the admin routes that manage invites."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import psycopg2
import psycopg2.extras
import pytest
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.security import generate_password_hash

import config
import routes.session_auth as session_auth_module
from backend.db.repositories.user_accounts import (
    DEFAULT_ADMIN_EMAIL,
    PLACEHOLDER_ACCOUNT_EMAIL,
)
from tests.db_roles import runtime_database_url

_PASSWORD = "correct horse battery staple"  # noqa: S105
_INVITES = "/api/settings/system/registration-invites"
_FRIEND = "friend@example.com"


@pytest.fixture(autouse=True)
def _test_db(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))


@pytest.fixture
def sent_emails(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(
        session_auth_module,
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


def _account(pg_dsn: str, email: str) -> dict[str, Any] | None:
    rows = _owner_query(pg_dsn, "SELECT * FROM user_accounts WHERE email = %s", (email,))
    return rows[0] if rows else None


def _logged_in(pg_dsn: str, flask_app: Flask, email: str, role: str) -> FlaskClient:
    _owner_query(
        pg_dsn,
        "INSERT INTO user_accounts (email, password_hash, role) VALUES (%s, %s, %s)",
        (email, generate_password_hash(_PASSWORD), role),
    )
    client = flask_app.test_client()
    login = client.post("/api/login", json={"email": email, "password": _PASSWORD})
    assert login.status_code == 200, login.get_json()
    return client


@pytest.fixture
def admin(pg_dsn: str, flask_app: Flask) -> FlaskClient:
    return _logged_in(pg_dsn, flask_app, "boss@example.com", "admin")


def _complete(client: FlaskClient, link: str) -> Any:
    token = parse_qs(urlparse(link).query)["token"][0]
    return client.post("/api/account/complete-registration", json={"token": token, "password": _PASSWORD})


# ─── who may register ───────────────────────────────────────────────────────────


def test_a_fresh_install_is_invite_only_and_turns_strangers_away(
    pg_dsn: str, flask_app: Flask, sent_emails: list[dict[str, str]]
) -> None:
    resp = flask_app.test_client().post("/api/account", json={"email": "stranger@example.com"})

    assert resp.status_code == 403
    assert "invitation" in resp.get_json()["error"]
    assert _account(pg_dsn, "stranger@example.com") is None  # no row written
    assert sent_emails == []  # and no email sent


def test_an_invited_email_registers_and_uses_up_the_invite(
    pg_dsn: str, flask_app: Flask, admin: FlaskClient, sent_emails: list[dict[str, str]]
) -> None:
    assert admin.post(_INVITES, json={"email": "  Friend@Example.COM "}).status_code == 201
    visitor = flask_app.test_client()

    started = visitor.post("/api/account", json={"email": "friend@example.com"})
    completed = _complete(visitor, sent_emails[-1]["link"])

    assert started.status_code == 202, started.get_json()
    assert completed.status_code == 200, completed.get_json()
    assert admin.get(_INVITES).get_json()["invites"] == []  # used up
    account = _account(pg_dsn, _FRIEND)
    assert account is not None and account["role"] == "user"


def test_an_invite_revoked_after_registration_started_stops_the_emailed_link(
    pg_dsn: str, flask_app: Flask, admin: FlaskClient, sent_emails: list[dict[str, str]]
) -> None:
    admin.post(_INVITES, json={"email": _FRIEND})
    visitor = flask_app.test_client()
    assert visitor.post("/api/account", json={"email": _FRIEND}).status_code == 202
    assert admin.post(f"{_INVITES}/revoke", json={"email": _FRIEND}).status_code == 200

    completed = _complete(visitor, sent_emails[-1]["link"])

    assert completed.status_code == 403
    account = _account(pg_dsn, _FRIEND)
    assert account is not None and account["password_hash"] is None  # rolled back
    assert visitor.get("/api/session").get_json()["authenticated"] is False


def test_open_registration_lets_anyone_register(
    pg_dsn: str, flask_app: Flask, open_registration: None, sent_emails: list[dict[str, str]]
) -> None:
    visitor = flask_app.test_client()

    assert visitor.post("/api/account", json={"email": "stranger@example.com"}).status_code == 202
    assert _complete(visitor, sent_emails[-1]["link"]).status_code == 200


def test_a_deleted_account_needs_a_new_invite(
    pg_dsn: str, flask_app: Flask, admin: FlaskClient, sent_emails: list[dict[str, str]]
) -> None:
    admin.post(_INVITES, json={"email": _FRIEND})
    visitor = flask_app.test_client()
    visitor.post("/api/account", json={"email": _FRIEND})
    assert _complete(visitor, sent_emails[-1]["link"]).status_code == 200
    _owner_query(pg_dsn, "DELETE FROM user_accounts WHERE email = %s", (_FRIEND,))

    again = flask_app.test_client().post("/api/account", json={"email": _FRIEND})

    assert again.status_code == 403


def test_a_fresh_database_seeds_no_claimable_placeholder_account(pg_dsn: str) -> None:
    """The other half of 4.6: the first stranger to register must not inherit the seed
    data by claiming a pre-seeded account. The only seeded account is the bootstrap
    admin, which can't be registered into and must finish setup before anything else."""
    accounts = _owner_query(pg_dsn, "SELECT email, must_complete_setup FROM user_accounts")

    assert {a["email"] for a in accounts} == {DEFAULT_ADMIN_EMAIL}
    assert PLACEHOLDER_ACCOUNT_EMAIL not in {a["email"] for a in accounts}
    assert all(a["must_complete_setup"] for a in accounts)


# ─── the admin routes ───────────────────────────────────────────────────────────


def test_only_an_admin_can_see_or_change_invites(pg_dsn: str, flask_app: Flask) -> None:
    user = _logged_in(pg_dsn, flask_app, "someone@example.com", "user")

    assert user.get(_INVITES).status_code == 403
    assert user.post(_INVITES, json={"email": _FRIEND}).status_code == 403
    assert user.post(f"{_INVITES}/revoke", json={"email": _FRIEND}).status_code == 403
    assert flask_app.test_client().get(_INVITES).status_code == 401


def test_an_admin_lists_adds_and_revokes_invites(pg_dsn: str, admin: FlaskClient) -> None:
    added = admin.post(_INVITES, json={"email": _FRIEND})
    listed = admin.get(_INVITES).get_json()
    revoked = admin.post(f"{_INVITES}/revoke", json={"email": _FRIEND})

    assert added.status_code == 201
    assert added.get_json()["email"] == _FRIEND
    assert listed["invite_only"] is True
    assert [i["email"] for i in listed["invites"]] == [_FRIEND]
    assert revoked.status_code == 200
    assert admin.get(_INVITES).get_json()["invites"] == []
    events = _owner_query(
        pg_dsn,
        "SELECT event_type FROM audit_log a JOIN user_accounts u ON u.id = a.user_id"
        " WHERE u.email = 'boss@example.com' AND event_type LIKE 'registration_invite_%%'"
        " ORDER BY a.id",
    )
    assert [e["event_type"] for e in events] == [
        "registration_invite_created",
        "registration_invite_revoked",
    ]


@pytest.mark.parametrize(
    ("body", "status"),
    [({"email": "not-an-email"}, 400), ({}, 400), ({"email": _FRIEND, "extra": 1}, 400)],
)
def test_an_invite_needs_a_plausible_email(admin: FlaskClient, body: dict[str, Any], status: int) -> None:
    assert admin.post(_INVITES, json=body).status_code == status


def test_inviting_twice_or_inviting_an_existing_account_is_a_conflict(admin: FlaskClient) -> None:
    admin.post(_INVITES, json={"email": _FRIEND})

    assert admin.post(_INVITES, json={"email": _FRIEND.upper()}).status_code == 409
    assert admin.post(_INVITES, json={"email": "boss@example.com"}).status_code == 409


def test_revoking_an_invite_that_does_not_exist_is_not_found(admin: FlaskClient) -> None:
    assert admin.post(f"{_INVITES}/revoke", json={"email": "nobody@example.com"}).status_code == 404


def test_an_invite_outlives_the_admin_who_made_it(pg_dsn: str, admin: FlaskClient) -> None:
    admin.post(_INVITES, json={"email": _FRIEND})

    _owner_query(pg_dsn, "DELETE FROM user_accounts WHERE email = 'boss@example.com'")

    rows = _owner_query(pg_dsn, "SELECT invited_by FROM registration_invites WHERE email = %s", (_FRIEND,))
    assert rows == [{"invited_by": None}]
