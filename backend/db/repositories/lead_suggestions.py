# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Suggested leads: conversations from an imported LinkedIn messages.csv that may
be a recruiter reaching out (backend/db/schema.py's lead_suggestions table). A row
moves unclassified -> suggested / not_recruiter (Contacts rules or the AI), then
the user accepts or dismisses it. No method here commits."""

from __future__ import annotations

from typing import Any

from psycopg2.extras import Json

from ..base import _BaseRepo
from ..connection import _row_to_dict

# ai: claude-opus-5-5 | 2026-11-11
# Mirrors the CHECK on lead_suggestions.status.
SUGGESTION_STATUSES: tuple[str, ...] = ("unclassified", "suggested", "not_recruiter", "accepted", "dismissed")

_LIST_FIELDS = (
    "id, source, external_ref, status, detection, reason, counterpart_name, counterpart_profile_url,"
    " contact_id, job_id, conversation_title, role_guess, hiring_company_guess, recruiter_company_guess,"
    " first_message_at, last_message_at, message_count, messages, imported_at, decided_at"
)


# ai: claude-opus-5-5 | 2026-11-11
class PostgresLeadSuggestionRepository(_BaseRepo):
    def upsert(self, user_id: int, row: dict[str, Any], imported_at: str) -> dict[str, Any]:
        """Insert a conversation, or refresh one imported before (newer messages,
        the matched contact). Its status only moves when this import's verdict is
        stronger: an unchecked row takes whatever this import decided, and a row
        ruled out becomes suggested when a Contacts mark now says recruiter. An
        accepted or dismissed row keeps its status. Returns the row's id, status,
        contact_id, job_id and whether it was new."""
        cur = self._execute(
            """
            INSERT INTO lead_suggestions AS s
                (user_id, source, external_ref, status, detection, reason, counterpart_name,
                 counterpart_profile_url, contact_id, conversation_title, first_message_at,
                 last_message_at, message_count, messages, imported_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, source, external_ref) DO UPDATE SET
                counterpart_name = EXCLUDED.counterpart_name,
                counterpart_profile_url = EXCLUDED.counterpart_profile_url,
                contact_id = COALESCE(EXCLUDED.contact_id, s.contact_id),
                conversation_title = EXCLUDED.conversation_title,
                first_message_at = EXCLUDED.first_message_at,
                last_message_at = EXCLUDED.last_message_at,
                message_count = EXCLUDED.message_count,
                messages = EXCLUDED.messages,
                imported_at = EXCLUDED.imported_at,
                status = CASE
                    WHEN s.status = 'unclassified' THEN EXCLUDED.status
                    WHEN s.status = 'not_recruiter' AND EXCLUDED.status = 'suggested' THEN 'suggested'
                    ELSE s.status END,
                detection = CASE
                    WHEN s.status = 'unclassified'
                      OR (s.status = 'not_recruiter' AND EXCLUDED.status = 'suggested')
                    THEN EXCLUDED.detection ELSE s.detection END,
                reason = CASE
                    WHEN s.status = 'unclassified'
                      OR (s.status = 'not_recruiter' AND EXCLUDED.status = 'suggested')
                    THEN EXCLUDED.reason ELSE s.reason END
            RETURNING id, status, contact_id, job_id, (xmax = 0) AS inserted
            """,
            (
                user_id, row["source"], row["external_ref"], row["status"], row.get("detection"),
                row.get("reason"), row["counterpart_name"], row.get("counterpart_profile_url"),
                row.get("contact_id"), row.get("conversation_title"), row["first_message_at"],
                row["last_message_at"], row["message_count"], Json(row["messages"]), imported_at,
            ),
        )
        return _row_to_dict(cur.fetchone())

    def list_by_status(self, user_id: int, status: str) -> list[dict[str, Any]]:
        """One tab of the inbox, most recently active first."""
        cur = self._execute(
            f"SELECT {_LIST_FIELDS} FROM lead_suggestions"
            " WHERE user_id = %s AND status = %s ORDER BY last_message_at DESC, id DESC",
            (user_id, status),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def counts(self, user_id: int) -> dict[str, int]:
        """How many rows are in each status (every status present, zero if none)."""
        cur = self._execute(
            "SELECT status, COUNT(*) AS n FROM lead_suggestions WHERE user_id = %s GROUP BY status",
            (user_id,),
        )
        found = {r["status"]: int(r["n"]) for r in cur.fetchall()}
        return {status: found.get(status, 0) for status in SUGGESTION_STATUSES}

    def get(self, user_id: int, suggestion_id: int) -> dict[str, Any] | None:
        cur = self._execute(
            f"SELECT {_LIST_FIELDS} FROM lead_suggestions WHERE id = %s AND user_id = %s",
            (suggestion_id, user_id),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def list_unclassified(self, user_id: int, limit: int) -> list[dict[str, Any]]:
        """The next rows waiting for the AI, newest conversations first, with the
        matched contact's title and company (what the AI is told about the sender)."""
        fields = ", ".join(f"s.{name.strip()}" for name in _LIST_FIELDS.split(","))
        cur = self._execute(
            f"SELECT {fields}, c.position AS contact_position, c.company_raw AS contact_company"
            " FROM lead_suggestions s"
            " LEFT JOIN contacts c ON c.id = s.contact_id AND c.user_id = s.user_id"
            " WHERE s.user_id = %s AND s.status = 'unclassified'"
            " ORDER BY s.last_message_at DESC, s.id DESC LIMIT %s",
            (user_id, limit),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    def record_verdict(self, user_id: int, suggestion_id: int, verdict: dict[str, Any]) -> bool:
        """Store the AI's verdict on a row still waiting for one. False when the
        row was decided meanwhile (the user dismissed it, a re-import ruled on it)."""
        cur = self._execute(
            """
            UPDATE lead_suggestions
            SET status = %s, detection = 'ai', reason = %s, role_guess = %s,
                hiring_company_guess = %s, recruiter_company_guess = %s
            WHERE id = %s AND user_id = %s AND status = 'unclassified'
            """,
            (
                verdict["status"], verdict.get("reason"), verdict.get("role_guess"),
                verdict.get("hiring_company_guess"), verdict.get("recruiter_company_guess"),
                suggestion_id, user_id,
            ),
        )
        return cur.rowcount > 0

    def move(
        self, user_id: int, suggestion_id: int, to_status: str, from_statuses: tuple[str, ...], decided_at: str | None
    ) -> bool:
        """Dismiss or restore: change status only from one of *from_statuses*."""
        cur = self._execute(
            "UPDATE lead_suggestions SET status = %s, decided_at = %s"
            " WHERE id = %s AND user_id = %s AND status = ANY(%s)",
            (to_status, decided_at, suggestion_id, user_id, list(from_statuses)),
        )
        return cur.rowcount > 0

    def mark_accepted(
        self, user_id: int, suggestion_id: int, contact_id: int, job_id: int | None, decided_at: str
    ) -> bool:
        cur = self._execute(
            "UPDATE lead_suggestions SET status = 'accepted', contact_id = %s, job_id = %s, decided_at = %s"
            " WHERE id = %s AND user_id = %s AND status <> 'accepted'",
            (contact_id, job_id, decided_at, suggestion_id, user_id),
        )
        return cur.rowcount > 0
