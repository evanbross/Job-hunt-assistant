# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-16
"""One-time migration run by migrate_schema: re-key suggested leads from one row
per LinkedIn conversation to one row per person.

Until 2026-11-16 the messages.csv import (backend/leads/lead_suggestions.py) made a
suggestion per conversation, so a recruiter whose outreach LinkedIn split across
conversations (an InMail plus a message, a pitch sent twice) showed up twice. A
suggestion's external_ref is now linkedin_messages.suggestion_ref() -- the person --
and this folds each person's old rows into one:

- **The kept row** has the strongest status: accepted, then suggested, then
  unclassified, then dismissed, then not_recruiter (a person with one conversation
  still suggested stays visible even if another was dismissed); the most recently
  active wins ties. Its status, verdict and decision time stand.
- **Messages** are merged one per message ref, oldest first; first/last message
  times and the count follow them. The name, profile URL and conversation title are
  the most recently active row's.
- **Guesses and links** (role, hiring and recruiter company, contact, lead) come
  from the kept row, or from the others when it has none.

A folded row's messages reach an accepted person's contact timeline on the next
import, which logs any not-yet-logged message for an accepted row.

Runs as the owner (exempt from row-level security) across every account. Idempotent:
once every row is person-keyed there is nothing left for it to do."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from psycopg2.extras import Json

from backend.leads.linkedin_messages import (
    SUGGESTION_REF_PREFIX,
    SUGGESTION_SOURCE,
    suggestion_ref,
)

logger = logging.getLogger(__name__)

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


def merge_lead_suggestions_by_person(conn: Any) -> int:
    """Run the migration described in the module docstring and commit it. Returns
    how many rows were folded into another."""
    cur = conn.cursor()  # dict rows: backend/db/connection.py's RealDictCursor
    cur.execute(
        "SELECT 1 FROM lead_suggestions WHERE source = %s AND external_ref NOT LIKE %s LIMIT 1",
        (SUGGESTION_SOURCE, f"{SUGGESTION_REF_PREFIX}%"),
    )
    if cur.fetchone() is None:
        return 0
    cur.execute("SELECT * FROM lead_suggestions WHERE source = %s ORDER BY id", (SUGGESTION_SOURCE,))
    people: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in cur.fetchall():
        people[(row["user_id"], suggestion_ref(row["counterpart_profile_url"], row["counterpart_name"]))].append(row)

    folded = 0
    for (_user_id, ref), rows in people.items():
        if len(rows) == 1 and rows[0]["external_ref"] == ref:
            continue
        kept, merged = merged_row(rows)
        others = [row["id"] for row in rows if row["id"] != kept["id"]]
        if others:
            # First, so the kept row can take the ref one of them may already hold.
            cur.execute("DELETE FROM lead_suggestions WHERE id = ANY(%s)", (others,))
            folded += len(others)
        cur.execute(
            """
            UPDATE lead_suggestions SET
                external_ref = %s, counterpart_name = %s, counterpart_profile_url = %s,
                conversation_title = %s, first_message_at = %s, last_message_at = %s,
                message_count = %s, messages = %s, imported_at = %s, role_guess = %s,
                hiring_company_guess = %s, recruiter_company_guess = %s, contact_id = %s,
                job_id = %s
            WHERE id = %s
            """,
            (
                ref, merged["counterpart_name"], merged["counterpart_profile_url"],
                merged["conversation_title"], merged["first_message_at"], merged["last_message_at"],
                len(merged["messages"]), Json(merged["messages"]), merged["imported_at"],
                merged["role_guess"], merged["hiring_company_guess"], merged["recruiter_company_guess"],
                merged["contact_id"], merged["job_id"], kept["id"],
            ),
        )
    conn.commit()
    if folded:
        logger.info("Suggested leads: %d per-conversation row(s) folded into one row per person.", folded)
    return folded


def merged_row(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The row to keep for one person, and the field values it takes."""
    kept = max(rows, key=lambda r: (_STATUS_RANK[r["status"]], r["last_message_at"], r["id"]))
    latest = max(rows, key=lambda r: (r["last_message_at"], r["id"]))
    messages: dict[str, dict[str, Any]] = {}
    for row in rows:
        for message in row["messages"]:
            messages.setdefault(message["external_ref"], message)
    ordered = sorted(messages.values(), key=lambda m: (m["sent_at"], m["external_ref"]))
    merged: dict[str, Any] = {
        "counterpart_name": latest["counterpart_name"],
        "counterpart_profile_url": latest["counterpart_profile_url"],
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
