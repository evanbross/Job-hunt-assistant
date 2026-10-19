# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Persistence for each user's saved Greenhouse browser session (stored encrypted)."""

from __future__ import annotations

from typing import Any

from ..base import _BaseRepo
from ..connection import _row_to_dict


# ai: claude-sonnet-5 | 2026-09-26
class PostgresGreenhouseSessionRepository(_BaseRepo):
    def get(self, user_id: int) -> dict[str, Any] | None:
        cur = self._execute(
            "SELECT state_encrypted, updated_at FROM greenhouse_sessions WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def upsert(self, user_id: int, state_encrypted: str, updated_at: str) -> None:
        self._execute(
            """
            INSERT INTO greenhouse_sessions (user_id, state_encrypted, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE
                SET state_encrypted = EXCLUDED.state_encrypted,
                    updated_at = EXCLUDED.updated_at
            """,
            (user_id, state_encrypted, updated_at),
        )

    def delete(self, user_id: int) -> bool:
        cur = self._execute("DELETE FROM greenhouse_sessions WHERE user_id = %s", (user_id,))
        return cur.rowcount > 0
