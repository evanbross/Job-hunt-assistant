# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-18
"""The extension's "Capture this conversation" (lead_suggestions.capture_conversation)
against the real schema: a captured LinkedIn thread lands in the suggested-leads
inbox, and it and a messages.csv import of the same conversation -- in either
order -- end up as one suggestion with every message once, though LinkedIn names
the person by member id in one and by vanity name in the other. Every name and
message here is invented."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from backend.leads.lead_suggestions import CaptureOutcome, capture_conversation
from backend.leads.linkedin_messages import CapturedConversation, CapturedMessage
from backend.utils.utils import get_repos
from tests.lead_suggestion_fixtures import (
    ROWS,
    inbox_tab,
    suggestion_named,
    suggestions_client,
    upload,
)
from tests.linkedin_export_fixtures import profile_url

_NOW = "2026-11-18T12:00:00+00:00"


@pytest.fixture
def client_and_user(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, int]:
    return suggestions_client(pg_dsn, monkeypatch)


def _member_url(tag: str) -> str:
    """A made-up linkedin.com/in/<member id> link (LinkedIn's ACoAA... form)."""
    return f"https://www.linkedin.com/in/ACoAA{tag}" + "x" * (34 - len(tag))


def _message(when: str, text: str, *, from_owner: bool = False, subject: str = "") -> CapturedMessage:
    return CapturedMessage(
        # deliveredAt has milliseconds; the export's DATE doesn't.
        sent_at=datetime.fromisoformat(when).replace(microsecond=437_000, tzinfo=UTC),
        from_owner=from_owner,
        sender_name="Pat Seeker" if from_owner else "",
        subject=subject,
        text=text,
    )


# The c-rita conversation of ROWS, as LinkedIn's messaging data has it.
RITA_THREAD = (
    _message("2026-11-01 09:00:00", "Open to a Staff role?"),
    _message("2026-11-01 10:00:00", "Tell me more", from_owner=True),
)
SAM_THREAD = (
    _message("2026-11-04 09:00:00", "We're hiring a Principal", subject="Principal role"),
    _message("2026-11-04 12:00:00", "Interested", from_owner=True),
)


def _capture(user_id: int, conversation_id: str, name: str, url: str, *messages: CapturedMessage) -> CaptureOutcome:
    """What the listener's callback does (web/extension_listener.py)."""
    with get_repos(user_id) as repos:
        outcome = capture_conversation(
            user_id, repos, CapturedConversation(conversation_id, name, url, messages), _NOW
        )
        repos["conn"].commit()
    return outcome


def _all_rows(client: Any) -> list[dict[str, Any]]:
    tabs = ("suggested", "unclassified", "not_recruiter", "dismissed")
    return [row for status in tabs for row in inbox_tab(client, status)["suggestions"]]


def test_a_new_persons_conversation_is_suggested(client_and_user: tuple[Any, int]) -> None:
    client, user_id = client_and_user
    url = _member_url("Cara")

    outcome = _capture(
        user_id, "2-cara", "Cara Capture", url,
        _message("2026-11-10 09:00:00", "Hi! A Staff role?", subject="Staff role"),
        _message("2026-11-10 09:30:00", "Sure", from_owner=True),
    )

    assert (outcome.status, outcome.added, outcome.messages, outcome.messages_logged) == ("suggested", True, 2, 0)
    cara = suggestion_named(client, "Cara Capture")
    assert (cara["id"], cara["reason"], cara["counterpart_profile_url"]) == (
        outcome.suggestion_id, "Captured from LinkedIn", url,
    )
    assert cara["conversation_title"] == "Staff role"
    assert [(m["text"], m["from_owner"]) for m in cara["messages"]] == [("Hi! A Staff role?", False), ("Sure", True)]


def test_a_capture_joins_the_imported_row_of_its_conversation(client_and_user: tuple[Any, int]) -> None:
    client, user_id = client_and_user
    upload(client, ROWS)
    before = len(_all_rows(client))

    outcome = _capture(
        user_id, "c-rita", "Rita Recruiter", _member_url("Rita"),
        *RITA_THREAD, _message("2026-11-12 08:00:00", "Following up"),
    )

    assert (outcome.added, outcome.messages) == (False, 3)
    rita = suggestion_named(client, "Rita Recruiter")  # capturing moved her from unclassified to suggested
    assert rita["external_ref"] == "person:rita-r"
    assert rita["counterpart_profile_url"].rstrip("/") == profile_url("rita-r")  # the vanity link stays
    assert [m["text"] for m in rita["messages"]] == ["Open to a Staff role?", "Tell me more", "Following up"]
    assert len(_all_rows(client)) == before


def test_a_new_conversation_joins_the_person_with_that_name(client_and_user: tuple[Any, int]) -> None:
    client, user_id = client_and_user
    upload(client, ROWS)

    _capture(user_id, "c-rita-2", "Rita Recruiter", _member_url("Rita"), _message("2026-11-12 08:00:00", "New role"))

    rita = suggestion_named(client, "Rita Recruiter")
    assert rita["external_ref"] == "person:rita-r"
    assert [m["text"] for m in rita["messages"]][-1] == "New role"


def test_an_import_after_a_capture_folds_into_one_row_and_logs_nothing_twice(
    client_and_user: tuple[Any, int],
) -> None:
    """Sam's thread is captured and accepted (a contact with his member-id link and
    both messages on his timeline); then an export arrives with the same thread."""
    client, user_id = client_and_user
    member_url = _member_url("Sam")
    captured = _capture(user_id, "c-sam", "Sam Stranger", member_url, *SAM_THREAD)
    accepted = client.post(
        f"/api/lead-suggestions/{captured.suggestion_id}/accept",
        json={"lead": "none", "contact_company": "Agency Co", "contact_position": "Recruiter"},
    ).get_json()
    assert accepted["messages_logged"] == 2

    imported = upload(client, ROWS).get_json()

    assert imported["messages_logged"] == 0
    rows = [row for row in _all_rows(client) if row["counterpart_name"] == "Sam Stranger"]
    assert rows == []  # still one row, and it's accepted (not an inbox tab)
    contacts = client.get("/api/contacts").get_json()["contacts"]
    sam = next(c for c in contacts if c["id"] == accepted["contact_id"])
    assert sam["profile_url"] == profile_url("sam-s")  # the export's vanity link replaced the member id
    activity = client.get(f"/api/contacts/{sam['id']}/activity").get_json()
    assert len(activity["interactions"]) == 2
    with get_repos(user_id) as repos:
        [row] = repos["lead_suggestions"].holding_conversations(user_id, ["c-sam"])
    assert (row["external_ref"], row["status"], row["message_count"]) == ("person:sam-s", "accepted", 2)


def test_capturing_an_accepted_person_logs_only_the_new_messages(client_and_user: tuple[Any, int]) -> None:
    client, user_id = client_and_user
    upload(client, ROWS)
    rita = suggestion_named(client, "Rita Recruiter", "unclassified")
    accepted = client.post(
        f"/api/lead-suggestions/{rita['id']}/accept",
        json={"lead": "none", "contact_company": "Agency Co", "contact_position": "Recruiter"},
    ).get_json()

    outcome = _capture(
        user_id, "c-rita", "Rita Recruiter", _member_url("Rita"),
        *RITA_THREAD, _message("2026-11-12 08:00:00", "Following up"),
    )

    assert (outcome.status, outcome.messages_logged) == ("accepted", 1)
    activity = client.get(f"/api/contacts/{accepted['contact_id']}/activity").get_json()
    assert sorted(i["occurred_at"] for i in activity["interactions"])[-1] == "2026-11-12T08:00:00+00:00"
    assert len(activity["interactions"]) == 3
