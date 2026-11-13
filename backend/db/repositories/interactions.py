# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""The call / message / note log behind recruiter lead tracking: one row per
interaction, tied to a contact, a job, or both. Outbound messages here are also
what the Contacts tab calls "outreach" (see PostgresContactRepository.list_with_outreach).

Every write that names a contact or job checks that it belongs to the same user in
the same statement. Row-level security only checks the interactions row's own
user_id; a foreign key would happily point at another account's contact."""

from __future__ import annotations

from typing import Any

from ..base import _BaseRepo
from ..connection import _row_to_dict

# ai: claude-opus-5-5 | 2026-11-11
# The columns returned for an interaction everywhere it is listed.
_INTERACTION_FIELDS = (
    "i.id, i.contact_id, i.job_id, i.occurred_at, i.channel, i.direction, i.kind,"
    " i.summary, i.body_html, i.source, i.external_ref, i.response_received_at, i.created_at"
)


# ai: claude-opus-5-5 | 2026-11-11
class PostgresInteractionRepository(_BaseRepo):
    _INSERT_COLUMNS: frozenset[str] = frozenset(
        {
            "contact_id", "job_id", "occurred_at", "channel", "direction", "kind",
            "summary", "body_html", "source", "external_ref", "response_received_at",
        }
    )
    # Which contact or job an interaction belongs to is fixed once logged.
    _UPDATE_COLUMNS: frozenset[str] = frozenset(
        {
            "occurred_at", "channel", "direction", "kind", "summary", "body_html",
            "response_received_at",
        }
    )

    def add(self, user_id: int, fields: dict[str, Any], created_at: str) -> dict[str, Any] | None:
        """Insert one interaction. Returns None (and inserts nothing) when the named
        contact or job doesn't exist or belongs to another user."""
        row = self._insert(user_id, fields, created_at, skip_known_ref=False)
        return self.get_by_id(user_id, row["id"]) if row else None

    # ai: claude-opus-5-5 | 2026-11-11
    def add_imported(self, user_id: int, fields: dict[str, Any], created_at: str) -> bool:
        """Insert one interaction from an external import (fields carry source and
        external_ref). False when that source already logged that external_ref --
        re-importing the same export adds nothing -- or when the named contact or
        job isn't this user's."""
        if not fields.get("external_ref"):
            raise ValueError("An imported interaction needs an external_ref")
        return self._insert(user_id, fields, created_at, skip_known_ref=True) is not None

    # ai: claude-opus-5-5 | 2026-11-11 -- the body of add(), shared with add_imported()
    def _insert(
        self, user_id: int, fields: dict[str, Any], created_at: str, *, skip_known_ref: bool
    ) -> dict[str, Any] | None:
        unknown = set(fields) - self._INSERT_COLUMNS
        if unknown:
            raise ValueError(f"Attempt to insert unknown columns: {unknown}")
        contact_id = fields.get("contact_id")
        job_id = fields.get("job_id")
        if contact_id is None and job_id is None:
            raise ValueError("An interaction needs a contact_id or a job_id")
        columns = list(fields)
        # The partial unique index idx_interactions_user_external_ref.
        on_conflict = (
            " ON CONFLICT (user_id, source, external_ref) WHERE external_ref IS NOT NULL DO NOTHING"
            if skip_known_ref
            else ""
        )
        cur = self._execute(
            f"INSERT INTO interactions (user_id, {', '.join(columns)}, created_at)"
            f" SELECT %s, {', '.join(['%s'] * len(columns))}, %s"
            " WHERE (%s::integer IS NULL"
            "        OR EXISTS (SELECT 1 FROM contacts WHERE id = %s AND user_id = %s))"
            "   AND (%s::integer IS NULL"
            "        OR EXISTS (SELECT 1 FROM jobs WHERE id = %s AND user_id = %s))"
            f"{on_conflict}"
            " RETURNING id",
            [
                user_id, *fields.values(), created_at,
                contact_id, contact_id, user_id,
                job_id, job_id, user_id,
            ],
        )
        return cur.fetchone()

    def get_by_id(self, user_id: int, interaction_id: int) -> dict[str, Any] | None:
        cur = self._execute(
            f"SELECT {_INTERACTION_FIELDS} FROM interactions i WHERE i.id = %s AND i.user_id = %s",
            (interaction_id, user_id),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def update(
        self, user_id: int, interaction_id: int, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Apply a partial update; returns the updated row, or None when it doesn't
        exist or belongs to another user."""
        unknown = set(fields) - self._UPDATE_COLUMNS
        if unknown:
            raise ValueError(f"Attempt to update unknown columns: {unknown}")
        if not fields:
            raise ValueError("No fields to update")
        assignments = ", ".join(f"{col} = %s" for col in fields)
        cur = self._execute(
            f"UPDATE interactions SET {assignments} WHERE id = %s AND user_id = %s RETURNING id",
            [*fields.values(), interaction_id, user_id],
        )
        return self.get_by_id(user_id, interaction_id) if cur.fetchone() else None

    def delete(self, user_id: int, interaction_id: int) -> bool:
        cur = self._execute(
            "DELETE FROM interactions WHERE id = %s AND user_id = %s", (interaction_id, user_id)
        )
        return cur.rowcount > 0

    def list_for_job(self, user_id: int, job_id: int) -> list[dict[str, Any]]:
        """A job's interactions newest first, each with the contact's name."""
        cur = self._execute(
            f"SELECT {_INTERACTION_FIELDS}, c.full_name AS contact_name"
            " FROM interactions i LEFT JOIN contacts c ON c.id = i.contact_id AND c.user_id = i.user_id"
            " WHERE i.user_id = %s AND i.job_id = %s"
            " ORDER BY i.occurred_at DESC, i.id DESC",
            (user_id, job_id),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def list_for_contact(self, user_id: int, contact_id: int) -> list[dict[str, Any]]:
        """A contact's interactions newest first, each with its job's company and
        title (NULL for a conversation not tied to a role)."""
        cur = self._execute(
            f"SELECT {_INTERACTION_FIELDS}, j.company AS job_company, j.job_title AS job_title"
            " FROM interactions i LEFT JOIN jobs j ON j.id = i.job_id AND j.user_id = i.user_id"
            " WHERE i.user_id = %s AND i.contact_id = %s"
            " ORDER BY i.occurred_at DESC, i.id DESC",
            (user_id, contact_id),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]
