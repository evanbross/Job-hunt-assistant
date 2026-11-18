# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-09
"""One-time account links: registration, email change and password reset.

Shared by the account routes (routes/session_auth.py) and the admin routes that resend
a registration link (routes/admin_accounts.py), so a resent link is the same kind of
token, with the same lifetime and the same URL, as the first one."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

REGISTRATION_TOKEN_LIFETIME = timedelta(hours=24)
EMAIL_CHANGE_TOKEN_LIFETIME = timedelta(hours=1)
PASSWORD_RESET_TOKEN_LIFETIME = timedelta(hours=1)


# ai: claude-opus-5-5 | 2026-11-18 -- 2026-11-18 full code review: these are bearer
# credentials (a reset link is an account takeover for its hour), so only a digest is
# stored -- a database snapshot or dump no longer hands out working links. A fast hash is
# enough: the tokens are 256-bit random values, not guessable passwords.
def hash_account_token(token: str) -> str:
    """The digest user_accounts.verification_token_hash stores for *token*. Must stay
    identical to the SQL backfill in backend/db/schema.py that hashed existing tokens:
    hex SHA-256 of the UTF-8 bytes."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token(lifetime: timedelta) -> tuple[str, str]:
    """A fresh unguessable token and its expiry (ISO 8601, UTC)."""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(UTC) + lifetime).isoformat()
    return token, expires_at


def registration_link(app_base_url: str, token: str) -> str:
    """The page that completes a registration (frontend VerifyAccountPage)."""
    return f"{app_base_url}/verify-account?token={token}"
