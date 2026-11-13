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

import secrets
from datetime import UTC, datetime, timedelta

REGISTRATION_TOKEN_LIFETIME = timedelta(hours=24)
EMAIL_CHANGE_TOKEN_LIFETIME = timedelta(hours=1)
PASSWORD_RESET_TOKEN_LIFETIME = timedelta(hours=1)


def new_token(lifetime: timedelta) -> tuple[str, str]:
    """A fresh unguessable token and its expiry (ISO 8601, UTC)."""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(UTC) + lifetime).isoformat()
    return token, expires_at


def registration_link(app_base_url: str, token: str) -> str:
    """The page that completes a registration (frontend VerifyAccountPage)."""
    return f"{app_base_url}/verify-account?token={token}"
