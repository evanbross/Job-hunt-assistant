# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""The per-account security log (backend/security/audit_log.py, GET /api/account/audit-log):
credential and identity events recorded through the real routes, read back only by the
account they belong to."""

from __future__ import annotations

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import config
import routes.session_auth as session_auth_module
from backend.security.audit_log import (
    ACCOUNT_REGISTERED,
    EMAIL_CHANGE_CONFIRMED,
    EMAIL_CHANGE_REQUESTED,
    EVENTS,
    EXTENSION_TOKEN_CREATED,
    EXTENSION_TOKEN_REVOKED,
    LOGIN_FAILURE,
    LOGIN_SUCCESS,
    PASSWORD_CHANGED,
    PASSWORD_RESET_COMPLETED,
    PASSWORD_RESET_REQUESTED,
    record_event,
)
from backend.utils.utils import get_repos

# ai: claude-sonnet-5 | 2026-09-27

_EMAIL = "owner@example.com"
_PASSWORD = "correct horse battery staple"  # noqa: S105


@pytest.fixture(autouse=True)
def _test_db(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", pg_dsn)


@pytest.fixture
def sent_emails(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(
        session_auth_module,
        "send_verification_email",
        lambda to, link, purpose: calls.append({"to": to, "link": link, "purpose": purpose}),
    )
    return calls


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def _seed_active_account(email: str = _EMAIL, password: str = _PASSWORD) -> int:
    with get_repos() as repos:
        repos["user_accounts"].create_pending(email, f"seed-{email}", "2099-01-01")
        account = repos["user_accounts"].complete_registration(
            f"seed-{email}", generate_password_hash(password)
        )
        repos["conn"].commit()
    return account["id"]


def _login(client, *, email: str = _EMAIL, password: str = _PASSWORD):
    return client.post("/api/login", json={"email": email, "password": password})


def _event_types(client) -> list[str]:
    resp = client.get("/api/account/audit-log")
    assert resp.status_code == 200
    return [e["event_type"] for e in resp.get_json()]


# ─── the event constants themselves ─────────────────────────────────────────────


def test_record_event_rejects_a_name_outside_the_fixed_set(pg_dsn: str) -> None:
    _seed_active_account()
    with get_repos(1) as repos, pytest.raises(ValueError, match="not_a_real_event"):
        record_event(repos, 1, "not_a_real_event")


def test_every_constant_is_in_the_allowed_set() -> None:
    for name in (
        LOGIN_SUCCESS, LOGIN_FAILURE, PASSWORD_CHANGED, PASSWORD_RESET_REQUESTED,
        PASSWORD_RESET_COMPLETED, EMAIL_CHANGE_REQUESTED, EMAIL_CHANGE_CONFIRMED,
        ACCOUNT_REGISTERED, EXTENSION_TOKEN_CREATED, EXTENSION_TOKEN_REVOKED,
    ):
        assert name in EVENTS


def test_a_store_failure_is_swallowed_not_raised(pg_dsn: str) -> None:
    """Best-effort: an audit-log write must never be the reason a real action fails."""
    user_id = _seed_active_account()
    with get_repos(user_id) as repos:
        broken = dict(repos)
        broken["audit_log"] = _BrokenAuditLog()
        record_event(broken, user_id, LOGIN_SUCCESS)  # must not raise


class _BrokenAuditLog:
    def insert(self, *args: object, **kwargs: object) -> int:
        import psycopg2

        raise psycopg2.OperationalError("simulated failure")


# ─── login ──────────────────────────────────────────────────────────────────────


def test_login_success_and_failure_are_both_recorded(client) -> None:
    _seed_active_account()
    assert _login(client, password="wrong").status_code == 401
    assert _login(client).status_code == 200

    types = _event_types(client)
    assert types[:2] == [LOGIN_SUCCESS, LOGIN_FAILURE]  # newest first


def test_a_login_attempt_against_an_unknown_email_records_nothing(client) -> None:
    _seed_active_account()
    assert _login(client, email="nobody@example.com", password="x").status_code == 401
    _login(client)  # so there is a session to read the log through

    assert LOGIN_FAILURE not in _event_types(client) or len(_event_types(client)) == 1


# ─── registration, password reset, email change ─────────────────────────────────


def test_completing_registration_records_account_registered(client, sent_emails) -> None:
    client.post("/api/account", json={"email": _EMAIL})
    from urllib.parse import parse_qs, urlparse

    token = parse_qs(urlparse(sent_emails[-1]["link"]).query)["token"][0]
    resp = client.post(
        "/api/account/complete-registration", json={"token": token, "password": _PASSWORD}
    )
    assert resp.status_code == 200

    assert _event_types(client) == [ACCOUNT_REGISTERED]


def test_password_reset_records_both_the_request_and_completion(client, sent_emails) -> None:
    from urllib.parse import parse_qs, urlparse

    _seed_active_account()
    client.post("/api/account/forgot-password", json={"email": _EMAIL})
    token = parse_qs(urlparse(sent_emails[-1]["link"]).query)["token"][0]
    resp = client.post(
        "/api/account/reset-password", json={"token": token, "password": "new-password-123"}
    )
    assert resp.status_code == 200

    types = _event_types(client)
    assert types[:2] == [PASSWORD_RESET_COMPLETED, PASSWORD_RESET_REQUESTED]


def test_email_change_records_the_request_and_the_confirmation(client, sent_emails) -> None:
    from urllib.parse import parse_qs, urlparse

    _seed_active_account()
    _login(client)
    client.post("/api/account/request-email-change", json={"new_email": "new@example.com"})
    token = parse_qs(urlparse(sent_emails[-1]["link"]).query)["token"][0]
    resp = client.post("/api/account/confirm-email-change", json={"token": token})
    assert resp.status_code == 200

    types = _event_types(client)
    assert types[:2] == [EMAIL_CHANGE_CONFIRMED, EMAIL_CHANGE_REQUESTED]


def test_change_password_records_the_event(client) -> None:
    _seed_active_account()
    _login(client)  # itself records LOGIN_SUCCESS
    resp = client.post(
        "/api/account/change-password",
        json={"current_password": _PASSWORD, "new_password": "new-password-123"},
    )
    assert resp.status_code == 200

    assert _event_types(client)[0] == PASSWORD_CHANGED


def test_a_failed_password_change_records_nothing(client) -> None:
    _seed_active_account()
    _login(client)
    before = _event_types(client)
    resp = client.post(
        "/api/account/change-password",
        json={"current_password": "wrong", "new_password": "new-password-123"},
    )
    assert resp.status_code == 401

    assert _event_types(client) == before  # unchanged: no new row for the failed attempt


# ─── extension tokens ────────────────────────────────────────────────────────────


def test_creating_and_revoking_a_token_are_both_recorded(client) -> None:
    _seed_active_account()
    _login(client)
    created = client.post("/api/extension-tokens", json={"label": "My laptop"})
    assert created.status_code == 201
    token_id = created.get_json()["record"]["id"]

    revoked = client.delete(f"/api/extension-tokens/{token_id}")
    assert revoked.status_code == 200

    types = _event_types(client)
    assert types[:2] == [EXTENSION_TOKEN_REVOKED, EXTENSION_TOKEN_CREATED]


# ─── detail and IP, and isolation between accounts ──────────────────────────────


def test_detail_round_trips_as_json(client) -> None:
    _seed_active_account()
    _login(client)
    client.post("/api/extension-tokens", json={"label": "My phone"})

    events = client.get("/api/account/audit-log").get_json()
    assert events[0]["detail"] == {"label": "My phone"}


def test_the_client_address_is_recorded(client) -> None:
    _seed_active_account()
    _login(client, password="wrong")
    _login(client)

    events = client.get("/api/account/audit-log").get_json()
    assert all(e["ip_address"] for e in events)


def test_one_account_never_sees_another_accounts_events(client) -> None:
    _seed_active_account()
    _login(client)
    client.post("/api/extension-tokens", json={"label": "Alice's token"})
    client.post("/api/logout")

    _seed_active_account("bob@example.com")
    _login(client, email="bob@example.com")

    assert _event_types(client) == [LOGIN_SUCCESS]  # not Alice's token/login events too
