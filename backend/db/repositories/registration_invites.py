# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-07
"""Emails an admin has invited to register (docs/cloud-deployment-readiness-plan.md 4.6).

Instance-wide, not per tenant: registration reads it before any account (or session)
exists, so it carries no user_id and row-level security doesn't apply; the admin-only
routes are what guard it. Emails are stored normalized (backend/security/account_email.py)."""

from __future__ import annotations

from typing import Any

from ..base import _BaseRepo
from ..connection import _row_to_dict

_COLUMNS = "email, invited_by, created_at"


class PostgresRegistrationInviteRepository(_BaseRepo):
    def list_all(self) -> list[dict[str, Any]]:
        cur = self._execute(f"SELECT {_COLUMNS} FROM registration_invites ORDER BY created_at, email")
        return [_row_to_dict(row) for row in cur.fetchall()]

    def exists(self, email: str) -> bool:
        cur = self._execute("SELECT 1 FROM registration_invites WHERE email = %s", (email,))
        return cur.fetchone() is not None

    def add(self, email: str, invited_by: int) -> dict[str, Any] | None:
        """The new invite, or None if *email* is already invited."""
        cur = self._execute(
            f"""
            INSERT INTO registration_invites (email, invited_by) VALUES (%s, %s)
            ON CONFLICT (email) DO NOTHING
            RETURNING {_COLUMNS}
            """,
            (email, invited_by),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def remove(self, email: str) -> bool:
        """Revoke an invite (or consume it on a completed registration). True if one
        existed -- a single DELETE, so two callers can never both consume the same one."""
        cur = self._execute("DELETE FROM registration_invites WHERE email = %s RETURNING email", (email,))
        return cur.fetchone() is not None
