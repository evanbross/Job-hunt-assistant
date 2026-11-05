# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

import logging
import secrets
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from dotenv import dotenv_values

import backend.crypto as crypto
import config
from backend.celery_app import _require_valid_encryption_key
from backend.security.rotation_plan import (
    plan_encryption_key_rotation,
    plan_flask_secret_rotation,
)
from backend.security.secrets_policy import (
    ENCRYPTION_KEY_ENV,
    ENCRYPTION_KEY_FALLBACKS_ENV,
    FLASK_SECRET_ENV,
    FLASK_SECRET_FALLBACKS_ENV,
    InsecureSecretError,
    encryption_key_problem,
    flask_secret_problem,
    generate_encryption_key,
    generate_flask_secret,
    validate_encryption_config,
    validate_secrets,
)
from backend.security.startup import (
    APP_ENV_DEVELOPMENT,
    APP_ENV_PRODUCTION,
    enforce_startup_secrets,
    normalize_app_env,
)
from web import create_app
from web.serving import install_startup_secrets

# ai: claude-sonnet-5 | 2026-09-26


def good_env(**overrides: str) -> dict[str, str]:
    env = {
        FLASK_SECRET_ENV: generate_flask_secret(),
        ENCRYPTION_KEY_ENV: generate_encryption_key(),
    }
    env.update(overrides)
    return env


# ─── what counts as an acceptable secret ────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "change-this-in-production",
        "CHANGE-THIS-IN-PRODUCTION",
        "changeme",
        "generate-with-the-command-above",
        "<random 32+ char string>",
        "[your secret]",
    ],
)
def test_placeholder_flask_secrets_are_rejected(value: str) -> None:
    assert "placeholder" in (flask_secret_problem(value) or "")


def test_a_short_secret_is_rejected_with_its_length() -> None:
    assert "28 characters" in flask_secret_problem("x1y2z3" * 4 + "abcd")


@pytest.mark.parametrize("value", ["a" * 40, "ab" * 20, "0123" * 10])
def test_repetitive_secrets_are_rejected(value: str) -> None:
    assert "repetitive" in flask_secret_problem(value)


def test_surrounding_whitespace_is_rejected() -> None:
    assert "whitespace" in flask_secret_problem(" " + generate_flask_secret())


def test_a_generated_secret_is_accepted() -> None:
    assert flask_secret_problem(generate_flask_secret()) is None
    assert flask_secret_problem(secrets.token_urlsafe(32)) is None


@pytest.mark.parametrize("value", ["", "generate-with-the-command-above", "not-base64", "abc"])
def test_bad_encryption_keys_are_rejected(value: str) -> None:
    assert encryption_key_problem(value) is not None


def test_a_generated_encryption_key_is_accepted() -> None:
    assert encryption_key_problem(generate_encryption_key()) is None


def test_every_problem_is_reported_at_once() -> None:
    with pytest.raises(InsecureSecretError) as excinfo:
        validate_secrets({FLASK_SECRET_ENV: "change-this-in-production", ENCRYPTION_KEY_ENV: ""})

    message = str(excinfo.value)
    assert FLASK_SECRET_ENV in message and ENCRYPTION_KEY_ENV in message
    assert "manage_secrets.py generate" in message
    assert "APP_ENV=development" in message


def test_valid_secrets_are_returned_parsed() -> None:
    old_secret, old_key = generate_flask_secret(), generate_encryption_key()
    env = good_env(
        **{
            FLASK_SECRET_FALLBACKS_ENV: f" {old_secret} , ",
            ENCRYPTION_KEY_FALLBACKS_ENV: old_key,
        }
    )

    parsed = validate_secrets(env)

    assert parsed.flask_secret == env[FLASK_SECRET_ENV]
    assert parsed.flask_fallbacks == (old_secret,)
    assert parsed.encryption_key == env[ENCRYPTION_KEY_ENV]
    assert parsed.encryption_fallbacks == (old_key,)


def test_fallbacks_are_validated_and_may_not_repeat_the_current_value() -> None:
    env = good_env()
    with pytest.raises(InsecureSecretError, match="must not repeat"):
        validate_secrets({**env, FLASK_SECRET_FALLBACKS_ENV: env[FLASK_SECRET_ENV]})
    with pytest.raises(InsecureSecretError, match="An entry in SETTINGS_ENCRYPTION_KEY_FALLBACKS"):
        validate_secrets({**env, ENCRYPTION_KEY_FALLBACKS_ENV: "garbage"})


def test_the_worker_check_only_looks_at_the_encryption_key() -> None:
    validate_encryption_config({ENCRYPTION_KEY_ENV: generate_encryption_key()})
    with pytest.raises(InsecureSecretError, match=ENCRYPTION_KEY_ENV):
        validate_encryption_config({FLASK_SECRET_ENV: generate_flask_secret()})


def test_the_celery_worker_refuses_to_start_without_a_valid_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENCRYPTION_KEY_ENV, generate_encryption_key())
    _require_valid_encryption_key()

    monkeypatch.setenv(ENCRYPTION_KEY_ENV, "generate-with-the-command-above")
    with pytest.raises(SystemExit):
        _require_valid_encryption_key()


# ─── startup enforcement ────────────────────────────────────────────────────────


def test_app_env_defaults_to_production_and_rejects_unknown_values() -> None:
    assert normalize_app_env("") == APP_ENV_PRODUCTION
    assert normalize_app_env(" Development ") == APP_ENV_DEVELOPMENT
    with pytest.raises(InsecureSecretError, match="APP_ENV"):
        normalize_app_env("staging")


def test_production_refuses_a_missing_secret_and_writes_nothing(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env = {ENCRYPTION_KEY_ENV: generate_encryption_key()}

    with pytest.raises(InsecureSecretError, match=FLASK_SECRET_ENV):
        enforce_startup_secrets(env, app_env=APP_ENV_PRODUCTION, env_file=env_file)

    assert not env_file.exists() and FLASK_SECRET_ENV not in env


def test_development_generates_and_saves_a_missing_signing_secret(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env = {ENCRYPTION_KEY_ENV: generate_encryption_key(), FLASK_SECRET_ENV: "change-this-in-production"}

    result = enforce_startup_secrets(env, app_env=APP_ENV_DEVELOPMENT, env_file=env_file)

    assert flask_secret_problem(result.flask_secret) is None
    assert env[FLASK_SECRET_ENV] == result.flask_secret
    assert dotenv_values(env_file)[FLASK_SECRET_ENV] == result.flask_secret


def test_development_keeps_a_valid_secret_and_never_overwrites_a_weak_one(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    valid = good_env()
    kept = enforce_startup_secrets(dict(valid), app_env=APP_ENV_DEVELOPMENT, env_file=env_file)
    assert kept.flask_secret == valid[FLASK_SECRET_ENV] and not env_file.exists()

    weak = {**valid, FLASK_SECRET_ENV: "short-but-deliberate-value"}
    with pytest.raises(InsecureSecretError, match="characters"):
        enforce_startup_secrets(weak, app_env=APP_ENV_DEVELOPMENT, env_file=env_file)
    assert weak[FLASK_SECRET_ENV] == "short-but-deliberate-value"


def test_the_encryption_key_is_never_auto_generated(tmp_path: Path) -> None:
    """Minting a new one would orphan everything encrypted under the lost one."""
    env = {FLASK_SECRET_ENV: generate_flask_secret()}

    with pytest.raises(InsecureSecretError, match=ENCRYPTION_KEY_ENV):
        enforce_startup_secrets(env, app_env=APP_ENV_DEVELOPMENT, env_file=tmp_path / ".env")

    assert ENCRYPTION_KEY_ENV not in env


def test_the_app_installs_validated_secrets_and_fallbacks(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    new_secret, old_secret = generate_flask_secret(), generate_flask_secret()
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setenv(FLASK_SECRET_ENV, new_secret)
    monkeypatch.setenv(FLASK_SECRET_FALLBACKS_ENV, old_secret)
    monkeypatch.setenv(ENCRYPTION_KEY_ENV, generate_encryption_key())

    app = create_app()

    assert install_startup_secrets(app) == "production"

    assert app.secret_key == new_secret
    assert app.config["SECRET_KEY_FALLBACKS"] == [old_secret]


def test_the_app_refuses_to_start_on_a_placeholder_secret(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setenv(FLASK_SECRET_ENV, "change-this-in-production")
    monkeypatch.setenv(ENCRYPTION_KEY_ENV, generate_encryption_key())

    with caplog.at_level(logging.CRITICAL), pytest.raises(SystemExit) as excinfo:
        install_startup_secrets(create_app())

    assert excinfo.value.code == 1
    assert "Refusing to start" in caplog.text


# ─── key rotation: the plan ─────────────────────────────────────────────────────


def test_flask_rotation_makes_the_new_secret_primary_and_keeps_the_old_one() -> None:
    older = generate_flask_secret()
    env = good_env(**{FLASK_SECRET_FALLBACKS_ENV: older})
    current = env[FLASK_SECRET_ENV]

    plan = plan_flask_secret_rotation(env)

    assert flask_secret_problem(plan[FLASK_SECRET_ENV]) is None and plan[FLASK_SECRET_ENV] != current
    assert plan[FLASK_SECRET_FALLBACKS_ENV].split(",") == [current, older]


def test_flask_rotation_accepts_a_weak_current_secret_since_that_is_why_it_is_rotated() -> None:
    plan = plan_flask_secret_rotation({FLASK_SECRET_ENV: "only-28-characters-long-abcd"})
    assert plan[FLASK_SECRET_FALLBACKS_ENV] == "only-28-characters-long-abcd"


def test_rotation_never_lists_a_value_twice() -> None:
    env = good_env()
    once = plan_flask_secret_rotation({**env, FLASK_SECRET_FALLBACKS_ENV: env[FLASK_SECRET_ENV]})
    assert once[FLASK_SECRET_FALLBACKS_ENV] == env[FLASK_SECRET_ENV]


def test_encryption_rotation_plan_is_valid_and_keeps_the_old_key() -> None:
    env = good_env()

    plan = plan_encryption_key_rotation(env)

    assert encryption_key_problem(plan[ENCRYPTION_KEY_ENV]) is None
    assert plan[ENCRYPTION_KEY_FALLBACKS_ENV] == env[ENCRYPTION_KEY_ENV]
    validate_secrets({**env, **plan})


@pytest.mark.parametrize("bad", [{}, {ENCRYPTION_KEY_ENV: "not-a-key"}])
def test_nothing_can_be_rotated_without_a_current_value(bad: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        plan_encryption_key_rotation(bad)
    with pytest.raises(ValueError):
        plan_flask_secret_rotation({})


# ─── key rotation: multi-key decryption ─────────────────────────────────────────


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """Start with an old key as the only key; returns (old, new)."""
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv(ENCRYPTION_KEY_ENV, old)
    monkeypatch.setenv(ENCRYPTION_KEY_FALLBACKS_ENV, "")
    monkeypatch.setattr(crypto, "_fernet", None)
    return old, new


def _use_keys(monkeypatch: pytest.MonkeyPatch, primary: str, fallbacks: str = "") -> None:
    monkeypatch.setenv(ENCRYPTION_KEY_ENV, primary)
    monkeypatch.setenv(ENCRYPTION_KEY_FALLBACKS_ENV, fallbacks)
    monkeypatch.setattr(crypto, "_fernet", None)


def test_values_encrypted_under_a_fallback_key_stay_readable(
    keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = keys
    token = crypto.encrypt_secret("api-key-value")

    _use_keys(monkeypatch, new, old)

    assert crypto.decrypt_secret(token) == "api-key-value"
    assert crypto.needs_rotation(token) is True


def test_new_values_use_the_primary_key_only(keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    old, new = keys
    _use_keys(monkeypatch, new, old)
    token = crypto.encrypt_secret("fresh")

    assert crypto.needs_rotation(token) is False
    _use_keys(monkeypatch, new)  # old key retired
    assert crypto.decrypt_secret(token) == "fresh"


def test_reencrypting_moves_a_value_to_the_primary_key(
    keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = keys
    token = crypto.encrypt_secret("secret")
    _use_keys(monkeypatch, new, old)

    moved = crypto.reencrypt(token)

    assert crypto.needs_rotation(moved) is False
    _use_keys(monkeypatch, new)
    assert crypto.decrypt_secret(moved) == "secret"
    with pytest.raises(ValueError, match="Could not decrypt"):
        crypto.decrypt_secret(token)


def test_a_value_no_configured_key_can_read_is_reported_loudly(
    keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = keys
    token = crypto.encrypt_secret("secret")
    _use_keys(monkeypatch, new)  # forgot to keep the old key as a fallback

    with pytest.raises(ValueError, match="Could not decrypt"):
        crypto.needs_rotation(token)
