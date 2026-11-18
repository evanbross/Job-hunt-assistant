# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-18
"""One-time migration run by migrate_schema: move LinkedIn message refs to
linkedin_messages.message_ref's scheme 2.

Until 2026-11-18 a message's ref hashed its sender's profile slug, as the
messages.csv export spells it (linkedin.com/in/<vanity>). The extension's "Capture
this conversation" reads LinkedIn's messaging data, which only has the member-id
form of the same URL, so scheme 2 hashes the message's direction instead. Every
stored ref is recomputed from what the row already holds -- the conversation id
(the ref's prefix), sent_at, from_owner and text:

- **lead_suggestions.messages**: each message's external_ref (a row never holds two
  messages with the same new ref; if two collapse into one, the first is kept).
- **interactions** logged from those messages (source 'linkedin_import'): their
  external_ref moves with the message's, so the next import or capture recognises
  them. An interaction whose message isn't in any suggestion keeps its old ref and
  is counted in the log line.

Runs as the owner (exempt from row-level security) across every account.
Idempotent: once no suggestion holds a scheme 1 ref there is nothing left for it to do."""

from __future__ import annotations

import logging
from typing import Any

from psycopg2.extras import Json, execute_values

from backend.leads.linkedin_messages import (
    LOGGED_MESSAGE_SOURCE,
    MESSAGE_REF_SCHEME,
    SUGGESTION_SOURCE,
    conversation_of,
    is_current_message_ref,
    message_ref,
)

logger = logging.getLogger(__name__)

_CURRENT_MARK = f"%/{MESSAGE_REF_SCHEME}:%"


def rekey_linkedin_message_refs(conn: Any) -> int:
    """Run the migration described in the module docstring and commit it. Returns
    how many message refs changed."""
    cur = conn.cursor()  # dict rows: backend/db/connection.py's RealDictCursor
    # Interactions only move with their suggestion's messages, in the same commit,
    # so once those are all scheme 2 there is nothing left -- a stranded
    # interaction must not make every start-up rescan.
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM lead_suggestions s, jsonb_array_elements(s.messages) AS m
            WHERE s.source = %s AND m->>'external_ref' NOT LIKE %s
        ) AS pending
        """,
        (SUGGESTION_SOURCE, _CURRENT_MARK),
    )
    if not cur.fetchone()["pending"]:
        return 0

    cur.execute("SELECT id, user_id, messages FROM lead_suggestions WHERE source = %s ORDER BY id", (SUGGESTION_SOURCE,))
    new_refs: dict[tuple[int, str], str] = {}
    changed = 0
    for row in cur.fetchall():
        messages: dict[str, dict[str, Any]] = {}
        for message in row["messages"]:
            old = message["external_ref"]
            new = old if is_current_message_ref(old) else message_ref(
                conversation_of(old), message["sent_at"], bool(message["from_owner"]), message["text"]
            )
            if new != old:
                new_refs[(row["user_id"], old)] = new
                changed += 1
            messages.setdefault(new, {**message, "external_ref": new})
        ordered = sorted(messages.values(), key=lambda m: (m["sent_at"], m["external_ref"]))
        cur.execute(
            "UPDATE lead_suggestions SET messages = %s, message_count = %s WHERE id = %s",
            (Json(ordered), len(ordered), row["id"]),
        )

    if new_refs:
        # Skip a move onto a ref the same user already has (the unique index on
        # interactions (user_id, source, external_ref)). execute_values takes one
        # %s (the VALUES list), so the source -- a module constant -- is inlined.
        execute_values(
            cur,
            f"""
            UPDATE interactions AS i SET external_ref = v.new_ref
            FROM (VALUES %s) AS v(user_id, old_ref, new_ref)
            WHERE i.user_id = v.user_id AND i.source = '{LOGGED_MESSAGE_SOURCE}' AND i.external_ref = v.old_ref
              AND NOT EXISTS (
                  SELECT 1 FROM interactions o
                  WHERE o.user_id = v.user_id AND o.source = '{LOGGED_MESSAGE_SOURCE}'
                    AND o.external_ref = v.new_ref)
            """,
            [(user_id, old, new) for (user_id, old), new in new_refs.items()],
        )
    cur.execute(
        "SELECT COUNT(*) AS n FROM interactions WHERE source = %s AND external_ref IS NOT NULL AND external_ref NOT LIKE %s",
        (LOGGED_MESSAGE_SOURCE, _CURRENT_MARK),
    )
    stranded = int(cur.fetchone()["n"])
    conn.commit()
    logger.info(
        "LinkedIn message refs: %d moved to scheme %s; %d logged interaction(s) kept an old ref.",
        changed, MESSAGE_REF_SCHEME, stranded,
    )
    return changed
