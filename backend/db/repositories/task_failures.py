# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

from typing import Any

from ..base import _BaseRepo
from ..connection import _row_to_dict


# ai: claude-opus-5-5 | 2026-11-09
class PostgresTaskFailureRepository(_BaseRepo):
    """Background tasks that raised, per account (plan 4.5). backend/task_failures.py is
    the only writer, and redacts the message before it gets here; the admin account
    view reads it. Pruned by the data lifecycle sweep (TASK_FAILURES_RETENTION_DAYS)."""

    def insert(
        self, user_id: int, task_name: str, task_id: str | None, error_type: str, error_message: str
    ) -> int:
        cur = self._execute(
            "INSERT INTO task_failures (user_id, task_name, task_id, error_type, error_message)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (user_id, task_name, task_id, error_type, error_message),
        )
        return cur.fetchone()["id"]

    def get_recent(self, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        cur = self._execute(
            "SELECT id, task_name, task_id, error_type, error_message, failed_at FROM task_failures"
            " WHERE user_id = %s ORDER BY failed_at DESC, id DESC LIMIT %s",
            (user_id, limit),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]
