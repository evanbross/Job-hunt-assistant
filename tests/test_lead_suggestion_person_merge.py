# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-16
"""backend/db/lead_suggestion_person_merge.py against real Postgres: suggested leads
stored one per conversation are folded into one per person."""

from __future__ import annotations

from typing import Any

import pytest
from psycopg2.extras import Json

from backend.db import open_repositories
from backend.db.lead_suggestion_person_merge import merge_lead_suggestions_by_person
from backend.db.schema import migrate_schema


def _message(ref: str, sent_at: str, text: str, *, from_owner: bool = False) -> dict[str, Any]:
    return {"external_ref": ref, "sent_at": sent_at, "sender_name": "x", "from_owner": from_owner, "subject": "", "text": text}


def _suggestion(cur: Any, user_id: int, conversation_id: str, name: str, slug: str, status: str,
                messages: list[dict[str, Any]], **fields: Any) -> int:
    """A row as the import stored it before 2026-11-16: keyed by conversation."""
    cur.execute(
        """
        INSERT INTO lead_suggestions
            (user_id, source, external_ref, status, counterpart_name, counterpart_profile_url,
             first_message_at, last_message_at, message_count, messages, imported_at,
             conversation_title, role_guess, hiring_company_guess, decided_at)
        VALUES (%s, 'linkedin_messages', %s, %s, %s, %s, %s, %s, %s, %s, '2026-11-16T16:08:40+00:00',
                %s, %s, %s, %s)
        RETURNING id
        """,
        (
            user_id, conversation_id, status, name, f"https://www.linkedin.com/in/{slug}",
            messages[0]["sent_at"], messages[-1]["sent_at"], len(messages), Json(messages),
            fields.get("conversation_title"), fields.get("role_guess"), fields.get("hiring_company_guess"),
            fields.get("decided_at"),
        ),
    )
    return int(cur.fetchone()["id"])


def _rows(conn: Any) -> dict[tuple[int, str], dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM lead_suggestions ORDER BY id")
    return {(r["user_id"], r["external_ref"]): r for r in cur.fetchall()}


@pytest.fixture
def seeded(pg_dsn: str) -> Any:
    repos = open_repositories(pg_dsn)
    conn = repos["conn"]
    user_a = int(repos["user_accounts"].create_pending("sugg-a@example.com", "seed-sugg-a", "2099-01-01")["id"])
    user_b = int(repos["user_accounts"].create_pending("sugg-b@example.com", "seed-sugg-b", "2099-01-01")["id"])
    cur = conn.cursor()
    ids = {
        # One recruiter, two conversations (a message, then the same pitch as an InMail).
        "sam_message": _suggestion(
            cur, user_a, "c-sam-1", "Sam Stranger", "sam-s", "suggested",
            [_message("c-sam-1/a", "2026-11-04T09:00:00+00:00", "We're hiring"),
             _message("c-sam-1/b", "2026-11-04T12:00:00+00:00", "Interested", from_owner=True)],
            conversation_title="Principal role", role_guess="Principal Engineer",
        ),
        "sam_inmail": _suggestion(
            cur, user_a, "c-sam-2", "Sam S. Stranger", "sam-s", "dismissed",
            [_message("c-sam-2/a", "2026-11-06T09:00:00+00:00", "Same role, via InMail")],
            conversation_title="Principal role (InMail)", hiring_company_guess="Acme",
            decided_at="2026-11-16T17:00:00+00:00",
        ),
        "rita": _suggestion(
            cur, user_a, "c-rita", "Rita Recruiter", "rita-r", "unclassified",
            [_message("c-rita/a", "2026-11-01T09:00:00+00:00", "Hi")],
        ),
        # The same profile under another account is that account's own person.
        "sam_other_user": _suggestion(
            cur, user_b, "c-sam-b", "Sam Stranger", "sam-s", "suggested",
            [_message("c-sam-b/a", "2026-11-02T09:00:00+00:00", "Hello")],
        ),
    }
    conn.commit()
    try:
        yield conn, user_a, user_b, ids
    finally:
        conn.close()


def test_a_persons_rows_fold_into_one_keyed_by_the_person(seeded: Any) -> None:
    conn, user_a, user_b, ids = seeded

    assert merge_lead_suggestions_by_person(conn) == 1

    rows = _rows(conn)
    assert set(rows) == {(user_a, "person:sam-s"), (user_a, "person:rita-r"), (user_b, "person:sam-s")}
    sam = rows[(user_a, "person:sam-s")]
    # The stronger status's row is kept, with its own verdict and decision.
    assert (sam["id"], sam["status"], sam["decided_at"]) == (ids["sam_message"], "suggested", None)
    assert [m["text"] for m in sam["messages"]] == ["We're hiring", "Interested", "Same role, via InMail"]
    assert (sam["message_count"], sam["first_message_at"], sam["last_message_at"]) == (
        3, "2026-11-04T09:00:00+00:00", "2026-11-06T09:00:00+00:00",
    )
    # Name and title follow the most recently active conversation; guesses fall back.
    assert (sam["counterpart_name"], sam["conversation_title"]) == ("Sam S. Stranger", "Principal role (InMail)")
    assert (sam["role_guess"], sam["hiring_company_guess"]) == ("Principal Engineer", "Acme")
    assert rows[(user_a, "person:rita-r")]["id"] == ids["rita"]
    assert rows[(user_b, "person:sam-s")]["id"] == ids["sam_other_user"]


def test_migrate_schema_runs_it_once(seeded: Any) -> None:
    conn, _user_a, _user_b, _ids = seeded

    migrate_schema(conn)
    after_first = _rows(conn)
    assert all(ref.startswith("person:") for _user, ref in after_first)
    assert merge_lead_suggestions_by_person(conn) == 0
    assert _rows(conn) == after_first
