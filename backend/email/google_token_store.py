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
from datetime import UTC, datetime

from google.oauth2.credentials import Credentials

import config
from backend.crypto import decrypt_secret, encrypt_secret
from backend.db.interfaces import IGoogleTokenRepository


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
        return Credentials.from_authorized_user_info(info, config.GMAIL_SCOPES)

    def granted_scopes(self, user_id: int) -> list[str]:
        """Scopes recorded in the stored token; reads only local state, so it
        can never trigger a refresh or an auth flow."""
        info = self._load_info(user_id)
        return list(info.get("scopes") or []) if info else []

    def save(self, user_id: int, credentials: Credentials) -> None:
        """Store (or replace) the user's token. The caller commits."""
        self._tokens.upsert(
            user_id,
            encrypt_secret(credentials.to_json()),
            datetime.now(UTC).isoformat(timespec="seconds"),
        )

    def delete(self, user_id: int) -> bool:
        return self._tokens.delete(user_id)

    def _load_info(self, user_id: int) -> dict | None:
        row = self._tokens.get(user_id)
        if row is None:
            return None
        return json.loads(decrypt_secret(row["token_encrypted"]))
