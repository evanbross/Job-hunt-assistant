# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-08
"""backend/secret_files.py: NAME_FILE delivers a secret as a file (cloud plan 4.2)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.db.backup import BackupSettings
from backend.db.repositories.user_accounts import BOOTSTRAP_ADMIN_PASSWORD_ENV
from backend.secret_files import SECRET_ENV_VARS, SecretFileError, load_secret_files
from backend.security import secrets_policy

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _secret(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "secret"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_file_becomes_the_variable_and_the_file_setting_is_removed(tmp_path: Path) -> None:
    environ = {"FLASK_SECRET_KEY_FILE": str(_secret(tmp_path, "s3cret-value\n"))}

    assert load_secret_files(environ) == ["FLASK_SECRET_KEY"]
    assert environ == {"FLASK_SECRET_KEY": "s3cret-value"}


def test_two_sources_for_one_secret_are_refused(tmp_path: Path) -> None:
    environ = {"DATABASE_URL": "postgresql://a", "DATABASE_URL_FILE": str(_secret(tmp_path, "postgresql://b"))}

    with pytest.raises(SecretFileError, match="Both DATABASE_URL and DATABASE_URL_FILE"):
        load_secret_files(environ)


@pytest.mark.parametrize("contents", [None, "", "  \n"], ids=["missing", "empty", "blank"])
def test_an_unusable_file_fails_without_showing_its_contents(tmp_path: Path, contents: str | None) -> None:
    path = tmp_path / "secret" if contents is None else _secret(tmp_path, contents)

    with pytest.raises(SecretFileError, match="SETTINGS_ENCRYPTION_KEY_FILE") as raised:
        load_secret_files({"SETTINGS_ENCRYPTION_KEY_FILE": str(path)})
    assert str(path) in str(raised.value)


def test_file_paths_that_are_not_secrets_are_left_alone() -> None:
    environ = {"REDIS_TLS_CA_FILE": "/tls/ca.crt", "OPENAI_API_KEY_FILE": ""}

    assert load_secret_files(environ) == []
    assert environ == {"REDIS_TLS_CA_FILE": "/tls/ca.crt"}


def test_every_secret_other_modules_name_can_come_from_a_file() -> None:
    named_elsewhere = {
        secrets_policy.FLASK_SECRET_ENV,
        secrets_policy.FLASK_SECRET_FALLBACKS_ENV,
        secrets_policy.ENCRYPTION_KEY_ENV,
        secrets_policy.ENCRYPTION_KEY_FALLBACKS_ENV,
        BOOTSTRAP_ADMIN_PASSWORD_ENV,
        *(f"BACKUP_{field.upper()}" for field in BackupSettings.model_fields if "access_key" in field),
    }

    assert named_elsewhere <= set(SECRET_ENV_VARS)


def test_a_backup_secret_from_a_file_passes_the_strict_backup_settings(tmp_path: Path) -> None:
    environ = {
        "BACKUP_S3_BUCKET": "b",
        "BACKUP_S3_ACCESS_KEY_ID": "id",
        "BACKUP_S3_SECRET_ACCESS_KEY_FILE": str(_secret(tmp_path, "from-a-file")),
    }

    load_secret_files(environ)

    settings = BackupSettings.from_env(environ)
    assert settings is not None and settings.s3_secret_access_key == "from-a-file"


def test_config_loads_secret_files_before_reading_settings(tmp_path: Path) -> None:
    """The real process start: importing config in a fresh interpreter."""
    env = {**os.environ, "BOOTSTRAP_ADMIN_PASSWORD_FILE": str(_secret(tmp_path, "from-the-file\n"))}
    env.pop("BOOTSTRAP_ADMIN_PASSWORD", None)
    probe = (
        "import os, config; "
        "print(os.environ['BOOTSTRAP_ADMIN_PASSWORD'], 'BOOTSTRAP_ADMIN_PASSWORD_FILE' in os.environ)"
    )

    result = subprocess.run(  # noqa: S603 - fixed interpreter and code
        [sys.executable, "-c", probe], cwd=_REPO_ROOT, env=env, capture_output=True, text=True, check=True
    )

    assert result.stdout.split() == ["from-the-file", "False"]
