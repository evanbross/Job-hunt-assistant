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

"""Outreach log for the Contacts tab: one row per message the user sent to a
contact (by email or LinkedIn message -- see channel), with the date a reply
came back (if any)."""

from __future__ import annotations

from typing import Any

from ..base import _BaseRepo
from ..connection import _row_to_dict


# ai: claude-sonnet-5 | 2026-09-25
# ai: claude-opus-5-5 | 2026-10-14 -- was PostgresContactEmailRepository over
# contact_emails; add() now records the outreach channel.
class PostgresContactOutreachRepository(_BaseRepo):
    def add(
        self, user_id: int, contact_id: int, sent_at: str, channel: str
    ) -> dict[str, Any] | None:
        """Log a sent message. Returns None when the contact doesn't exist or
        belongs to another user (the INSERT ... SELECT matches no row)."""
        cur = self._execute(
            """
            INSERT INTO contact_outreach (user_id, contact_id, sent_at, channel)
            SELECT %s, c.id, %s, %s FROM contacts c WHERE c.id = %s AND c.user_id = %s
            RETURNING id, sent_at, response_received_at, channel
            """,
            (user_id, sent_at, channel, contact_id, user_id),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def set_response(
        self, user_id: int, outreach_id: int, response_received_at: str | None
    ) -> bool:
        cur = self._execute(
            "UPDATE contact_outreach SET response_received_at = %s WHERE id = %s AND user_id = %s",
            (response_received_at, outreach_id, user_id),
        )
        return cur.rowcount > 0

    def delete(self, user_id: int, outreach_id: int) -> bool:
        cur = self._execute(
            "DELETE FROM contact_outreach WHERE id = %s AND user_id = %s",
            (outreach_id, user_id),
        )
        return cur.rowcount > 0
