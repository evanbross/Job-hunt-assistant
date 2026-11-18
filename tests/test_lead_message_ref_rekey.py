# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-18
"""backend/db/lead_message_ref_rekey.py against real Postgres: stored LinkedIn
message refs move from scheme 1 (hashing the sender's profile slug) to
linkedin_messages.message_ref's scheme 2, in suggestions and on contact timelines."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from psycopg2.extras import Json

from backend.contacts.contact_identity import contact_identity_columns
from backend.db import open_repositories
from backend.db.lead_message_ref_rekey import rekey_linkedin_message_refs
from backend.db.schema import migrate_schema
from backend.leads.linkedin_messages import message_ref

_CREATED = "2026-11-16T16:08:40+00:00"


def _scheme_1_ref(conversation_id: str, sent_at: str, sender_key: str, text: str) -> str:
    """How linkedin_messages made a message ref until 2026-11-18."""
    digest = hashlib.sha256("\x1f".join((conversation_id, sent_at, sender_key, text)).encode("utf-8")).hexdigest()[:24]
    return f"{conversation_id}/{digest}"


def _message(conversation_id: str, sent_at: str, text: str, *, from_owner: bool) -> dict[str, Any]:
    sender_key = "pat-seeker" if from_owner else "rita-r"
    return {
        "external_ref": _scheme_1_ref(conversation_id, sent_at, sender_key, text),
        "sent_at": sent_at, "sender_name": "x", "from_owner": from_owner, "subject": "", "text": text,
    }


# A conversation id with a "/" in it, as base64 ids can have.
_CONVERSATION = "2-abc/def=="
_MESSAGES = [
    _message(_CONVERSATION, "2026-11-01T09:00:00+00:00", "Open to a Staff role?", from_owner=False),
    _message(_CONVERSATION, "2026-11-01T10:00:00+00:00", "Tell me more", from_owner=True),
]


def _log(cur: Any, user_id: int, contact_id: int, ref: str, when: str) -> None:
    cur.execute(
        "INSERT INTO interactions (user_id, contact_id, occurred_at, channel, direction, kind, source,"
        " external_ref, created_at) VALUES (%s, %s, %s, 'linkedin', 'inbound', 'message', 'linkedin_import', %s, %s)",
        (user_id, contact_id, when, ref, _CREATED),
    )


@pytest.fixture
def seeded(pg_dsn: str) -> Any:
    repos = open_repositories(pg_dsn)
    conn = repos["conn"]
    user_id = int(repos["user_accounts"].create_pending("rekey@example.com", "seed-rekey", "2099-01-01")["id"])
    contact_id, _ = repos["contacts"].create_manual(
        user_id, contact_identity_columns("Rita Recruiter", "", "", "https://www.linkedin.com/in/rita-r"), {}, _CREATED
    )
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO lead_suggestions
            (user_id, source, external_ref, status, counterpart_name, counterpart_profile_url, contact_id,
             first_message_at, last_message_at, message_count, messages, imported_at)
        VALUES (%s, 'linkedin_messages', 'person:rita-r', 'accepted', 'Rita Recruiter',
                'https://www.linkedin.com/in/rita-r', %s, %s, %s, 2, %s, %s)
        """,
        (user_id, contact_id, _MESSAGES[0]["sent_at"], _MESSAGES[1]["sent_at"], Json(_MESSAGES), _CREATED),
    )
    for message in _MESSAGES:
        _log(cur, user_id, contact_id, message["external_ref"], message["sent_at"])
    # Logged from a message no suggestion holds any more: it can't be recomputed.
    _log(cur, user_id, contact_id, "2-gone/0123456789abcdef01234567", "2026-10-01T09:00:00+00:00")
    conn.commit()
    try:
        yield conn, user_id
    finally:
        conn.close()


def _refs(conn: Any, user_id: int) -> tuple[list[str], list[str]]:
    cur = conn.cursor()
    cur.execute("SELECT messages FROM lead_suggestions WHERE user_id = %s", (user_id,))
    stored = [m["external_ref"] for m in cur.fetchone()["messages"]]
    cur.execute("SELECT external_ref FROM interactions WHERE user_id = %s ORDER BY occurred_at", (user_id,))
    return stored, [row["external_ref"] for row in cur.fetchall()]


def test_refs_move_to_the_direction_based_scheme_everywhere(seeded: Any) -> None:
    conn, user_id = seeded
    expected = [
        message_ref(_CONVERSATION, "2026-11-01T09:00:00+00:00", False, "Open to a Staff role?"),
        message_ref(_CONVERSATION, "2026-11-01T10:00:00+00:00", True, "Tell me more"),
    ]

    assert rekey_linkedin_message_refs(conn) == 2

    stored, logged = _refs(conn, user_id)
    assert stored == expected
    assert logged == ["2-gone/0123456789abcdef01234567", *expected]  # the stranded one keeps its ref


def test_migrate_schema_runs_it_once(seeded: Any) -> None:
    conn, user_id = seeded

    migrate_schema(conn)
    after_first = _refs(conn, user_id)

    assert rekey_linkedin_message_refs(conn) == 0
    assert _refs(conn, user_id) == after_first
