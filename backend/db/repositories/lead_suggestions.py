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

from backend.contacts.contact_identity import is_linkedin_member_id_url

from ..base import _BaseRepo
from ..connection import _row_to_dict

# ai: claude-opus-5-5 | 2026-11-11
# Mirrors the CHECK on lead_suggestions.status.
SUGGESTION_STATUSES: tuple[str, ...] = ("unclassified", "suggested", "not_recruiter", "accepted", "dismissed")

# ai: claude-opus-5-5 | 2026-11-16
# The upsert's stored and incoming messages, one per external_ref, oldest first. A
# message's ref is a hash of its conversation, time, direction and text
# (linkedin_messages.message_ref), so the same ref is the same message.
MERGED_MESSAGES_SQL = """(
    SELECT COALESCE(jsonb_agg(m ORDER BY m->>'sent_at', m->>'external_ref'), '[]'::jsonb)
    FROM (
        SELECT DISTINCT ON (m->>'external_ref') m
        FROM jsonb_array_elements(s.messages || EXCLUDED.messages) AS m
        ORDER BY m->>'external_ref'
    ) AS merged(m)
)"""

# Strongest first: which status a person's merged row keeps.
_STATUS_RANK: dict[str, int] = {
    "accepted": 4,
    "suggested": 3,
    "unclassified": 2,
    "dismissed": 1,
    "not_recruiter": 0,
}
_FALLBACK_FIELDS: tuple[str, ...] = (
    "role_guess",
    "hiring_company_guess",
    "recruiter_company_guess",
    "contact_id",
    "job_id",
)


# ai: claude-opus-5-5 | 2026-11-16 -- moved here from lead_suggestion_person_merge.py 2026-11-18
def merged_row(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The row to keep among one person's rows, and the field values it takes:
    the strongest status (accepted, suggested, unclassified, dismissed,
    not_recruiter; most recently active on a tie), every message once, the most
    recently active row's name, URL (a vanity one over a member id) and title, and
    guesses and links from the kept row or else the others."""
    kept = max(rows, key=lambda r: (_STATUS_RANK[r["status"]], r["last_message_at"], r["id"]))
    latest = max(rows, key=lambda r: (r["last_message_at"], r["id"]))
    messages: dict[str, dict[str, Any]] = {}
    for row in rows:
        for message in row["messages"]:
            messages.setdefault(message["external_ref"], message)
    ordered = sorted(messages.values(), key=lambda m: (m["sent_at"], m["external_ref"]))
    # ai: claude-opus-5-5 | 2026-11-18 -- a vanity URL beats a captured member-id one
    vanity = [r for r in rows if r["counterpart_profile_url"] and not is_linkedin_member_id_url(r["counterpart_profile_url"])]
    profile_url = (
        latest["counterpart_profile_url"]
        if latest in vanity or not vanity
        else max(vanity, key=lambda r: (r["last_message_at"], r["id"]))["counterpart_profile_url"]
    )
    merged: dict[str, Any] = {
        "counterpart_name": latest["counterpart_name"],
        "counterpart_profile_url": profile_url,
        "conversation_title": latest["conversation_title"],
        "first_message_at": min(r["first_message_at"] for r in rows),
        "last_message_at": max(r["last_message_at"] for r in rows),
        "imported_at": max(r["imported_at"] for r in rows),
        "messages": ordered,
    }
    for name in _FALLBACK_FIELDS:
        merged[name] = kept[name] if kept[name] is not None else next(
            (r[name] for r in sorted(rows, key=lambda r: r["last_message_at"], reverse=True) if r[name] is not None),
            None,
        )
    return kept, merged


_LIST_FIELDS = (
    "id, source, external_ref, status, detection, reason, counterpart_name, counterpart_profile_url,"
    " contact_id, job_id, conversation_title, role_guess, hiring_company_guess, recruiter_company_guess,"
    " first_message_at, last_message_at, message_count, messages, imported_at, decided_at"
)


# ai: claude-opus-5-5 | 2026-11-11
class PostgresLeadSuggestionRepository(_BaseRepo):
    def upsert(self, user_id: int, row: dict[str, Any], imported_at: str) -> dict[str, Any]:
        """Insert a person's conversations, or refresh a row imported before (newer
        messages, the matched contact). Messages are added, never replaced: a later
        import with a shorter "active within" window may leave out a conversation
        the row already holds. Its status only moves when this import's verdict is
        stronger: an unchecked row takes whatever this import decided, and a row
        ruled out becomes suggested when a Contacts mark now says recruiter. An
        accepted or dismissed row keeps its status. Returns the row's id, status,
        contact_id, job_id and whether it was new."""
        # ai: claude-opus-5-5 | 2026-11-16 -- messages merged by external_ref (one row per
        # person now); title follows the most recently active conversation.
        cur = self._execute(
            f"""
            INSERT INTO lead_suggestions AS s
                (user_id, source, external_ref, status, detection, reason, counterpart_name,
                 counterpart_profile_url, contact_id, conversation_title, first_message_at,
                 last_message_at, message_count, messages, imported_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, source, external_ref) DO UPDATE SET
                counterpart_name = EXCLUDED.counterpart_name,
                counterpart_profile_url = EXCLUDED.counterpart_profile_url,
                contact_id = COALESCE(EXCLUDED.contact_id, s.contact_id),
                conversation_title = CASE WHEN EXCLUDED.last_message_at >= s.last_message_at
                    THEN EXCLUDED.conversation_title ELSE s.conversation_title END,
                first_message_at = LEAST(s.first_message_at, EXCLUDED.first_message_at),
                last_message_at = GREATEST(s.last_message_at, EXCLUDED.last_message_at),
                message_count = jsonb_array_length({MERGED_MESSAGES_SQL}),
                messages = {MERGED_MESSAGES_SQL},
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

    # ai: claude-opus-5-5 | 2026-11-18
    def holding_conversations(self, user_id: int, conversation_ids: list[str]) -> list[dict[str, Any]]:
        """Every row with a message from one of *conversation_ids* (a message ref
        starts with its conversation id and a "/"), oldest first."""
        cur = self._execute(
            "SELECT * FROM lead_suggestions s"
            " WHERE s.user_id = %s AND EXISTS ("
            "   SELECT 1 FROM jsonb_array_elements(s.messages) AS m, unnest(%s::text[]) AS c(id)"
            "   WHERE left(m->>'external_ref', length(c.id) + 1) = c.id || '/')"
            " ORDER BY s.id",
            (user_id, conversation_ids),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    # ai: claude-opus-5-5 | 2026-11-18
    def with_ref(self, user_id: int, source: str, external_ref: str) -> dict[str, Any] | None:
        cur = self._execute(
            "SELECT * FROM lead_suggestions WHERE user_id = %s AND source = %s AND external_ref = %s",
            (user_id, source, external_ref),
        )
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    # ai: claude-opus-5-5 | 2026-11-18
    def with_counterpart_name(self, user_id: int, name: str) -> list[dict[str, Any]]:
        """Rows whose sender has this name (ignoring case and surrounding spaces)."""
        cur = self._execute(
            "SELECT * FROM lead_suggestions WHERE user_id = %s AND lower(trim(counterpart_name)) = lower(trim(%s))"
            " ORDER BY id",
            (user_id, name),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]

    # ai: claude-opus-5-5 | 2026-11-18 -- from lead_suggestion_person_merge.py's loop
    def fold(self, user_id: int, rows: list[dict[str, Any]], ref: str) -> int:
        """Fold one person's *rows* (full rows, as holding_conversations returns)
        into the one merged_row keeps, under *ref*; the others are deleted. Returns
        the kept row's id."""
        kept, merged = merged_row(rows)
        others = [row["id"] for row in rows if row["id"] != kept["id"]]
        if others:
            # First, so the kept row can take the ref one of them may already hold.
            self._execute("DELETE FROM lead_suggestions WHERE id = ANY(%s) AND user_id = %s", (others, user_id))
        self._execute(
            """
            UPDATE lead_suggestions SET
                external_ref = %s, counterpart_name = %s, counterpart_profile_url = %s,
                conversation_title = %s, first_message_at = %s, last_message_at = %s,
                message_count = %s, messages = %s, imported_at = %s, role_guess = %s,
                hiring_company_guess = %s, recruiter_company_guess = %s, contact_id = %s,
                job_id = %s
            WHERE id = %s AND user_id = %s
            """,
            (
                ref, merged["counterpart_name"], merged["counterpart_profile_url"],
                merged["conversation_title"], merged["first_message_at"], merged["last_message_at"],
                len(merged["messages"]), Json(merged["messages"]), merged["imported_at"],
                merged["role_guess"], merged["hiring_company_guess"], merged["recruiter_company_guess"],
                merged["contact_id"], merged["job_id"], kept["id"], user_id,
            ),
        )
        return int(kept["id"])

    def mark_accepted(
        self, user_id: int, suggestion_id: int, contact_id: int, job_id: int | None, decided_at: str
    ) -> bool:
        cur = self._execute(
            "UPDATE lead_suggestions SET status = 'accepted', contact_id = %s, job_id = %s, decided_at = %s"
            " WHERE id = %s AND user_id = %s AND status <> 'accepted'",
            (contact_id, job_id, decided_at, suggestion_id, user_id),
        )
        return cur.rowcount > 0
