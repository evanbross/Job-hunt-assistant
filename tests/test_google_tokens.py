# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from flask import Flask, session
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

import backend.crypto as crypto
import backend.email.google_token_store as google_token_store
import config
import routes.auth as auth_routes
from backend.db import (
    PostgresGoogleTokenRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.repositories.user_accounts import DEFAULT_ADMIN_EMAIL
from backend.db.row_level_security import set_current_user
from backend.db.schema import _migrate_legacy_google_files
from backend.email.gmail_client import GmailClient, NotAuthenticatedError
from backend.email.gmail_scopes import (
    GMAIL_MODIFY_SCOPE,
    GMAIL_SEND_SCOPE,
    gmail_scopes,
)
from backend.email.google_oauth_pending import MemoryPendingAuthStore, PendingGoogleAuth
from backend.email.google_token_store import (
    GOOGLE_REVOKE_URL,
    GoogleTokenStore,
    revoke_google_access,
)
from backend.settings_registry import SYSTEM_SETTING_KEYS, schema_for_scope
from backend.settings_service import get_setting, is_setting_configured
from backend.utils.utils import get_repos
from routes.auth import auth_bp
from routes.setup import setup_bp
from tests.db_roles import runtime_database_url
from tests.login_session import sign_in

# ai: claude-sonnet-5 | 2026-09-26

CLIENT_CONFIG = {
    "installed": {
        "client_id": "abc.apps.googleusercontent.com",
        "client_secret": "shhh",
        "redirect_uris": ["http://localhost"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", key)
    monkeypatch.setattr(crypto, "_fernet", None)
    return key


# ai: claude-opus-5-5 | 2026-11-10
class _FakeGoogleRevoke:
    """Stands in for Google's revoke endpoint: records each token sent, answers
    with `status`, or raises `error` like an unreachable network."""

    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.status = 200
        self.error: Exception | None = None

    def post(self, url: str, data: dict[str, str], timeout: float) -> httpx.Response:
        assert url == GOOGLE_REVOKE_URL
        if self.error is not None:
            raise self.error
        self.tokens.append(data["token"])
        return httpx.Response(self.status)


@pytest.fixture(autouse=True)
def google_revoke(monkeypatch: pytest.MonkeyPatch) -> _FakeGoogleRevoke:
    """No test in this file may reach the real Google: disconnect now revokes."""
    fake = _FakeGoogleRevoke()
    monkeypatch.setattr(google_token_store.httpx, "post", fake.post)
    return fake


def make_credentials(
    refresh_token: str = "refresh-abc",
    scopes: list[str] | None = None,
    expired: bool = False,
) -> Credentials:
    expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=-1 if expired else 1)
    return Credentials(
        token="access-token",
        refresh_token=refresh_token,
        client_id="cid",
        client_secret="csecret",
        token_uri="https://oauth2.googleapis.com/token",
        scopes=scopes or gmail_scopes(),
        expiry=expiry,
    )


def _new_user(conn: Any, email: str = "owner@example.com") -> int:
    account = PostgresUserAccountRepository(conn).create_pending(
        email, f"seed-{email}", "2099-01-01"
    )
    set_current_user(conn.cursor(), account["id"])
    return account["id"]


# ─── token store ────────────────────────────────────────────────────────────────


def test_save_then_load_round_trips_and_is_encrypted_at_rest(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        store = GoogleTokenStore(PostgresGoogleTokenRepository(conn))

        store.save(user_id, make_credentials(refresh_token="very-secret-refresh"))
        conn.commit()

        loaded = store.load(user_id)
        assert loaded is not None and loaded.refresh_token == "very-secret-refresh"
        cur = conn.cursor()
        cur.execute("SELECT token_encrypted FROM google_oauth_tokens WHERE user_id = %s", (user_id,))
        stored = cur.fetchone()["token_encrypted"]
        assert "very-secret-refresh" not in stored and "csecret" not in stored
    finally:
        conn.close()


def test_each_user_has_their_own_token_and_disconnect_is_scoped(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        first_id = _new_user(conn)
        second_id = _new_user(conn, "second@example.com")
        store = GoogleTokenStore(PostgresGoogleTokenRepository(conn))
        set_current_user(conn.cursor(), first_id)
        store.save(first_id, make_credentials("first-refresh"))
        set_current_user(conn.cursor(), second_id)
        store.save(second_id, make_credentials("second-refresh"))

        set_current_user(conn.cursor(), first_id)
        assert store.load(first_id).refresh_token == "first-refresh"
        set_current_user(conn.cursor(), second_id)
        assert store.load(second_id).refresh_token == "second-refresh"

        set_current_user(conn.cursor(), first_id)
        assert store.delete(first_id) is True
        assert store.is_connected(first_id) is False
        set_current_user(conn.cursor(), second_id)
        assert store.is_connected(second_id) is True
        set_current_user(conn.cursor(), first_id)
        assert store.delete(first_id) is False
    finally:
        conn.close()


def test_saving_again_replaces_the_token_and_scopes_are_reported(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        store = GoogleTokenStore(PostgresGoogleTokenRepository(conn))
        assert store.granted_scopes(user_id) is None  # ai: claude-opus-5-5 | 2026-10-24

        store.save(user_id, make_credentials("old", scopes=["scope-a"]))
        store.save(user_id, make_credentials("new", scopes=["scope-a", GMAIL_SEND_SCOPE]))

        assert store.load(user_id).refresh_token == "new"
        assert GMAIL_SEND_SCOPE in store.granted_scopes(user_id)
    finally:
        conn.close()


def test_deleting_a_user_removes_their_google_token(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        store = GoogleTokenStore(PostgresGoogleTokenRepository(conn))
        store.save(user_id, make_credentials())

        PostgresUserAccountRepository(conn).delete(user_id)
        assert store.is_connected(user_id) is False
    finally:
        conn.close()


# ─── Gmail client uses the caller's own token ───────────────────────────────────


@pytest.fixture
def two_connected_users(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        first_id, second_id = _new_user(conn), _new_user(conn, "second@example.com")
        store = GoogleTokenStore(PostgresGoogleTokenRepository(conn))
        set_current_user(conn.cursor(), first_id)
        store.save(first_id, make_credentials("first-refresh", expired=True))
        set_current_user(conn.cursor(), second_id)
        store.save(second_id, make_credentials("second-refresh", expired=True))
        conn.commit()
    finally:
        conn.close()
    return first_id, second_id


def _stored_refresh_token(pg_dsn: str, user_id: int) -> str | None:
    conn = get_connection(pg_dsn)
    try:
        set_current_user(conn.cursor(), user_id)
        creds = GoogleTokenStore(PostgresGoogleTokenRepository(conn)).load(user_id)
        return creds.refresh_token if creds else None
    finally:
        conn.close()


def test_gmail_client_requires_a_connected_account(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(NotAuthenticatedError, match="not connected"):
        GmailClient(user_id)


def test_a_refresh_persists_only_for_the_calling_user(
    pg_dsn: str, two_connected_users: tuple[int, int]
) -> None:
    first_id, second_id = two_connected_users

    def fake_refresh(self: Credentials, _request: object) -> None:
        self.token = "refreshed"
        self.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

    with patch.object(Credentials, "refresh", fake_refresh), patch(
        "backend.email.gmail_client.build", return_value=MagicMock()
    ):
        GmailClient(first_id)

    conn = get_connection(pg_dsn)
    try:
        store = GoogleTokenStore(PostgresGoogleTokenRepository(conn))
        set_current_user(conn.cursor(), first_id)
        assert store.load(first_id).token == "refreshed"
        set_current_user(conn.cursor(), second_id)
        assert store.load(second_id).token == "access-token"
    finally:
        conn.close()


def test_a_revoked_token_is_deleted_for_that_user_only(
    pg_dsn: str, two_connected_users: tuple[int, int]
) -> None:
    first_id, second_id = two_connected_users

    def rejecting_refresh(self: Credentials, _request: object) -> None:
        raise RefreshError("invalid_grant")

    with patch.object(Credentials, "refresh", rejecting_refresh), pytest.raises(
        NotAuthenticatedError, match="rejected"
    ):
        GmailClient(first_id)

    assert _stored_refresh_token(pg_dsn, first_id) is None
    assert _stored_refresh_token(pg_dsn, second_id) == "second-refresh"


# ─── OAuth routes ───────────────────────────────────────────────────────────────


@pytest.fixture
def auth_client(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, int], dict[str, Any]]:
    """A Flask client for the auth blueprint, with a switchable 'logged-in' user."""
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        ids = {"a": _new_user(conn, "a@example.com"), "b": _new_user(conn, "b@example.com")}
        conn.commit()
    finally:
        conn.close()
    session_user = {"id": ids["a"]}
    monkeypatch.setattr("routes.auth.current_user_id", lambda: session_user["id"])
    flask_app = Flask(__name__)
    flask_app.secret_key = "test"
    flask_app.register_blueprint(auth_bp, url_prefix="/api")

    @flask_app.before_request
    def _sync_rls_session() -> None:
        sign_in(session, session_user["id"])

    return flask_app.test_client(), ids, session_user


def _configure_google_client(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO system_settings (key, value) VALUES (%s, %s)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            ("google_oauth_client_config", crypto.encrypt_secret(json.dumps(CLIENT_CONFIG))),
        )
        conn.commit()
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-10
def test_disconnect_revokes_the_grant_at_google_then_forgets_the_token(
    pg_dsn: str, auth_client: Any, google_revoke: _FakeGoogleRevoke
) -> None:
    """Deleting only our copy left the app listed in the user's Google account, with
    a token that still worked. The refresh token is what ends the whole grant."""
    client, ids, _session_user = auth_client
    conn = get_connection(pg_dsn)
    try:
        set_current_user(conn.cursor(), ids["a"])
        GoogleTokenStore(PostgresGoogleTokenRepository(conn)).save(ids["a"], make_credentials("a-refresh"))
        conn.commit()
    finally:
        conn.close()

    resp = client.post("/api/auth/disconnect")

    assert resp.get_json() == {"status": "disconnected", "revoked": True}
    assert google_revoke.tokens == ["a-refresh"]
    assert client.get("/api/auth/status").get_json()["authenticated"] is False


# ai: claude-opus-5-5 | 2026-11-10
def test_disconnect_still_forgets_the_token_when_google_is_unreachable(
    pg_dsn: str, auth_client: Any, google_revoke: _FakeGoogleRevoke
) -> None:
    client, ids, _session_user = auth_client
    conn = get_connection(pg_dsn)
    try:
        set_current_user(conn.cursor(), ids["a"])
        GoogleTokenStore(PostgresGoogleTokenRepository(conn)).save(ids["a"], make_credentials("a-refresh"))
        conn.commit()
    finally:
        conn.close()
    google_revoke.error = httpx.ConnectError("no route to host")

    resp = client.post("/api/auth/disconnect")

    assert resp.get_json() == {"status": "disconnected", "revoked": False}
    assert client.get("/api/auth/status").get_json()["authenticated"] is False


# ai: claude-opus-5-5 | 2026-11-10
@pytest.mark.parametrize(("status", "revoked"), [(200, True), (400, True), (503, False)])
def test_revoke_treats_an_already_invalid_token_as_revoked(
    google_revoke: _FakeGoogleRevoke, status: int, revoked: bool
) -> None:
    """Google answers 400 for a token that is already invalid (e.g. the user removed
    the app in their Google account): access is gone either way."""
    google_revoke.status = status
    assert revoke_google_access("tok") is revoked


def test_status_and_disconnect_only_concern_the_logged_in_user(
    pg_dsn: str, auth_client: Any
) -> None:
    client, ids, session_user = auth_client
    conn = get_connection(pg_dsn)
    try:
        store = GoogleTokenStore(PostgresGoogleTokenRepository(conn))
        set_current_user(conn.cursor(), ids["a"])
        store.save(ids["a"], make_credentials("a-refresh", scopes=[GMAIL_MODIFY_SCOPE, GMAIL_SEND_SCOPE]))
        set_current_user(conn.cursor(), ids["b"])
        store.save(ids["b"], make_credentials("b-refresh", scopes=["other"]))
        conn.commit()
    finally:
        conn.close()

    assert client.get("/api/auth/status").get_json() == {"authenticated": True, "has_required_scopes": True}
    session_user["id"] = ids["b"]
    assert client.get("/api/auth/status").get_json() == {"authenticated": True, "has_required_scopes": False}

    assert client.post("/api/auth/disconnect").status_code == 200
    assert client.get("/api/auth/status").get_json()["authenticated"] is False
    session_user["id"] = ids["a"]
    assert client.get("/api/auth/status").get_json()["authenticated"] is True


# ai: claude-opus-5-5 | 2026-11-09
def test_a_modify_only_token_needs_reconnecting_on_desktop_but_not_hosted(
    pg_dsn: str, auth_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hosted never asks for gmail.send (plan 3.1), so a token without it is complete
    there; on desktop the same token is missing the health-check email's scope."""
    client, ids, _session_user = auth_client
    conn = get_connection(pg_dsn)
    try:
        set_current_user(conn.cursor(), ids["a"])
        GoogleTokenStore(PostgresGoogleTokenRepository(conn)).save(
            ids["a"], make_credentials("a-refresh", scopes=[GMAIL_MODIFY_SCOPE])
        )
        conn.commit()
    finally:
        conn.close()

    assert client.get("/api/auth/status").get_json()["has_required_scopes"] is False
    monkeypatch.setattr(config, "APP_MODE", "hosted")
    assert client.get("/api/auth/status").get_json()["has_required_scopes"] is True


# ai: claude-opus-5-5 | 2026-11-09
@pytest.mark.parametrize(
    ("app_mode", "expected_scopes"),
    [("desktop", [GMAIL_MODIFY_SCOPE, GMAIL_SEND_SCOPE]), ("hosted", [GMAIL_MODIFY_SCOPE])],
)
def test_the_consent_url_asks_for_this_deployments_gmail_scopes(
    pg_dsn: str,
    auth_client: Any,
    pending_google_auths: MemoryPendingAuthStore,
    monkeypatch: pytest.MonkeyPatch,
    app_mode: str,
    expected_scopes: list[str],
) -> None:
    """What Google's consent screen shows is what the verification review sizes (plan 3.1)."""
    client, _, _ = auth_client
    _configure_google_client(pg_dsn)
    monkeypatch.setattr(config, "APP_MODE", app_mode)

    response = client.get("/api/auth/google")

    assert response.status_code == 200
    query = parse_qs(urlparse(response.get_json()["auth_url"]).query)
    assert query["scope"][0].split(" ") == expected_scopes


def test_starting_auth_without_an_admin_configured_client_is_a_clear_400(auth_client: Any) -> None:
    client, _, _ = auth_client
    response = client.get("/api/auth/google")
    assert response.status_code == 400
    assert "administrator" in response.get_json()["error"]


def test_starting_auth_records_which_user_began_the_flow(
    pg_dsn: str, auth_client: Any, pending_google_auths: MemoryPendingAuthStore
) -> None:
    client, ids, _ = auth_client
    _configure_google_client(pg_dsn)

    response = client.get("/api/auth/google")

    assert response.status_code == 200
    assert response.get_json()["auth_url"].startswith("https://accounts.google.com/")
    assert [flow.user_id for flow in pending_google_auths.flows.values()] == [ids["a"]]


# ai: claude-opus-5-5 | 2026-11-02
@pytest.mark.parametrize(
    "headers",
    [{"Origin": "http://jha.localhost"}, {"Referer": "https://jha.localhost/settings"}, {"Origin": "null"}],
)
def test_starting_auth_off_the_redirect_host_is_refused_with_where_to_go(
    pg_dsn: str,
    auth_client: Any,
    headers: dict[str, str],
    pending_google_auths: MemoryPendingAuthStore,
) -> None:
    """The login cookie is host-scoped, so a flow started on jha.localhost would only
    fail at the callback on localhost -- after the user already approved on Google."""
    client, _, _ = auth_client
    _configure_google_client(pg_dsn)

    response = client.get("/api/auth/google", headers=headers)

    assert response.status_code == 409
    assert response.get_json()["connect_url"].startswith("http://localhost:")
    assert pending_google_auths.flows == {}


# ai: claude-opus-5-5 | 2026-11-02
def test_the_callback_returns_to_the_origin_the_flow_started_from(
    pg_dsn: str, auth_client: Any, pending_google_auths: MemoryPendingAuthStore
) -> None:
    client, _, _ = auth_client
    _configure_google_client(pg_dsn)
    assert client.get(
        "/api/auth/google", headers={"Referer": "http://localhost:5174/settings"}
    ).status_code == 200
    (state,) = pending_google_auths.flows
    fake_flow = MagicMock()
    fake_flow.credentials = make_credentials("fresh-from-google")

    with patch.object(auth_routes, "_build_flow", return_value=fake_flow):
        response = client.get(f"/api/auth/google/callback?code=abc&state={state}")

    assert response.headers["Location"] == "http://localhost:5174/?auth=success"


def test_callback_saves_the_token_for_the_user_who_started_the_flow(
    pg_dsn: str, auth_client: Any, pending_google_auths: MemoryPendingAuthStore
) -> None:
    client, ids, _ = auth_client
    _configure_google_client(pg_dsn)
    fake_flow = MagicMock()
    fake_flow.credentials = make_credentials("fresh-from-google")
    pending_google_auths.put(
        "good-state", PendingGoogleAuth("v", ids["a"], "http://localhost:5174")
    )

    with patch.object(auth_routes, "_build_flow", return_value=fake_flow):
        response = client.get("/api/auth/google/callback?code=abc&state=good-state")

    assert response.status_code == 302
    assert response.headers["Location"] == "http://localhost:5174/?auth=success"
    assert _stored_refresh_token(pg_dsn, ids["a"]) == "fresh-from-google"
    assert _stored_refresh_token(pg_dsn, ids["b"]) is None
    assert fake_flow.code_verifier == "v"  # the verifier recorded at start, not a new one
    assert pending_google_auths.flows == {}  # single use


def test_callback_refuses_a_flow_started_by_a_different_account(
    pg_dsn: str, auth_client: Any, pending_google_auths: MemoryPendingAuthStore
) -> None:
    client, ids, session_user = auth_client
    _configure_google_client(pg_dsn)
    pending_google_auths.put(
        "their-state", PendingGoogleAuth("v", ids["a"], "http://localhost:5174")
    )
    session_user["id"] = ids["b"]

    with patch.object(auth_routes, "_build_flow") as build_flow:
        response = client.get("/api/auth/google/callback?code=abc&state=their-state")

    assert response.status_code == 403
    build_flow.assert_not_called()
    assert _stored_refresh_token(pg_dsn, ids["a"]) is None
    assert _stored_refresh_token(pg_dsn, ids["b"]) is None


def test_callback_rejects_missing_code_and_unknown_state(auth_client: Any) -> None:
    client, _, _ = auth_client
    assert client.get("/api/auth/google/callback").status_code == 400
    assert client.get("/api/auth/google/callback?code=abc&state=nope").status_code == 400


# ─── admin-managed OAuth client config ──────────────────────────────────────────


@pytest.fixture
def setup_client(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, int], dict[str, Any]]:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        admin = PostgresUserAccountRepository(conn).get_by_email(DEFAULT_ADMIN_EMAIL)["id"]
        member = _new_user(conn, "member@example.com")
        conn.commit()
    finally:
        conn.close()
    session_user = {"id": admin}
    monkeypatch.setattr("routes.setup.current_user_id", lambda: session_user["id"])
    flask_app = Flask(__name__)
    flask_app.secret_key = "test"
    flask_app.register_blueprint(setup_bp, url_prefix="/api")

    @flask_app.before_request
    def _sync_rls_session() -> None:
        sign_in(session, session_user["id"])

    return flask_app.test_client(), {"admin": admin, "member": member}, session_user


def test_only_an_admin_can_upload_the_google_client_config(pg_dsn: str, setup_client: Any) -> None:
    client, ids, session_user = setup_client
    session_user["id"] = ids["member"]

    assert client.post("/api/setup/upload-credentials", json=CLIENT_CONFIG).status_code == 403

    with get_repos() as repos:
        assert is_setting_configured("google_oauth_client_config", None, repos) is False


def test_admin_upload_is_stored_encrypted_as_a_system_setting(pg_dsn: str, setup_client: Any) -> None:
    client, _, _ = setup_client

    response = client.post("/api/setup/upload-credentials", json=CLIENT_CONFIG)

    assert response.status_code == 200
    with get_repos() as repos:
        assert json.loads(get_setting("google_oauth_client_config", None, repos)) == CLIENT_CONFIG
        cur = repos["conn"].cursor()
        cur.execute("SELECT value FROM system_settings WHERE key = 'google_oauth_client_config'")
        assert "shhh" not in cur.fetchone()["value"]


@pytest.mark.parametrize("body", [{}, {"installed": {"client_id": "x"}}, {"web": "nope"}])
def test_admin_upload_rejects_malformed_client_config(setup_client: Any, body: dict[str, Any]) -> None:
    client, _, _ = setup_client
    assert client.post("/api/setup/upload-credentials", json=body).status_code == 400


def test_the_client_config_setting_is_hidden_from_the_generic_settings_api() -> None:
    assert "google_oauth_client_config" not in SYSTEM_SETTING_KEYS
    assert "google_oauth_client_config" not in {f["key"] for f in schema_for_scope("system")}


# ─── one-time migration of the legacy shared files ──────────────────────────────


def _legacy_files(tmp_path: Path) -> tuple[Path, Path]:
    creds_file, token_file = tmp_path / "google_credentials.json", tmp_path / "token.json"
    creds_file.write_text(json.dumps(CLIENT_CONFIG), encoding="utf-8")
    token_file.write_text(make_credentials("legacy-refresh").to_json(), encoding="utf-8")
    return creds_file, token_file


def test_legacy_files_move_into_the_database_and_are_renamed(
    pg_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    creds_file, token_file = _legacy_files(tmp_path)
    conn = get_connection(pg_dsn)
    try:
        first_id = PostgresUserAccountRepository(conn).get_by_email(DEFAULT_ADMIN_EMAIL)["id"]
        _new_user(conn, "later@example.com")

        _migrate_legacy_google_files(conn)

        assert not creds_file.exists() and not token_file.exists()
        assert (tmp_path / "google_credentials.json.migrated").exists()
        assert (tmp_path / "token.json.migrated").exists()
        assert GoogleTokenStore(PostgresGoogleTokenRepository(conn)).load(first_id).refresh_token == "legacy-refresh"
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM google_oauth_tokens")
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT value FROM system_settings WHERE key = 'google_oauth_client_config'")
        assert json.loads(crypto.decrypt_secret(cur.fetchone()["value"])) == CLIENT_CONFIG
    finally:
        conn.close()


def test_legacy_migration_never_overwrites_an_existing_token_or_config(
    pg_dsn: str, tmp_path: Path
) -> None:
    creds_file, token_file = _legacy_files(tmp_path)
    conn = get_connection(pg_dsn)
    try:
        first_id = PostgresUserAccountRepository(conn).get_by_email(DEFAULT_ADMIN_EMAIL)["id"]
        store = GoogleTokenStore(PostgresGoogleTokenRepository(conn))
        set_current_user(conn.cursor(), first_id)
        store.save(first_id, make_credentials("already-here"))
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO system_settings (key, value) VALUES (%s, %s)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            ("google_oauth_client_config", crypto.encrypt_secret('{"kept": true}')),
        )

        _migrate_legacy_google_files(conn)

        assert store.load(first_id).refresh_token == "already-here"
        cur.execute("SELECT value FROM system_settings WHERE key = 'google_oauth_client_config'")
        assert json.loads(crypto.decrypt_secret(cur.fetchone()["value"])) == {"kept": True}
        assert not creds_file.exists() and not token_file.exists()
    finally:
        conn.close()


def test_legacy_migration_leaves_unusable_or_undecryptable_files_in_place(
    pg_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    creds_file, token_file = _legacy_files(tmp_path)
    creds_file.write_text("{not json", encoding="utf-8")
    conn = get_connection(pg_dsn)
    try:
        _migrate_legacy_google_files(conn)
        assert creds_file.exists() and not token_file.exists()

        (tmp_path / "token.json.migrated").rename(token_file)
        monkeypatch.delenv("SETTINGS_ENCRYPTION_KEY")
        monkeypatch.setattr(crypto, "_fernet", None)
        _migrate_legacy_google_files(conn)
        assert token_file.exists()
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-10-24
def test_status_counts_an_undecryptable_token_as_connected(pg_dsn: str, auth_client: Any) -> None:
    """A stored token that no longer decrypts (SETTINGS_ENCRYPTION_KEY changed) is
    still a connection the user has to fix or disconnect -- not "never connected"."""
    client, ids, _session_user = auth_client
    conn = get_connection(pg_dsn)
    try:
        set_current_user(conn.cursor(), ids["a"])
        PostgresGoogleTokenRepository(conn).upsert(ids["a"], "not-a-fernet-token", "2026-10-24T00:00:00+00:00")
        conn.commit()
    finally:
        conn.close()

    assert client.get("/api/auth/status").get_json() == {"authenticated": True, "has_required_scopes": False}
