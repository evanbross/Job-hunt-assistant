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
