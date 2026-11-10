# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-10
"""Accepting the Terms of Service (backend/security/terms_of_service.py), through the real
routes and database: sign-up on a hosted deployment requires it, an account that has not
accepted the current version is told so, and the version the page shows is the one the
server accepts."""

from __future__ import annotations

import json
import re
from pathlib import Path
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
from backend.security.terms_of_service import TERMS_VERSION
from tests.db_roles import runtime_database_url

_PASSWORD = "correct horse battery staple"  # noqa: S105
_EMAIL = "newcomer@example.com"
_PUBLIC_SITE_TS = (
    Path(__file__).resolve().parent.parent / "frontend" / "src" / "components" / "public" / "publicSite.ts"
)


@pytest.fixture(autouse=True)
def _test_db(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))


@pytest.fixture
def hosted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "APP_MODE", "hosted")
    # Hosted, registration links are built from it and it must be a public https URL.
    monkeypatch.setattr(config, "APP_BASE_URL", "https://jobs.example.com")


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


def _account(pg_dsn: str, email: str) -> dict[str, Any]:
    return _owner_query(pg_dsn, "SELECT * FROM user_accounts WHERE email = %s", (email,))[0]


def _audit(pg_dsn: str, user_id: int) -> list[dict[str, Any]]:
    """The account's audit events, with each detail (stored as JSON text) decoded."""
    rows = _owner_query(
        pg_dsn, "SELECT event_type, detail FROM audit_log WHERE user_id = %s ORDER BY id", (user_id,)
    )
    return [{**row, "detail": json.loads(row["detail"]) if row["detail"] else None} for row in rows]


def _registration_token(client: FlaskClient, sent_emails: list[dict[str, str]]) -> str:
    assert client.post("/api/account", json={"email": _EMAIL}).status_code == 202
    return parse_qs(urlparse(sent_emails[-1]["link"]).query)["token"][0]


def _logged_in(pg_dsn: str, flask_app: Flask, email: str, role: str = "user") -> FlaskClient:
    """An account made directly, the way every account that predates the terms was."""
    _owner_query(
        pg_dsn,
        "INSERT INTO user_accounts (email, password_hash, role) VALUES (%s, %s, %s)",
        (email, generate_password_hash(_PASSWORD), role),
    )
    client = flask_app.test_client()
    assert client.post("/api/login", json={"email": email, "password": _PASSWORD}).status_code == 200
    return client


def test_the_page_and_the_server_name_the_same_terms_version() -> None:
    match = re.search(r'export const TERMS_VERSION = "([^"]+)";', _PUBLIC_SITE_TS.read_text(encoding="utf-8"))
    assert match is not None, "TERMS_VERSION is missing from publicSite.ts"
    assert match.group(1) == TERMS_VERSION


# ── signing up ────────────────────────────────────────────────────────────────


def test_hosted_sign_up_without_accepting_the_terms_is_refused(
    pg_dsn: str, flask_app: Flask, open_registration: None, hosted: None, sent_emails: list[dict[str, str]]
) -> None:
    client = flask_app.test_client()
    token = _registration_token(client, sent_emails)

    resp = client.post("/api/account/complete-registration", json={"token": token, "password": _PASSWORD})

    assert resp.status_code == 400
    assert resp.get_json()["code"] == "terms_required"
    assert _account(pg_dsn, _EMAIL)["password_hash"] is None  # still pending; the link still works


def test_hosted_sign_up_records_the_terms_accepted(
    pg_dsn: str, flask_app: Flask, open_registration: None, hosted: None, sent_emails: list[dict[str, str]]
) -> None:
    client = flask_app.test_client()
    token = _registration_token(client, sent_emails)

    resp = client.post(
        "/api/account/complete-registration",
        json={"token": token, "password": _PASSWORD, "accept_terms": True},
    )

    assert resp.status_code == 200
    account = _account(pg_dsn, _EMAIL)
    assert account["terms_accepted_version"] == TERMS_VERSION
    assert account["terms_accepted_at"] is not None
    events = _audit(pg_dsn, account["id"])
    assert {"event_type": "terms_accepted", "detail": {"version": TERMS_VERSION}} in events
    assert client.get("/api/session").get_json()["terms_acceptance_required"] is False


def test_desktop_sign_up_has_no_terms(
    pg_dsn: str, flask_app: Flask, open_registration: None, sent_emails: list[dict[str, str]]
) -> None:
    client = flask_app.test_client()
    token = _registration_token(client, sent_emails)

    resp = client.post("/api/account/complete-registration", json={"token": token, "password": _PASSWORD})

    assert resp.status_code == 200
    assert _account(pg_dsn, _EMAIL)["terms_accepted_version"] is None
    assert client.get("/api/session").get_json()["terms_acceptance_required"] is False


# ── accounts that have not accepted the current version ──────────────────────


def test_an_account_that_never_accepted_must_accept_before_using_the_app(
    pg_dsn: str, flask_app: Flask, hosted: None
) -> None:
    client = _logged_in(pg_dsn, flask_app, "early@example.com")
    assert client.get("/api/session").get_json()["terms_acceptance_required"] is True

    resp = client.post("/api/account/accept-terms", json={"version": TERMS_VERSION})

    assert resp.status_code == 200
    assert client.get("/api/session").get_json()["terms_acceptance_required"] is False
    assert _account(pg_dsn, "early@example.com")["terms_accepted_version"] == TERMS_VERSION


def test_an_account_on_an_older_version_must_accept_again(
    pg_dsn: str, flask_app: Flask, hosted: None
) -> None:
    client = _logged_in(pg_dsn, flask_app, "early@example.com")
    _owner_query(
        pg_dsn,
        "UPDATE user_accounts SET terms_accepted_version = %s, terms_accepted_at = NOW() WHERE email = %s",
        ("2000-01-01", "early@example.com"),
    )

    assert client.get("/api/session").get_json()["terms_acceptance_required"] is True


def test_accepting_terms_the_page_no_longer_shows_is_refused(
    pg_dsn: str, flask_app: Flask, hosted: None
) -> None:
    client = _logged_in(pg_dsn, flask_app, "early@example.com")

    resp = client.post("/api/account/accept-terms", json={"version": "2000-01-01"})

    assert resp.status_code == 409
    assert resp.get_json()["code"] == "terms_changed"
    assert _account(pg_dsn, "early@example.com")["terms_accepted_version"] is None


def test_admins_and_desktop_accounts_are_never_asked(
    pg_dsn: str, flask_app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop_user = _logged_in(pg_dsn, flask_app, "desktop@example.com")
    assert desktop_user.get("/api/session").get_json()["terms_acceptance_required"] is False

    monkeypatch.setattr(config, "APP_MODE", "hosted")
    operator = _logged_in(pg_dsn, flask_app, "operator@example.com", role="admin")
    assert operator.get("/api/session").get_json()["terms_acceptance_required"] is False


def test_accepting_terms_needs_a_session(flask_app: Flask, hosted: None) -> None:
    resp = flask_app.test_client().post("/api/account/accept-terms", json={"version": TERMS_VERSION})
    assert resp.status_code == 401
