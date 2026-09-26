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

"""Which environment values change when a secret is rotated.

Rotation never discards the old value: the new key becomes the primary and the
old one is prepended to the matching *_FALLBACKS list, so everything already
issued or stored keeps working until it is re-issued / re-encrypted and the
fallback is retired."""

from __future__ import annotations

from collections.abc import Mapping

from backend.security.secrets_policy import (
    ENCRYPTION_KEY_ENV,
    ENCRYPTION_KEY_FALLBACKS_ENV,
    FLASK_SECRET_ENV,
    FLASK_SECRET_FALLBACKS_ENV,
    encryption_key_problem,
    generate_encryption_key,
    generate_flask_secret,
    split_key_list,
)


def _with_old_first(old: str, existing_fallbacks: tuple[str, ...]) -> str:
    ordered: list[str] = []
    for value in (old, *existing_fallbacks):
        if value and value not in ordered:
            ordered.append(value)
    return ",".join(ordered)


# ai: claude-sonnet-5 | 2026-09-26
def plan_flask_secret_rotation(env: Mapping[str, str]) -> dict[str, str]:
    """New signing secret, with the current one kept as a fallback so existing
    sessions stay valid. The current secret may itself be too weak to start with
    (that is often why it is being rotated), so it is only required to exist."""
    current = env.get(FLASK_SECRET_ENV, "").strip()
    if not current:
        raise ValueError(f"{FLASK_SECRET_ENV} is not set; there is nothing to rotate.")
    return {
        FLASK_SECRET_ENV: generate_flask_secret(),
        FLASK_SECRET_FALLBACKS_ENV: _with_old_first(
            current, split_key_list(env.get(FLASK_SECRET_FALLBACKS_ENV, ""))
        ),
    }


# ai: claude-sonnet-5 | 2026-09-26
def plan_encryption_key_rotation(env: Mapping[str, str]) -> dict[str, str]:
    """New encryption key, with the current one kept as a fallback so stored
    values stay readable until they have been re-encrypted."""
    current = env.get(ENCRYPTION_KEY_ENV, "").strip()
    problem = encryption_key_problem(current)
    if problem:
        raise ValueError(f"{ENCRYPTION_KEY_ENV} {problem}; it cannot be rotated.")
    return {
        ENCRYPTION_KEY_ENV: generate_encryption_key(),
        ENCRYPTION_KEY_FALLBACKS_ENV: _with_old_first(
            current, split_key_list(env.get(ENCRYPTION_KEY_FALLBACKS_ENV, ""))
        ),
    }
