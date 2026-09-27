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

import importlib.util
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from dotenv import dotenv_values
from flask import Flask, session

import app as app_module
import backend.crypto as crypto
import config
from backend.db import (
    PostgresGoogleTokenRepository,
    PostgresGreenhouseSessionRepository,
    PostgresResumeRepository,
    PostgresUserAccountRepository,
    get_connection,
    open_repositories,
)
from backend.db.row_level_security import disable_for_migrations, set_current_user
from backend.security.key_rotation import RotationError, rotate_all
from backend.security.secrets_policy import (
    ENCRYPTION_KEY_ENV,
    ENCRYPTION_KEY_FALLBACKS_ENV,
    FLASK_SECRET_ENV,
    FLASK_SECRET_FALLBACKS_ENV,
    encryption_key_problem,
    flask_secret_problem,
    generate_flask_secret,
)
from backend.security.session_cookies import (
    PLAIN_COOKIE_NAME,
    SECURE_COOKIE_NAME,
    cookies_should_be_secure,
    normalize_secure_mode,
    session_cookie_config,
)
from backend.settings_registry import SETTINGS
from backend.settings_service import get_setting, set_setting

# ai: claude-sonnet-5 | 2026-09-26

REPO_ROOT = Path(__file__).resolve().parent.parent


# ─── session cookie configuration ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("base_url", "mode", "secure"),
    [
        ("https://jobs.example.com", "auto", True),
        ("HTTPS://jobs.example.com/", "auto", True),
        ("http://localhost:5004", "auto", False),
        ("http://100.64.1.2:5004", "auto", False),
        ("", "auto", False),
        ("http://localhost:5004", "true", True),
        ("https://jobs.example.com", "false", False),
    ],
)
def test_secure_follows_the_base_url_unless_forced(base_url: str, mode: str, secure: bool) -> None:
    assert cookies_should_be_secure(base_url, mode) is secure


def test_an_unknown_secure_mode_is_an_error() -> None:
    assert normalize_secure_mode("") == "auto"
    with pytest.raises(ValueError, match="SESSION_COOKIE_SECURE"):
        normalize_secure_mode("sometimes")


def test_the_cookie_config_is_always_httponly_and_lax() -> None:
    for url in ("https://a.example.com", "http://localhost:5004"):
        settings = session_cookie_config(url)
        assert settings["SESSION_COOKIE_HTTPONLY"] is True
        assert settings["SESSION_COOKIE_SAMESITE"] == "Lax"
        assert settings["SESSION_COOKIE_PATH"] == "/"


def test_a_secure_cookie_gets_the_host_prefix() -> None:
    assert session_cookie_config("https://a.example.com")["SESSION_COOKIE_NAME"] == SECURE_COOKIE_NAME
    assert session_cookie_config("http://localhost:5004")["SESSION_COOKIE_NAME"] == PLAIN_COOKIE_NAME


def _cookie_app(settings: dict[str, Any], secret: str, fallbacks: tuple[str, ...] = ()) -> Flask:
    flask_app = Flask(__name__)
    flask_app.secret_key = secret
    flask_app.config["SECRET_KEY_FALLBACKS"] = list(fallbacks)
    flask_app.config.update(settings)

    @flask_app.route("/login")
    def login() -> str:
        session["user_id"] = 7
        return "ok"

    @flask_app.route("/whoami")
    def whoami() -> str:
        return str(session.get("user_id"))

    return flask_app


def _set_cookie_header(response: Any) -> str:
    return response.headers.getlist("Set-Cookie")[0]


def test_the_real_set_cookie_header_carries_every_protective_flag_when_secure() -> None:
    flask_app = _cookie_app(session_cookie_config("https://jobs.example.com"), generate_flask_secret())

    header = _set_cookie_header(flask_app.test_client().get("/login", base_url="https://jobs.example.com"))

    assert header.startswith(f"{SECURE_COOKIE_NAME}=")
    for attribute in ("Secure", "HttpOnly", "SameSite=Lax", "Path=/"):
        assert attribute in header
    assert "Domain" not in header


def test_the_real_set_cookie_header_over_plain_http() -> None:
    flask_app = _cookie_app(session_cookie_config("http://localhost:5004"), generate_flask_secret())

    header = _set_cookie_header(flask_app.test_client().get("/login"))

    assert header.startswith(f"{PLAIN_COOKIE_NAME}=")
    assert "HttpOnly" in header and "SameSite=Lax" in header
    assert "Secure" not in header


def test_the_app_import_defaults_are_already_httponly_and_lax() -> None:
    assert app_module.app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app_module.app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


@pytest.fixture
def restore_cookie_config() -> Iterator[None]:
    keys = ("SESSION_COOKIE_SECURE", "SESSION_COOKIE_NAME", "SESSION_COOKIE_HTTPONLY", "SESSION_COOKIE_SAMESITE", "SESSION_COOKIE_PATH")
    original = {key: app_module.app.config.get(key) for key in keys}
    yield
    app_module.app.config.update(original)


def _set_base_url(pg_dsn: str, url: str) -> None:
    repos = open_repositories(pg_dsn)
    try:
        set_setting("app_base_url", url, None, repos)
        repos["conn"].commit()
    finally:
        repos["conn"].close()


def test_the_app_derives_cookie_flags_from_the_stored_base_url(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch, restore_cookie_config: None
) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", pg_dsn)
    monkeypatch.setattr(config, "SESSION_COOKIE_SECURE_MODE", "auto")
    _set_base_url(pg_dsn, "https://jobs.example.com")

    app_module._apply_session_cookie_config("production")

    assert app_module.app.config["SESSION_COOKIE_SECURE"] is True
    assert app_module.app.config["SESSION_COOKIE_NAME"] == SECURE_COOKIE_NAME


def test_production_over_http_warns_that_cookies_are_not_secure(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch, restore_cookie_config: None, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", pg_dsn)
    monkeypatch.setattr(config, "SESSION_COOKIE_SECURE_MODE", "auto")
    _set_base_url(pg_dsn, "http://localhost:5004")

    with caplog.at_level(logging.WARNING):
        app_module._apply_session_cookie_config("production")
        assert "not marked Secure" in caplog.text
        caplog.clear()
        app_module._apply_session_cookie_config("development")
        assert "not marked Secure" not in caplog.text

    assert app_module.app.config["SESSION_COOKIE_SECURE"] is False


def test_sessions_survive_a_signing_secret_rotation_only_with_the_fallback() -> None:
    old_secret, new_secret = generate_flask_secret(), generate_flask_secret()
    old_app = _cookie_app(session_cookie_config("http://localhost"), old_secret)
    old_client = old_app.test_client()
    old_client.get("/login")
    cookie_value = old_client.get_cookie(PLAIN_COOKIE_NAME).value

    def whoami(flask_app: Flask) -> str:
        client = flask_app.test_client()
        client.set_cookie(PLAIN_COOKIE_NAME, cookie_value)
        return client.get("/whoami").get_data(as_text=True)

    rotated_with_fallback = _cookie_app(session_cookie_config("http://localhost"), new_secret, (old_secret,))
    rotated_without_fallback = _cookie_app(session_cookie_config("http://localhost"), new_secret)

    assert whoami(rotated_with_fallback) == "7"
    assert whoami(rotated_without_fallback) == "None"


# ─── re-encrypting stored values ────────────────────────────────────────────────


def _make_key() -> str:
    return Fernet.generate_key().decode()


def _use_keys(monkeypatch: pytest.MonkeyPatch, primary: str, fallbacks: str = "") -> None:
    monkeypatch.setenv(ENCRYPTION_KEY_ENV, primary)
    monkeypatch.setenv(ENCRYPTION_KEY_FALLBACKS_ENV, fallbacks)
    monkeypatch.setattr(crypto, "_fernet", None)


def _clear_seeded_secrets(pg_dsn: str) -> None:
    """init_db seeds the first account's secret settings from the environment,
    encrypted under whatever key was active when the fixture ran. Remove them so
    each test controls exactly which key every ciphertext is under."""
    secret_keys = [key for key, spec in SETTINGS.items() if spec.is_secret]
    conn = get_connection(pg_dsn)
    try:
        disable_for_migrations(conn)  # deletes across every account, like a migration
        cur = conn.cursor()
        cur.execute("DELETE FROM system_settings WHERE key = ANY(%s)", (secret_keys,))
        cur.execute("DELETE FROM user_settings WHERE key = ANY(%s)", (secret_keys,))
        conn.commit()
    finally:
        conn.close()


def _populate(pg_dsn: str) -> dict[str, int]:
    """Store one ciphertext of every kind under the CURRENT key, plus plaintext
    settings that must never be touched. Returns the ids used."""
    _clear_seeded_secrets(pg_dsn)
    repos = open_repositories(pg_dsn)
    try:
        user_id = PostgresUserAccountRepository(repos["conn"]).create_pending(
            "rot@example.com", "seed-rot", "2099-01-01"
        )["id"]
        set_current_user(repos["conn"].cursor(), user_id)
        repos["resume"].insert(
            user_id,
            {
                "filename": "r.pdf", "content": "text", "uploaded_at": "2026-01-01",
                "file_encrypted": crypto.encrypt_bytes(b"%PDF-resume"), "file_size": 11, "file_sha256": "x",
            },
        )
        repos["google_tokens"].upsert(user_id, crypto.encrypt_secret('{"refresh_token": "r"}'), "2026-01-01")
        repos["greenhouse_sessions"].upsert(user_id, crypto.encrypt_secret('{"cookies": []}'), "2026-01-01")
        set_setting("anthropic_api_key", "sk-user-secret", user_id, repos)
        set_setting("email_lookback_days", "10", user_id, repos)
        set_setting("google_oauth_client_config", '{"installed": {}}', None, repos)
        repos["conn"].commit()
        return {"user_id": user_id}
    finally:
        repos["conn"].close()


def _rotate(pg_dsn: str, *, dry_run: bool) -> Any:
    conn = get_connection(pg_dsn)
    try:
        disable_for_migrations(conn)  # rotate_all crosses every account, like a migration
        report = rotate_all(conn, dry_run=dry_run)
        conn.commit()
        return report
    finally:
        conn.close()


def test_rotate_all_reencrypts_every_kind_of_ciphertext_and_only_those(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = _make_key(), _make_key()
    _use_keys(monkeypatch, old)
    ids = _populate(pg_dsn)
    _use_keys(monkeypatch, new, old)

    preview = _rotate(pg_dsn, dry_run=True)
    assert preview.rotated == {
        "system settings": 1, "user settings": 1, "resume files": 1,
        "Google tokens": 1, "Greenhouse sessions": 1,
    }
    assert _rotate(pg_dsn, dry_run=True).total_rotated == 5  # the preview changed nothing

    applied = _rotate(pg_dsn, dry_run=False)
    assert applied.total_rotated == 5
    assert _rotate(pg_dsn, dry_run=True).total_rotated == 0

    _use_keys(monkeypatch, new)  # retire the old key: everything must still read
    repos = open_repositories(pg_dsn)
    try:
        set_current_user(repos["conn"].cursor(), ids["user_id"])
        assert get_setting("anthropic_api_key", ids["user_id"], repos) == "sk-user-secret"
        assert get_setting("google_oauth_client_config", None, repos) == '{"installed": {}}'
        assert get_setting("email_lookback_days", ids["user_id"], repos) == "10"
        assert repos["resume"].get_latest_file(ids["user_id"])["file_encrypted"] is not None
        assert crypto.decrypt_bytes(repos["resume"].get_latest_file(ids["user_id"])["file_encrypted"]) == b"%PDF-resume"
        assert crypto.decrypt_secret(repos["google_tokens"].get(ids["user_id"])["token_encrypted"])
        assert crypto.decrypt_secret(repos["greenhouse_sessions"].get(ids["user_id"])["state_encrypted"])
    finally:
        repos["conn"].close()


def test_an_unreadable_value_aborts_and_the_caller_can_roll_everything_back(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = _make_key(), _make_key()
    _use_keys(monkeypatch, old)
    ids = _populate(pg_dsn)
    stranger = Fernet(_make_key().encode()).encrypt(b'{"cookies": []}').decode()
    conn = get_connection(pg_dsn)
    try:
        set_current_user(conn.cursor(), ids["user_id"])
        PostgresGreenhouseSessionRepository(conn).upsert(ids["user_id"], stranger, "2026-01-01")
        conn.commit()
    finally:
        conn.close()
    _use_keys(monkeypatch, new, old)

    conn = get_connection(pg_dsn)
    try:
        disable_for_migrations(conn)  # rotate_all crosses every account, like a migration
        with pytest.raises(RotationError, match="Greenhouse sessions"):
            rotate_all(conn)
        conn.rollback()
    finally:
        conn.close()

    # Earlier tables were updated inside that transaction; the rollback undid them,
    # so this token is still under the OLD key rather than half-rotated.
    assert crypto.needs_rotation(_google_token(pg_dsn, ids["user_id"])) is True


def _google_token(pg_dsn: str, user_id: int) -> str:
    conn = get_connection(pg_dsn)
    try:
        set_current_user(conn.cursor(), user_id)
        return PostgresGoogleTokenRepository(conn).get(user_id)["token_encrypted"]
    finally:
        conn.close()


def test_a_resume_row_without_a_stored_file_is_skipped(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_keys(monkeypatch, _make_key())
    _clear_seeded_secrets(pg_dsn)
    conn = get_connection(pg_dsn)
    try:
        user_id = PostgresUserAccountRepository(conn).create_pending("nofile@example.com", "s", "2099-01-01")["id"]
        set_current_user(conn.cursor(), user_id)
        PostgresResumeRepository(conn).insert(user_id, {"filename": "a.pdf", "content": "t", "uploaded_at": "2026-01-01"})
        conn.commit()
        disable_for_migrations(conn)  # rotate_all crosses every account, like a migration
        report = rotate_all(conn, dry_run=True)
    finally:
        conn.close()
    assert report.scanned["resume files"] == 0


# ─── the management CLI ─────────────────────────────────────────────────────────


@pytest.fixture
def manage_secrets() -> Any:
    spec = importlib.util.spec_from_file_location("manage_secrets", REPO_ROOT / "scripts" / "manage_secrets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generate_prints_a_valid_secret_and_a_valid_key(manage_secrets: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert manage_secrets.main(["generate"]) == 0

    printed = dict(line.split("=", 1) for line in capsys.readouterr().out.strip().splitlines())
    assert flask_secret_problem(printed[FLASK_SECRET_ENV]) is None
    assert encryption_key_problem(printed[ENCRYPTION_KEY_ENV]) is None


def test_flask_rotation_previews_by_default_and_writes_only_when_asked(
    manage_secrets: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current = generate_flask_secret()
    monkeypatch.setenv(FLASK_SECRET_ENV, current)
    monkeypatch.setenv(FLASK_SECRET_FALLBACKS_ENV, "")
    env_file = tmp_path / ".env"

    assert manage_secrets.main(["--env-file", str(env_file), "rotate-flask-secret"]) == 0
    assert not env_file.exists()

    assert manage_secrets.main(["--env-file", str(env_file), "rotate-flask-secret", "--write"]) == 0
    written = dotenv_values(env_file)
    assert written[FLASK_SECRET_ENV] != current and flask_secret_problem(written[FLASK_SECRET_ENV]) is None
    assert written[FLASK_SECRET_FALLBACKS_ENV] == current


def test_the_cli_reports_a_missing_value_instead_of_inventing_one(
    manage_secrets: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(FLASK_SECRET_ENV)
    assert manage_secrets.main(["--env-file", str(tmp_path / ".env"), "rotate-flask-secret", "--write"]) == 1
    assert "nothing to rotate" in capsys.readouterr().err


def test_encryption_rotation_end_to_end(
    manage_secrets: Any, pg_dsn: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    old = _make_key()
    _use_keys(monkeypatch, old)
    monkeypatch.setattr(config, "DATABASE_URL", pg_dsn)
    ids = _populate(pg_dsn)
    env_file = tmp_path / ".env"

    # Preview: nothing on disk or in the database changes.
    assert manage_secrets.main(["--env-file", str(env_file), "rotate-encryption-key"]) == 0
    assert not env_file.exists()
    _use_keys(monkeypatch, old)
    assert manage_secrets.main(["--env-file", str(env_file), "check-encryption"]) == 0

    _use_keys(monkeypatch, old)
    assert manage_secrets.main(["--env-file", str(env_file), "rotate-encryption-key", "--write"]) == 0

    written = dotenv_values(env_file)
    new = written[ENCRYPTION_KEY_ENV]
    assert new != old and written[ENCRYPTION_KEY_FALLBACKS_ENV] == old
    _use_keys(monkeypatch, new, old)
    assert manage_secrets.main(["--env-file", str(env_file), "check-encryption"]) == 0
    _use_keys(monkeypatch, new)  # old key retired: still readable
    repos = open_repositories(pg_dsn)
    try:
        set_current_user(repos["conn"].cursor(), ids["user_id"])
        assert get_setting("anthropic_api_key", ids["user_id"], repos) == "sk-user-secret"
    finally:
        repos["conn"].close()


def test_check_encryption_reports_values_still_under_an_old_key(
    manage_secrets: Any, pg_dsn: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    old, new = _make_key(), _make_key()
    _use_keys(monkeypatch, old)
    monkeypatch.setattr(config, "DATABASE_URL", pg_dsn)
    _populate(pg_dsn)
    _use_keys(monkeypatch, new, old)

    assert manage_secrets.main(["--env-file", str(tmp_path / ".env"), "check-encryption"]) == 1
