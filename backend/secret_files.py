# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-08
"""Read secrets from files: the `*_FILE` convention (docs/cloud-deployment-readiness-plan.md 4.2).

For each secret below, `NAME_FILE=/run/secrets/name` means "NAME is the contents of that
file". Docker and Kubernetes secrets, and most secrets-store agents, deliver secrets as
files like this, which keeps them out of the process environment that `docker inspect`,
`/proc/<pid>/environ` and crash reports show.

config.py calls load_secret_files(os.environ) once, before anything reads a setting, so
every later reader (config's own constants, backend/crypto.py, BackupSettings.from_env)
sees an ordinary NAME variable and needs no file handling of its own.

Only the names listed here are read. `*_FILE` is not a general rule because some
settings are file *paths* by design (REDIS_TLS_CA_FILE names a certificate the TLS
client opens itself).

This module is a leaf: it imports nothing from the project, so config.py can use it.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path

FILE_SUFFIX = "_FILE"

# Every environment variable that holds a secret. tests/test_secret_files.py checks
# this covers the names other modules define as constants.
SECRET_ENV_VARS: tuple[str, ...] = (
    "FLASK_SECRET_KEY",
    "FLASK_SECRET_KEY_FALLBACKS",
    "SETTINGS_ENCRYPTION_KEY",
    "SETTINGS_ENCRYPTION_KEY_FALLBACKS",
    "DATABASE_URL",
    "DATABASE_OWNER_URL",
    "POSTGRES_PASSWORD",
    "POSTGRES_APP_PASSWORD",
    "READONLY_DB_PASSWORD",
    "REDIS_URL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "BOOTSTRAP_ADMIN_PASSWORD",
    "BACKUP_S3_ACCESS_KEY_ID",
    "BACKUP_S3_SECRET_ACCESS_KEY",
)


class SecretFileError(RuntimeError):
    """A NAME_FILE setting can't be used. The message names the setting and the file,
    never the file's contents."""


def load_secret_files(environ: MutableMapping[str, str]) -> list[str]:
    """For each NAME_FILE set in `environ`, set NAME to that file's contents (surrounding
    whitespace removed, so a trailing newline from `echo` doesn't matter) and remove
    NAME_FILE. Returns the names loaded. Raises SecretFileError if NAME is also set
    (two sources for one secret is ambiguous), or the file is missing, unreadable or
    empty -- failing at startup, not on first use."""
    loaded: list[str] = []
    for name in SECRET_ENV_VARS:
        file_var = f"{name}{FILE_SUFFIX}"
        path_text = environ.get(file_var, "").strip()
        if not path_text:
            environ.pop(file_var, None)
            continue
        if environ.get(name, "").strip():
            raise SecretFileError(
                f"Both {name} and {file_var} are set. Set only one: {file_var} for a secret"
                f" delivered as a file, or {name} itself."
            )
        path = Path(path_text)
        try:
            value = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise SecretFileError(f"{file_var}: cannot read {path} ({exc.__class__.__name__}).") from exc
        if not value:
            raise SecretFileError(f"{file_var}: {path} is empty.")
        environ[name] = value
        # Removed so code that reads a whole prefix (BACKUP_* with extra="forbid")
        # never sees it as an unknown setting.
        del environ[file_var]
        loaded.append(name)
    return loaded
