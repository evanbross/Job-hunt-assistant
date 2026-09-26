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

"""Persistence for each user's candidate profile text (the career summary that
feeds fit scoring, cover letters, and pitches)."""

from __future__ import annotations

from typing import Any

from ..base import _BaseRepo
from ..connection import _row_to_dict


# ai: claude-sonnet-5 | 2026-09-26
class PostgresCandidateProfileRepository(_BaseRepo):
    def get(self, user_id: int) -> dict[str, Any] | None:
        cur = self._execute(
            "SELECT content, updated_at FROM candidate_profiles WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def get_for_update(self, user_id: int) -> dict[str, Any] | None:
        """Like get(), but row-locks the profile until the transaction ends so a
        read-modify-write (e.g. appending a learned rule) can't lose a
        concurrent edit."""
        cur = self._execute(
            "SELECT content, updated_at FROM candidate_profiles WHERE user_id = %s FOR UPDATE",
            (user_id,),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def upsert(self, user_id: int, content: str, updated_at: str) -> None:
        self._execute(
            """
            INSERT INTO candidate_profiles (user_id, content, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE
                SET content = EXCLUDED.content, updated_at = EXCLUDED.updated_at
            """,
            (user_id, content, updated_at),
        )
