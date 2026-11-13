# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Loads, saves, and deletes a user's Google OAuth credentials.

Each user's token is stored encrypted in their own row (not in a file shared
by every account), so one user connecting, refreshing, or disconnecting Google
can never read or affect another user's mailbox access."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import httpx
from google.oauth2.credentials import Credentials

from backend.crypto import decrypt_secret, encrypt_secret
from backend.db.interfaces import IGoogleTokenRepository
from backend.email.gmail_scopes import gmail_scopes

logger = logging.getLogger(__name__)

# ai: claude-opus-5-5 | 2026-11-10
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_REVOKE_TIMEOUT_SECONDS = 10


# ai: claude-opus-5-5 | 2026-11-10
def revoke_google_access(token: str) -> bool:
    """Ask Google to revoke the app's access for this token's grant.

    Revoking the refresh token ends the whole grant, so the app disappears from
    the user's Google account permissions and no copy of any token still works.
    Returns True when access is gone: revoked now, or Google says the token is
    already invalid (400, e.g. the user removed the app themselves). False when
    Google couldn't be reached or refused for another reason, so the caller can
    tell the user to remove access by hand. Never raises for a network failure.
    """
    try:
        response = httpx.post(
            GOOGLE_REVOKE_URL,
            data={"token": token},
            timeout=_REVOKE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning("Could not reach Google to revoke a token: %s", exc)
        return False
    if response.status_code in (200, 400):
        return True
    logger.warning("Google refused to revoke a token: HTTP %s", response.status_code)
    return False


# ai: claude-sonnet-5 | 2026-09-26
class GoogleTokenStore:
    def __init__(self, tokens: IGoogleTokenRepository) -> None:
        self._tokens = tokens

    def is_connected(self, user_id: int) -> bool:
        return self._tokens.get(user_id) is not None

    def load(self, user_id: int) -> Credentials | None:
        info = self._load_info(user_id)
        if info is None:
            return None
        return Credentials.from_authorized_user_info(info, gmail_scopes())  # ai: claude-opus-5-5 | 2026-11-09

    # ai: claude-opus-5-5 | 2026-10-24 — None (not []) when there is no token,
    # so a caller learns "connected?" and the scopes from the one read.
    def granted_scopes(self, user_id: int) -> list[str] | None:
        """Scopes recorded in the stored token, or None when the user hasn't
        connected Google; reads only local state, so it can never trigger a
        refresh or an auth flow."""
        info = self._load_info(user_id)
        return None if info is None else list(info.get("scopes") or [])

    def save(self, user_id: int, credentials: Credentials) -> None:
        """Store (or replace) the user's token. The caller commits."""
        self._tokens.upsert(
            user_id,
            encrypt_secret(credentials.to_json()),
            datetime.now(UTC).isoformat(timespec="seconds"),
        )

    def delete(self, user_id: int) -> bool:
        return self._tokens.delete(user_id)

    # ai: claude-opus-5-5 | 2026-11-10
    def revocable_token(self, user_id: int) -> str | None:
        """The token to hand revoke_google_access(): the refresh token, which ends
        the whole grant, else the access token. None when not connected."""
        info = self._load_info(user_id)
        if info is None:
            return None
        return info.get("refresh_token") or info.get("token") or None

    def _load_info(self, user_id: int) -> dict | None:
        row = self._tokens.get(user_id)
        if row is None:
            return None
        return json.loads(decrypt_secret(row["token_encrypted"]))
