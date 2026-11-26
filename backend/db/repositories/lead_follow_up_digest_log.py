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
from datetime import UTC, datetime, timedelta

from ..base import _BaseRepo

logger = logging.getLogger(__name__)


# ai: claude-opus-5-5 | 2026-11-18
class PostgresLeadFollowUpDigestLogRepository(_BaseRepo):
    """One row per (account, local date) whose lead follow-up email was claimed --
    see backend/leads/follow_up_digest.py."""

    # ai: claude-sonnet-5 | 2026-11-26
    MAX_DELIVERY_ATTEMPTS = 3
    # Below this age, an undelivered claim is presumed still in flight (another
    # worker mid-send, or this same call racing a duplicate dispatch) rather than
    # abandoned, so a retry is refused. Well under the 15-minute tick interval
    # send_lead_follow_up_digest runs on, so a genuinely failed attempt is still
    # retried on the very next tick.
    RETRY_COOLDOWN_SECONDS = 300

    # ai: claude-sonnet-5 | 2026-11-26 -- claim() used to be the only write: it set
    # sent_at before send_email() ever ran, so a crash or SMTP failure between the
    # claim and the send left the day permanently marked "sent" with nothing
    # delivered and no way to retry. Claiming and delivering are now separate: this
    # grants up to MAX_DELIVERY_ATTEMPTS tries per day (the caller must call
    # mark_delivered() only after send_email() actually returns), refuses to hand
    # out a retry while the previous attempt could still be in flight, and gives
    # up (logged) once attempts are exhausted rather than retrying forever.
    def claim(self, user_id: int, digest_date: str) -> bool:
        """Claim an attempt at sending *digest_date* (YYYY-MM-DD, the account's
        local date) for *user_id*. True when the caller may (re)try sending;
        False when that day is already delivered, a previous attempt may still
        be in flight, or retries are exhausted -- the caller must not send. The
        INSERT .. ON CONFLICT .. WHERE is a single atomic statement: it only
        touches/returns a row when all three conditions hold, so it holds under
        concurrent workers -- a SELECT-then-UPDATE would let two of them both
        pass."""
        now = datetime.now(UTC)
        cooldown_before = (now - timedelta(seconds=self.RETRY_COOLDOWN_SECONDS)).isoformat(timespec="seconds")
        cur = self._execute(
            """
            INSERT INTO lead_follow_up_digest_log (user_id, digest_date, sent_at, attempts)
            VALUES (%s, %s, %s, 1)
            ON CONFLICT (user_id, digest_date) DO UPDATE
                SET attempts = lead_follow_up_digest_log.attempts + 1,
                    sent_at = EXCLUDED.sent_at
            WHERE lead_follow_up_digest_log.delivered_at IS NULL
              AND lead_follow_up_digest_log.attempts < %s
              AND lead_follow_up_digest_log.sent_at < %s
            RETURNING attempts
            """,
            (user_id, digest_date, now.isoformat(timespec="seconds"), self.MAX_DELIVERY_ATTEMPTS, cooldown_before),
        )
        if cur.fetchone() is not None:
            return True
        self._log_claim_denied(user_id, digest_date)
        return False

    def mark_delivered(self, user_id: int, digest_date: str) -> None:
        """Record that *digest_date*'s email actually went out. Call this only
        after send_email() returns without raising -- never speculatively."""
        self._execute(
            "UPDATE lead_follow_up_digest_log SET delivered_at = %s"
            " WHERE user_id = %s AND digest_date = %s",
            (datetime.now(UTC).isoformat(timespec="seconds"), user_id, digest_date),
        )

    def _log_claim_denied(self, user_id: int, digest_date: str) -> None:
        """claim() returned False for one of three reasons: delivery already
        happened, or a prior attempt is still within its cooldown (both common
        and not worth logging), or every attempt was used up without a
        successful send -- the one case worth surfacing, since nothing else
        will."""
        cur = self._execute(
            "SELECT delivered_at, attempts FROM lead_follow_up_digest_log"
            " WHERE user_id = %s AND digest_date = %s",
            (user_id, digest_date),
        )
        row = cur.fetchone()
        if row is not None and row["delivered_at"] is None and row["attempts"] >= self.MAX_DELIVERY_ATTEMPTS:
            logger.warning(
                "Giving up on the lead follow-up email for user %s on %s after %s failed attempts",
                user_id, digest_date, row["attempts"],
            )
