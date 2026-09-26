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

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from flask import Flask

import backend.crypto as crypto
import config
from backend.db import PostgresUserAccountRepository, get_connection
from backend.db.repositories.user_accounts import DEFAULT_ADMIN_EMAIL
from backend.db.schema import _migrate_legacy_greenhouse_session
from backend.scanning.sources.greenhouse_searcher import GreenhouseSearcher
from backend.scanning.sources.greenhouse_session_store import GreenhouseSessionStore
from backend.utils.utils import get_repos
from routes.greenhouse import greenhouse_bp

# ai: claude-sonnet-5 | 2026-09-26

STATE_A = {"cookies": [{"name": "session", "value": "cookie-of-user-a"}], "origins": []}
STATE_B = {"cookies": [{"name": "session", "value": "cookie-of-user-b"}], "origins": []}


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", key)
    monkeypatch.setattr(crypto, "_fernet", None)
    return key


def _new_user(conn: Any, email: str) -> int:
    account = PostgresUserAccountRepository(conn).create_pending(
        email, f"seed-{email}", "2099-01-01"
    )
    return account["id"]


@pytest.fixture
def two_users(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    monkeypatch.setattr(config, "DATABASE_URL", pg_dsn)
    conn = get_connection(pg_dsn)
    try:
        ids = _new_user(conn, "a@example.com"), _new_user(conn, "b@example.com")
        conn.commit()
    finally:
        conn.close()
    return ids


# ─── store ──────────────────────────────────────────────────────────────────────


def test_save_load_round_trip_and_encrypted_at_rest(two_users: tuple[int, int]) -> None:
    user_a, _ = two_users
    store = GreenhouseSessionStore(user_a)
    assert store.exists() is False and store.load() is None

    store.save(STATE_A)

    assert store.exists() is True
    assert store.load() == STATE_A
    with get_repos() as repos:
        cur = repos["conn"].cursor()
        cur.execute("SELECT state_encrypted FROM greenhouse_sessions WHERE user_id = %s", (user_a,))
        assert "cookie-of-user-a" not in cur.fetchone()["state_encrypted"]


def test_each_user_has_a_separate_session_and_clearing_is_scoped(two_users: tuple[int, int]) -> None:
    user_a, user_b = two_users
    GreenhouseSessionStore(user_a).save(STATE_A)
    GreenhouseSessionStore(user_b).save(STATE_B)

    assert GreenhouseSessionStore(user_a).load() == STATE_A
    assert GreenhouseSessionStore(user_b).load() == STATE_B

    assert GreenhouseSessionStore(user_a).clear() is True
    assert GreenhouseSessionStore(user_a).exists() is False
    assert GreenhouseSessionStore(user_b).load() == STATE_B
    assert GreenhouseSessionStore(user_a).clear() is False


def test_saving_again_replaces_the_session(two_users: tuple[int, int]) -> None:
    user_a, _ = two_users
    store = GreenhouseSessionStore(user_a)
    store.save(STATE_A)
    store.save(STATE_B)
    assert store.load() == STATE_B


# ─── searcher uses the caller's own session ─────────────────────────────────────


def test_the_searcher_opens_the_browser_with_only_its_users_session(two_users: tuple[int, int]) -> None:
    user_a, user_b = two_users
    GreenhouseSessionStore(user_a).save(STATE_A)
    GreenhouseSessionStore(user_b).save(STATE_B)
    browser = MagicMock()

    GreenhouseSearcher("b@example.com", GreenhouseSessionStore(user_b))._load_context(browser)

    assert browser.new_context.call_args.kwargs["storage_state"] == STATE_B


def test_the_searcher_starts_fresh_when_the_user_has_no_saved_session(two_users: tuple[int, int]) -> None:
    user_a, user_b = two_users
    GreenhouseSessionStore(user_a).save(STATE_A)
    browser = MagicMock()

    GreenhouseSearcher("b@example.com", GreenhouseSessionStore(user_b))._load_context(browser)

    assert "storage_state" not in browser.new_context.call_args.kwargs


def test_an_undecryptable_session_falls_back_to_a_fresh_context(
    two_users: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    user_a, _ = two_users
    GreenhouseSessionStore(user_a).save(STATE_A)
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(crypto, "_fernet", None)
    browser = MagicMock()

    GreenhouseSearcher("a@example.com", GreenhouseSessionStore(user_a))._load_context(browser)

    assert "storage_state" not in browser.new_context.call_args.kwargs


def test_a_user_without_a_session_is_not_authenticated(two_users: tuple[int, int]) -> None:
    user_a, user_b = two_users
    GreenhouseSessionStore(user_a).save(STATE_A)
    page = MagicMock()

    searcher = GreenhouseSearcher("b@example.com", GreenhouseSessionStore(user_b))

    assert searcher._is_authenticated(page, "https://my.greenhouse.io/jobs") is False
    page.goto.assert_not_called()


def test_persisting_a_login_saves_only_for_that_user(two_users: tuple[int, int]) -> None:
    user_a, user_b = two_users
    context = MagicMock()
    context.storage_state.return_value = STATE_B

    GreenhouseSearcher("b@example.com", GreenhouseSessionStore(user_b))._persist_session(context)

    assert GreenhouseSessionStore(user_b).load() == STATE_B
    assert GreenhouseSessionStore(user_a).exists() is False


# ─── clear-session route ────────────────────────────────────────────────────────


def test_clear_session_route_only_clears_the_callers_session(
    two_users: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    user_a, user_b = two_users
    GreenhouseSessionStore(user_a).save(STATE_A)
    GreenhouseSessionStore(user_b).save(STATE_B)
    monkeypatch.setattr("routes.greenhouse.current_user_id", lambda: user_a)
    flask_app = Flask(__name__)
    flask_app.register_blueprint(greenhouse_bp, url_prefix="/api")
    client = flask_app.test_client()

    assert client.post("/api/greenhouse/clear-session").get_json() == {"status": "session cleared"}
    assert client.post("/api/greenhouse/clear-session").get_json() == {"status": "no saved session"}
    assert GreenhouseSessionStore(user_b).load() == STATE_B


# ─── one-time migration of the legacy shared file ───────────────────────────────


def test_the_legacy_session_file_moves_to_the_first_account(pg_dsn: str, tmp_path: Path) -> None:
    legacy = tmp_path / "greenhouse_session.json"
    legacy.write_text(json.dumps(STATE_A), encoding="utf-8")
    conn = get_connection(pg_dsn)
    try:
        first_id = PostgresUserAccountRepository(conn).get_by_email(DEFAULT_ADMIN_EMAIL)["id"]
        later_id = _new_user(conn, "later@example.com")

        _migrate_legacy_greenhouse_session(conn)

        cur = conn.cursor()
        cur.execute("SELECT user_id FROM greenhouse_sessions")
        assert [row["user_id"] for row in cur.fetchall()] == [first_id]
        assert later_id != first_id
        assert not legacy.exists() and (tmp_path / "greenhouse_session.json.migrated").exists()
    finally:
        conn.close()


def test_a_bad_legacy_session_file_is_left_alone(pg_dsn: str, tmp_path: Path) -> None:
    legacy = tmp_path / "greenhouse_session.json"
    legacy.write_text("{not json", encoding="utf-8")
    conn = get_connection(pg_dsn)
    try:
        _migrate_legacy_greenhouse_session(conn)
        assert legacy.exists()
    finally:
        conn.close()
