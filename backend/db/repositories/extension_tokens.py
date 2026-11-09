# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Persistence for per-user browser-extension credentials (hashed tokens)."""

from __future__ import annotations

from typing import Any

from ..base import _BaseRepo
from ..connection import _row_to_dict

_PUBLIC_COLUMNS = "id, user_id, token_prefix, label, created_at, last_used_at, expires_at, revoked_at"


# ai: claude-sonnet-5 | 2026-09-26
class PostgresExtensionTokenRepository(_BaseRepo):
    def create(
        self,
        user_id: int,
        token_hash: str,
        token_prefix: str,
        label: str,
        created_at: str,
        expires_at: str | None,
    ) -> dict[str, Any]:
        cur = self._execute(
            f"""
            INSERT INTO extension_tokens
                (user_id, token_hash, token_prefix, label, created_at, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING {_PUBLIC_COLUMNS}
            """,
            (user_id, token_hash, token_prefix, label, created_at, expires_at),
        )
        return _row_to_dict(cur.fetchone())

    def get_active_by_hash(self, token_hash: str, now: str) -> dict[str, Any] | None:
        """Auth lookup. Deliberately not user-scoped: the token itself is what
        determines which user is calling. Revoked and expired tokens never match,
        and neither does any token of an account an admin disabled (plan 4.5) --
        the token rows stay, so re-enabling the account brings them back."""
        # ai: claude-opus-5-5 | 2026-11-09 -- disabled accounts' tokens excluded
        cur = self._execute(
            f"""
            SELECT {_PUBLIC_COLUMNS} FROM extension_tokens
            WHERE token_hash = %s
              AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at > %s)
              AND NOT EXISTS (
                  SELECT 1 FROM user_accounts
                  WHERE user_accounts.id = extension_tokens.user_id
                    AND user_accounts.disabled_at IS NOT NULL
              )
            """,
            (token_hash, now),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def touch_last_used(self, token_id: int, now: str, stale_before: str) -> None:
        """Record use, but only when the stored value is older than
        stale_before, so a chatty client isn't a write per request."""
        self._execute(
            """
            UPDATE extension_tokens SET last_used_at = %s
            WHERE id = %s AND (last_used_at IS NULL OR last_used_at < %s)
            """,
            (now, token_id, stale_before),
        )

    def list_for_user(self, user_id: int) -> list[dict[str, Any]]:
        cur = self._execute(
            f"SELECT {_PUBLIC_COLUMNS} FROM extension_tokens"
            " WHERE user_id = %s ORDER BY created_at DESC, id DESC",
            (user_id,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def revoke(self, user_id: int, token_id: int, revoked_at: str) -> bool:
        cur = self._execute(
            """
            UPDATE extension_tokens SET revoked_at = %s
            WHERE id = %s AND user_id = %s AND revoked_at IS NULL
            """,
            (revoked_at, token_id, user_id),
        )
        return cur.rowcount > 0
