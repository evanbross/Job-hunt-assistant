# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Startup enforcement of the bootstrap secrets.

Production (the default) refuses to start on a missing or weak secret. An
explicit APP_ENV=development additionally lets a *missing* FLASK_SECRET_KEY be
generated once and saved to .env, so a fresh local checkout works without
hand-editing. Only that key is ever auto-generated: the encryption key
protects data that already exists, and silently minting a new one would make
everything encrypted under the lost one unreadable, so it must be supplied
deliberately."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from pathlib import Path

from dotenv import set_key

from backend.security.secrets_policy import (
    FLASK_SECRET_ENV,
    InsecureSecretError,
    SecretsConfig,
    generate_flask_secret,
    is_placeholder,
    validate_secrets,
)

logger = logging.getLogger(__name__)

APP_ENV_PRODUCTION = "production"
APP_ENV_DEVELOPMENT = "development"
_VALID_APP_ENVS = frozenset({APP_ENV_PRODUCTION, APP_ENV_DEVELOPMENT})


# ai: claude-sonnet-5 | 2026-09-26
def normalize_app_env(raw: str) -> str:
    """APP_ENV defaults to production; anything but the two known values is an
    error rather than being silently treated as one of them."""
    value = raw.strip().lower() or APP_ENV_PRODUCTION
    if value not in _VALID_APP_ENVS:
        raise InsecureSecretError(
            f"APP_ENV must be '{APP_ENV_PRODUCTION}' or '{APP_ENV_DEVELOPMENT}', not {raw!r}."
        )
    return value


# ai: claude-sonnet-5 | 2026-09-26
def enforce_startup_secrets(
    env: MutableMapping[str, str], *, app_env: str, env_file: Path
) -> SecretsConfig:
    """Validate the bootstrap secrets in *env* and return them.

    In development, a missing/placeholder FLASK_SECRET_KEY is generated, written
    to *env_file*, and placed in *env* before validating. Raises
    InsecureSecretError describing every problem otherwise."""
    if app_env == APP_ENV_DEVELOPMENT and is_placeholder(env.get(FLASK_SECRET_ENV, "")):
        generated = generate_flask_secret()
        env_file.touch(exist_ok=True)
        set_key(str(env_file), FLASK_SECRET_ENV, generated)
        env[FLASK_SECRET_ENV] = generated
        logger.warning(
            "APP_ENV=development: generated a new %s and saved it to %s.",
            FLASK_SECRET_ENV,
            env_file,
        )
    return validate_secrets(env)
