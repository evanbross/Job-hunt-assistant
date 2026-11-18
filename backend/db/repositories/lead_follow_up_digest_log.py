# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

import logging
from datetime import UTC, datetime

import psycopg2

from ..base import _BaseRepo

logger = logging.getLogger(__name__)


# ai: claude-opus-5-5 | 2026-11-18
class PostgresLeadFollowUpDigestLogRepository(_BaseRepo):
    """One row per (account, local date) whose lead follow-up email was claimed --
    see backend/leads/follow_up_digest.py."""

    def claim(self, user_id: int, digest_date: str) -> bool:
        """Claim *digest_date* (YYYY-MM-DD, the account's local date) for *user_id*.
        True when this call won; False when that day was already claimed, and the
        caller must not send. A plain INSERT that the (user_id, digest_date) UNIQUE
        constraint rejects the second time, so it holds under concurrent workers --
        a SELECT-then-INSERT would let two of them both pass."""
        try:
            self._execute(
                "INSERT INTO lead_follow_up_digest_log (user_id, digest_date, sent_at)"
                " VALUES (%s, %s, %s)",
                (user_id, digest_date, datetime.now(UTC).isoformat(timespec="seconds")),
            )
        except psycopg2.errors.UniqueViolation:
            # The failed INSERT aborted the transaction; every later statement on this
            # connection fails until it is rolled back.
            self._conn.rollback()
            logger.info("Lead follow-up email for user %s already claimed for %s", user_id, digest_date)
            return False
        return True
