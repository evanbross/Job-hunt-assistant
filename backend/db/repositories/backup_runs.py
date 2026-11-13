# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-07
"""The record of each off-site database backup (docs/cloud-deployment-readiness-plan.md 4.1).

Written only by the backup itself, which runs as the schema owner (backend/db/backup.py);
the runtime role has no access to this table at all (backend/db/app_role.py). It is how a
scheduled run knows whether one is due -- the bucket's credentials can write but not
list -- and the place monitoring reads a backup's age from."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..base import _BaseRepo
from ..connection import _row_to_dict

SUCCEEDED = "succeeded"
FAILED = "failed"
RUNNING = "running"

_ERROR_LIMIT = 4000  # a pg_dump error is a few lines; keep a runaway stderr out of the row


class PostgresBackupRunRepository(_BaseRepo):
    def start(self, object_key: str, schema_hash: str) -> int:
        cur = self._execute(
            "INSERT INTO backup_runs (object_key, schema_hash, status) VALUES (%s, %s, %s)"
            " RETURNING id",
            (object_key, schema_hash, RUNNING),
        )
        return int(cur.fetchone()["id"])

    def succeed(self, run_id: int, size_bytes: int, sha256: str) -> None:
        self._execute(
            "UPDATE backup_runs SET status = %s, finished_at = NOW(), size_bytes = %s,"
            " sha256 = %s WHERE id = %s",
            (SUCCEEDED, size_bytes, sha256, run_id),
        )

    def fail(self, run_id: int, error: str) -> None:
        self._execute(
            "UPDATE backup_runs SET status = %s, finished_at = NOW(), error = %s WHERE id = %s",
            (FAILED, error[-_ERROR_LIMIT:], run_id),
        )

    def last_success_at(self) -> datetime | None:
        cur = self._execute(
            "SELECT MAX(started_at) AS at FROM backup_runs WHERE status = %s", (SUCCEEDED,)
        )
        return cur.fetchone()["at"]

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        cur = self._execute(
            "SELECT id, object_key, schema_hash, status, started_at, finished_at, size_bytes,"
            " sha256, error FROM backup_runs ORDER BY started_at DESC, id DESC LIMIT %s",
            (limit,),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]
