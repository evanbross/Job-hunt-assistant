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

How rows fold is PostgresLeadSuggestionRepository.fold / merged_row (shared with
the import, which folds a captured conversation's row into the person's export row):

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

from backend.db.repositories.lead_suggestions import PostgresLeadSuggestionRepository
from backend.leads.linkedin_messages import (
    SUGGESTION_REF_PREFIX,
    SUGGESTION_SOURCE,
    suggestion_ref,
)

logger = logging.getLogger(__name__)


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
    repo = PostgresLeadSuggestionRepository(conn)
    for (user_id, ref), rows in people.items():
        if len(rows) == 1 and rows[0]["external_ref"] == ref:
            continue
        repo.fold(user_id, rows, ref)  # ai: claude-opus-5-5 | 2026-11-18 -- moved to the repository
        folded += len(rows) - 1
    conn.commit()
    if folded:
        logger.info("Suggested leads: %d per-conversation row(s) folded into one row per person.", folded)
    return folded
