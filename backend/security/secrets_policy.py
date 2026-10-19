# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""What counts as an acceptable bootstrap secret, and how to generate one.

Two secrets can never live in the database they protect: FLASK_SECRET_KEY
(signs session cookies) and SETTINGS_ENCRYPTION_KEY (encrypts stored tokens,
API keys, and uploaded documents). Both are supplied through the environment,
so a missing, copied-from-the-example, or weak value has to be caught before
the app starts serving rather than silently signing sessions with a value that
anyone who has read the repository already knows."""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.fernet import Fernet

FLASK_SECRET_ENV = "FLASK_SECRET_KEY"
FLASK_SECRET_FALLBACKS_ENV = "FLASK_SECRET_KEY_FALLBACKS"
ENCRYPTION_KEY_ENV = "SETTINGS_ENCRYPTION_KEY"
ENCRYPTION_KEY_FALLBACKS_ENV = "SETTINGS_ENCRYPTION_KEY_FALLBACKS"

MIN_FLASK_SECRET_LENGTH = 32
_MIN_DISTINCT_CHARACTERS = 10

# Values that ship in the repository's examples/docs or that people reach for
# by default. Compared case-insensitively.
_KNOWN_PLACEHOLDERS = frozenset(
    {
        "",
        "change-this-in-production",
        "change-me",
        "changeme",
        "generate-with-the-command-above",
        "secret",
        "secret-key",
        "your-secret-key",
        "your-secret-here",
        "dev",
        "development",
        "test",
    }
)
# "<random 32+ char string>", "[your key]" and similar fill-in-the-blank text.
_TEMPLATE_PATTERN = re.compile(r"^[<\[{].*[>\]}]$")


class InsecureSecretError(RuntimeError):
    """One or more startup secrets are missing or unacceptable. The message
    lists every problem and how to fix it."""


@dataclass(frozen=True)
class SecretsConfig:
    flask_secret: str
    flask_fallbacks: tuple[str, ...]
    encryption_key: str
    encryption_fallbacks: tuple[str, ...]


# ai: claude-sonnet-5 | 2026-09-26
def generate_flask_secret() -> str:
    return secrets.token_hex(32)


# ai: claude-sonnet-5 | 2026-09-26
def generate_encryption_key() -> str:
    return Fernet.generate_key().decode("utf-8")


# ai: claude-sonnet-5 | 2026-09-26
def is_placeholder(value: str) -> bool:
    stripped = value.strip()
    return stripped.lower() in _KNOWN_PLACEHOLDERS or bool(_TEMPLATE_PATTERN.match(stripped))


# ai: claude-sonnet-5 | 2026-09-26
def flask_secret_problem(value: str) -> str | None:
    """Why *value* is unacceptable as a session-signing secret, or None if fine."""
    if is_placeholder(value):
        return "is empty or a placeholder value copied from an example"
    if value != value.strip():
        return "has leading or trailing whitespace"
    if len(value) < MIN_FLASK_SECRET_LENGTH:
        return f"is {len(value)} characters; at least {MIN_FLASK_SECRET_LENGTH} are required"
    if len(set(value)) < _MIN_DISTINCT_CHARACTERS:
        return "is too repetitive to be a random secret"
    return None


# ai: claude-sonnet-5 | 2026-09-26
def encryption_key_problem(value: str) -> str | None:
    """Why *value* is unacceptable as a Fernet encryption key, or None if fine."""
    if is_placeholder(value):
        return "is empty or a placeholder value copied from an example"
    try:
        Fernet(value.strip().encode("utf-8"))
    except (ValueError, TypeError):
        return "is not a valid Fernet key (32 url-safe base64-encoded bytes)"
    return None


# ai: claude-sonnet-5 | 2026-09-26
def split_key_list(raw: str) -> tuple[str, ...]:
    """Parse a comma-separated list of old keys, ignoring blanks."""
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _flask_secret_problems(env: Mapping[str, str]) -> list[str]:
    problems: list[str] = []
    flask_secret = env.get(FLASK_SECRET_ENV, "")
    problem = flask_secret_problem(flask_secret)
    if problem:
        problems.append(f"{FLASK_SECRET_ENV} {problem}.")
    fallbacks = split_key_list(env.get(FLASK_SECRET_FALLBACKS_ENV, ""))
    for old_secret in fallbacks:
        problem = flask_secret_problem(old_secret)
        if problem:
            problems.append(f"An entry in {FLASK_SECRET_FALLBACKS_ENV} {problem}.")
    if flask_secret in fallbacks:
        problems.append(f"{FLASK_SECRET_FALLBACKS_ENV} must not repeat the current {FLASK_SECRET_ENV}.")
    return problems


def _encryption_key_problems(env: Mapping[str, str]) -> list[str]:
    problems: list[str] = []
    encryption_key = env.get(ENCRYPTION_KEY_ENV, "").strip()
    problem = encryption_key_problem(encryption_key)
    if problem:
        problems.append(f"{ENCRYPTION_KEY_ENV} {problem}.")
    fallbacks = split_key_list(env.get(ENCRYPTION_KEY_FALLBACKS_ENV, ""))
    for old_key in fallbacks:
        problem = encryption_key_problem(old_key)
        if problem:
            problems.append(f"An entry in {ENCRYPTION_KEY_FALLBACKS_ENV} {problem}.")
    if encryption_key in fallbacks:
        problems.append(
            f"{ENCRYPTION_KEY_FALLBACKS_ENV} must not repeat the current {ENCRYPTION_KEY_ENV}."
        )
    return problems


# ai: claude-sonnet-5 | 2026-09-26
def validate_encryption_config(env: Mapping[str, str]) -> None:
    """Validate only the encryption key(s); for processes such as the Celery
    worker that decrypt stored secrets but never sign sessions."""
    problems = _encryption_key_problems(env)
    if problems:
        raise InsecureSecretError(_format_failure(problems))


# ai: claude-sonnet-5 | 2026-09-26
def validate_secrets(env: Mapping[str, str]) -> SecretsConfig:
    """Validate every bootstrap secret in *env* (an environment mapping) and
    return them parsed, or raise InsecureSecretError describing all problems."""
    problems = _flask_secret_problems(env) + _encryption_key_problems(env)
    if problems:
        raise InsecureSecretError(_format_failure(problems))
    return SecretsConfig(
        flask_secret=env.get(FLASK_SECRET_ENV, ""),
        flask_fallbacks=split_key_list(env.get(FLASK_SECRET_FALLBACKS_ENV, "")),
        encryption_key=env.get(ENCRYPTION_KEY_ENV, "").strip(),
        encryption_fallbacks=split_key_list(env.get(ENCRYPTION_KEY_FALLBACKS_ENV, "")),
    )


def _format_failure(problems: list[str]) -> str:
    bullet_list = "\n".join(f"  - {problem}" for problem in problems)
    return (
        "Refusing to start: insecure or missing secrets.\n"
        f"{bullet_list}\n\n"
        "Generate replacements with:\n"
        "  python scripts/manage_secrets.py generate\n"
        "and put them in the environment (or .env). To replace a value that is already\n"
        "in use without invalidating sessions or stored data, see docs/secrets-and-key-rotation.md.\n"
        "For local development only, set APP_ENV=development and the app will generate\n"
        f"a missing {FLASK_SECRET_ENV} for you."
    )
