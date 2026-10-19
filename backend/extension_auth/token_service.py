# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Issues and verifies per-user browser-extension credentials.

A token is 256 bits of randomness with a recognizable prefix. Only its SHA-256
hash is persisted -- the random value is already high-entropy, so a slow
password hash would add cost without adding security -- and the plaintext is
returned exactly once, at issue time."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.db.interfaces import IExtensionTokenRepository

TOKEN_PREFIX = "jha_"
# token_urlsafe(32) yields 43 characters; anything wildly off-size is junk and
# is rejected before it costs a database lookup.
_TOKEN_MIN_LENGTH = len(TOKEN_PREFIX) + 40
_TOKEN_MAX_LENGTH = len(TOKEN_PREFIX) + 64
_DISPLAY_PREFIX_LENGTH = len(TOKEN_PREFIX) + 6
_LAST_USED_WRITE_INTERVAL = timedelta(seconds=60)
MAX_LABEL_LENGTH = 100


@dataclass(frozen=True)
class IssuedToken:
    plaintext: str
    record: dict[str, Any]


@dataclass(frozen=True)
class ExtensionIdentity:
    user_id: int
    token_id: int


# ai: claude-sonnet-5 | 2026-09-26
def hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _utc_iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


# ai: claude-sonnet-5 | 2026-09-26
class ExtensionTokenService:
    def __init__(self, tokens: IExtensionTokenRepository) -> None:
        self._tokens = tokens

    def issue(
        self,
        user_id: int,
        label: str,
        *,
        expires_in_days: int | None = None,
        now: datetime | None = None,
    ) -> IssuedToken:
        label = label.strip()
        if not label:
            raise ValueError("A token label is required")
        if len(label) > MAX_LABEL_LENGTH:
            raise ValueError(f"Token labels are limited to {MAX_LABEL_LENGTH} characters")
        if expires_in_days is not None and expires_in_days < 1:
            raise ValueError("expires_in_days must be at least 1")

        moment = now or datetime.now(UTC)
        plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
        expires_at = (
            _utc_iso(moment + timedelta(days=expires_in_days)) if expires_in_days else None
        )
        record = self._tokens.create(
            user_id,
            hash_token(plaintext),
            plaintext[:_DISPLAY_PREFIX_LENGTH],
            label,
            _utc_iso(moment),
            expires_at,
        )
        return IssuedToken(plaintext=plaintext, record=record)

    def authenticate(
        self, plaintext: str, *, now: datetime | None = None
    ) -> ExtensionIdentity | None:
        """Resolve a presented token to its owner, or None for anything
        unknown, malformed, revoked, or expired. Records last use as a side
        effect; the caller commits."""
        if (
            not plaintext.startswith(TOKEN_PREFIX)
            or not _TOKEN_MIN_LENGTH <= len(plaintext) <= _TOKEN_MAX_LENGTH
        ):
            return None

        moment = now or datetime.now(UTC)
        record = self._tokens.get_active_by_hash(hash_token(plaintext), _utc_iso(moment))
        if record is None:
            return None
        self._tokens.touch_last_used(
            record["id"], _utc_iso(moment), _utc_iso(moment - _LAST_USED_WRITE_INTERVAL)
        )
        return ExtensionIdentity(user_id=record["user_id"], token_id=record["id"])

    def list_for_user(self, user_id: int) -> list[dict[str, Any]]:
        return self._tokens.list_for_user(user_id)

    def revoke(self, user_id: int, token_id: int, *, now: datetime | None = None) -> bool:
        return self._tokens.revoke(user_id, token_id, _utc_iso(now or datetime.now(UTC)))
