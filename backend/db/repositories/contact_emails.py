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

"""Outreach-email log for the Contacts tab: one row per email the user sent to
a contact, with the date a reply came back (if any)."""

from __future__ import annotations

from typing import Any

from ..base import _BaseRepo
from ..connection import _row_to_dict


# ai: claude-sonnet-5 | 2026-09-25
class PostgresContactEmailRepository(_BaseRepo):
    def add(self, user_id: int, contact_id: int, sent_at: str) -> dict[str, Any] | None:
        """Log a sent email. Returns None when the contact doesn't exist or
        belongs to another user (the INSERT ... SELECT matches no row)."""
        cur = self._execute(
            """
            INSERT INTO contact_emails (user_id, contact_id, sent_at)
            SELECT %s, c.id, %s FROM contacts c WHERE c.id = %s AND c.user_id = %s
            RETURNING id, sent_at, response_received_at
            """,
            (user_id, sent_at, contact_id, user_id),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def set_response(
        self, user_id: int, email_id: int, response_received_at: str | None
    ) -> bool:
        cur = self._execute(
            "UPDATE contact_emails SET response_received_at = %s WHERE id = %s AND user_id = %s",
            (response_received_at, email_id, user_id),
        )
        return cur.rowcount > 0

    def delete(self, user_id: int, email_id: int) -> bool:
        cur = self._execute(
            "DELETE FROM contact_emails WHERE id = %s AND user_id = %s",
            (email_id, user_id),
        )
        return cur.rowcount > 0
