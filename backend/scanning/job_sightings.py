# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-15
"""Turning a capture of an already-tracked posting into a sighting.

A capture that names a posting already in `jobs` (the same URL or site job ID,
see backend/scoring/posting_identity.py) is recorded on that job as a sighting
(backend/db/repositories/job_sightings.py) rather than inserted as a second row.
The capture paths decide the match up front with db_match_for_job; this module
holds the two pieces they share: building the sighting from the capture's
job_data, and the insert-time fallback for a match that check couldn't see (a
concurrent import, or the same new job arriving twice in one email scan)."""

from __future__ import annotations

import logging
from typing import Any

from backend.db import DuplicateJobUrlError, SightingChannel
from backend.db.job_columns import JobColumns

logger = logging.getLogger(__name__)


def email_sighting_capture(parsed: dict[str, Any], sender: str, subject: str) -> dict[str, Any]:
    """The sighting fields for one parsed job of a digest email (routes/scan.py,
    routes/emails.py), in the shape record_sighting_from_job_data reads."""
    return {
        "source": parsed.get("source", ""),
        "url": parsed.get("url", ""),
        "sender_email": sender,
        JobColumns.CAPTURE_LABEL: subject,
    }


def extension_sighting_capture(url_row: dict[str, Any], capture_source_name: str) -> dict[str, Any]:
    """The sighting fields for one browser-extension-captured row (backend/tasks.py),
    sourced the way ManualJobIngestService names extension captures."""
    platform = str(url_row.get("platform") or "").strip()
    return {
        "source": f"Browser Extension - {platform}" if platform else "Browser Extension",
        "url": str(url_row.get("url") or ""),
        JobColumns.CAPTURE_LABEL: capture_source_name or "Browser Extension capture",
    }


def record_sighting_from_job_data(
    repos: dict[str, Any],
    user_id: int,
    job_id: int,
    job_data: dict[str, Any],
    channel: SightingChannel,
) -> bool:
    """Record *job_data* (the row a capture would have inserted) as a sighting of
    job *job_id*. Returns False when one from the same source today already exists."""
    return repos["job_sightings"].record(
        user_id,
        job_id,
        source=str(job_data.get("source") or ""),
        channel=channel,
        capture_label=job_data.get(JobColumns.CAPTURE_LABEL),
        url=job_data.get("url"),
        sender_email=job_data.get("sender_email"),
    )


def insert_or_record_sighting(
    repos: dict[str, Any],
    user_id: int,
    job_data: dict[str, Any],
    channel: SightingChannel,
) -> int | None:
    """Insert *job_data*; when another row already holds its URL, record a sighting
    on that row instead. Returns the new job's id, or None when it became a sighting.
    The lost insert rolls back only to its own savepoint (jobs repository), so the
    caller's earlier uncommitted writes survive and one commit can cover them all."""
    try:
        return repos["jobs"].insert(user_id, job_data)
    except DuplicateJobUrlError as exc:
        existing = repos["jobs"].get_by_url(user_id, exc.url)
        if existing is None:
            raise
        logger.info(
            "%s is already job %s; recording a sighting instead of a second row",
            exc.url,
            existing["id"],
        )
        record_sighting_from_job_data(repos, user_id, int(existing["id"]), job_data, channel)
        return None
