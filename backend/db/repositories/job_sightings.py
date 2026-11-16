# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Repeat sightings of a job the user already tracks (backend/db/schema.py's
job_sightings table).

When a capture names a posting that's already a row in `jobs` -- the same URL, or
the same site job ID (backend/scoring/posting_identity.py) -- it's recorded here
instead of being inserted as a second jobs row. The job's own source/date_found
stay the first sighting; this table is every one after it.

At most one row per job, source and UTC day (a unique index), so a scheduled
re-scan of the same page can't flood the history. No method here commits."""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from ..base import _BaseRepo
from ..connection import _row_to_dict


# ai: claude-opus-5-5 | 2026-11-15
class SightingChannel(StrEnum):
    """How a sighting arrived. Mirrors the CHECK on job_sightings.channel."""

    EMAIL = "email"
    EXTENSION = "extension"
    MANUAL = "manual"
    # A second jobs row for the same posting, folded into this one by the
    # 2026-11-15 migration (schema.py's _merge_same_posting_job_rows).
    MERGED_DUPLICATE = "merged_duplicate"


_FIELDS = "id, job_id, seen_at, source, channel, capture_label, url, sender_email"


# ai: claude-opus-5-5 | 2026-11-15
class PostgresJobSightingRepository(_BaseRepo):
    def record(
        self,
        user_id: int,
        job_id: int,
        *,
        source: str,
        channel: SightingChannel,
        capture_label: str | None = None,
        url: str | None = None,
        sender_email: str | None = None,
        seen_at: str | None = None,
    ) -> bool:
        """Record one sighting. Returns False when this job already has one from
        the same source on the same UTC day (nothing is written)."""
        cur = self._execute(
            """
            INSERT INTO job_sightings
                (user_id, job_id, seen_at, source, channel, capture_label, url, sender_email)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            (
                user_id,
                job_id,
                seen_at or datetime.now(UTC).isoformat(timespec="seconds"),
                (source or "").strip() or "Unknown",
                str(channel),
                capture_label or None,
                url or None,
                sender_email or None,
            ),
        )
        return cur.fetchone() is not None

    def list_for_job(self, user_id: int, job_id: int) -> list[dict[str, Any]]:
        """Every sighting of one job, newest first."""
        cur = self._execute(
            f"SELECT {_FIELDS} FROM job_sightings WHERE user_id = %s AND job_id = %s"
            " ORDER BY seen_at DESC, id DESC",
            (user_id, job_id),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]

    def counts_by_job(self, user_id: int, job_ids: Collection[int]) -> dict[int, int]:
        """Sighting count per job, for the jobs in *job_ids* that have any."""
        if not job_ids:
            return {}
        cur = self._execute(
            "SELECT job_id, COUNT(*) AS n FROM job_sightings"
            " WHERE user_id = %s AND job_id = ANY(%s) GROUP BY job_id",
            (user_id, list(job_ids)),
        )
        return {int(row["job_id"]): int(row["n"]) for row in cur.fetchall()}
