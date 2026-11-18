# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-11
"""backend/leads/linkedin_messages.py: reading LinkedIn's messages.csv and picking
the conversations worth checking for recruiters. The fixture follows the real
export's columns and value formats (checked 2026-11-11 against a real export);
every name and message in it is invented."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.leads import linkedin_messages
from backend.leads.linkedin_messages import (
    SKIP_GROUP,
    SKIP_NO_PROFILE,
    SKIP_SPAM,
    SKIP_STARTED_BY_YOU,
    SKIP_TOO_OLD,
    CapturedConversation,
    CapturedMessage,
    MessagesFormatError,
    captured_candidate,
    conversation_of,
    is_current_message_ref,
    message_ref,
    read_messages_upload,
    scan_export,
)
from tests.linkedin_export_fixtures import (
    ME,
    message_row,
    messages_csv,
    profile_url,
    zip_of,
)

ROWS = [
    # A recruiter's InMail (HTML content) the user answered: the candidate.
    message_row("c-recruiter", "Rita Recruiter", profile_url("rita-r"), ME, "2026-11-01 09:00:00",
         "<p>Hi Pat,</p><p>A <b>Staff</b> role &amp; more</p>", subject="Staff role"),
    message_row("c-recruiter", "Pat Seeker", ME, profile_url("rita-r"), "2026-11-01 10:30:00", "Sure, call me"),
    # Started by the user.
    message_row("c-mine", "Pat Seeker", ME, profile_url("old-friend"), "2026-11-02 09:00:00", "Hey!"),
    message_row("c-mine", "Old Friend", profile_url("old-friend"), ME, "2026-11-02 09:05:00", "Hi back"),
    # Spam folder.
    message_row("c-spam", "Spammer", profile_url("spam-co"), ME, "2026-11-03 09:00:00", "Buy now", folder="SPAM"),
    # Sponsored message: no sender profile.
    message_row("c-ad", "Some Brand", "", ME, "2026-11-03 09:00:00", "Webinar"),
    # Group conversation.
    message_row("c-group", "Gil Group", profile_url("gil"), f"{ME},{profile_url('third')}", "2026-11-04 09:00:00", "Hi all"),
    # Inbound, but quiet since 2025.
    message_row("c-old", "Olive Older", profile_url("olive"), ME, "2025-01-04 09:00:00", "Hello"),
]


def test_scan_keeps_inbound_one_to_one_recent_conversations() -> None:
    scan = scan_export(messages_csv(ROWS), datetime(2026, 1, 1, tzinfo=UTC))

    assert scan.conversation_count == 6
    assert dict(scan.skipped) == {
        SKIP_STARTED_BY_YOU: 1, SKIP_SPAM: 1, SKIP_NO_PROFILE: 1, SKIP_GROUP: 1, SKIP_TOO_OLD: 1,
    }
    [candidate] = scan.candidates
    assert (candidate.ref, candidate.counterpart_name) == ("person:rita-r", "Rita Recruiter")
    assert candidate.conversation_ids == ("c-recruiter",)
    assert candidate.counterpart_profile_url == profile_url("rita-r")
    assert (candidate.first_message_at, candidate.last_message_at) == (
        "2026-11-01T09:00:00+00:00", "2026-11-01T10:30:00+00:00",
    )
    first, reply = candidate.messages
    assert (first.from_owner, reply.from_owner) == (False, True)
    assert first.text == "Hi Pat,\nA Staff role & more"  # HTML content becomes plain text
    assert first.subject == "Staff role"


def test_no_cutoff_keeps_old_conversations_too() -> None:
    scan = scan_export(messages_csv(ROWS), None)
    assert {c.conversation_ids for c in scan.candidates} == {("c-recruiter",), ("c-old",)}


# ai: claude-opus-5-5 | 2026-11-16
def test_one_persons_conversations_are_one_candidate() -> None:
    """LinkedIn can split one person's outreach (an InMail and a message) across
    conversations; they are one candidate, messages interleaved oldest first, named
    and titled after the most recently active conversation."""
    rows = [
        *ROWS,
        message_row("c-recruiter-2", "Rita R. Recruiter", profile_url("rita-r"), ME, "2026-11-01 09:30:00",
                    "Same role, via InMail", title="InMail"),
    ]

    scan = scan_export(messages_csv(rows), datetime(2026, 1, 1, tzinfo=UTC))

    [rita] = scan.candidates
    assert rita.ref == "person:rita-r"
    assert rita.conversation_ids == ("c-recruiter-2", "c-recruiter")  # least recently active first
    assert [m.text for m in rita.messages] == ["Hi Pat,\nA Staff role & more", "Same role, via InMail", "Sure, call me"]
    assert (rita.counterpart_name, rita.title) == ("Rita Recruiter", "")
    assert len({m.external_ref for m in rita.messages}) == 3


def test_a_suggestion_ref_falls_back_to_the_name_without_a_profile() -> None:
    assert linkedin_messages.suggestion_ref(profile_url("rita-r") + "/", "Rita") == "person:rita-r"
    assert linkedin_messages.suggestion_ref(None, " Some Brand ") == "person:name:some brand"
    with pytest.raises(ValueError):
        linkedin_messages.suggestion_ref(None, " ")


def test_message_refs_are_stable_across_exports() -> None:
    """A re-export must produce the same refs, or re-importing would log every message twice."""
    first = scan_export(messages_csv(ROWS), None).candidates
    again = scan_export(messages_csv(list(reversed(ROWS))), None).candidates
    refs = sorted(m.external_ref for c in first for m in c.messages)
    assert refs == sorted(m.external_ref for c in again for m in c.messages)
    assert len(set(refs)) == len(refs)


def test_rows_without_a_date_or_sender_are_counted_not_fatal() -> None:
    rows = [*ROWS, message_row("c-recruiter", "", "", ME, "not a date", "x")]
    assert scan_export(messages_csv(rows), None).unreadable_rows == 1


def test_a_single_conversation_cannot_say_who_the_owner_is() -> None:
    with pytest.raises(MessagesFormatError, match="which participant is you"):
        scan_export(messages_csv(ROWS[:2]), None)


def test_a_csv_that_is_not_messages_csv_is_rejected() -> None:
    with pytest.raises(MessagesFormatError, match="missing"):
        scan_export("First Name,Last Name\nA,B\n", None)


def test_the_export_zip_is_read_for_messages_csv_only() -> None:
    """The real export also holds learning_coach_messages.csv and similar files."""
    raw = zip_of({"learning_coach_messages.csv": "nope", "messages.csv": messages_csv(ROWS), "Connections.csv": "x"})
    assert read_messages_upload("Basic_LinkedInDataExport.zip", raw) == messages_csv(ROWS)


@pytest.mark.parametrize(
    ("filename", "raw", "message"),
    [
        ("notes.txt", b"x", "Upload messages.csv"),
        ("export.zip", b"not a zip", "Could not read that zip"),
        ("export.zip", zip_of({"guide_messages.csv": "x"}), "no messages.csv"),
        ("messages.csv", b"\xff\xfe\x00bad", "isn't UTF-8"),
    ],
)
def test_unreadable_uploads_are_refused(filename: str, raw: bytes, message: str) -> None:
    with pytest.raises(MessagesFormatError, match=message):
        read_messages_upload(filename, raw)


def test_an_oversized_member_is_refused_without_reading_it_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linkedin_messages, "MAX_MESSAGES_CSV_BYTES", 100)
    with pytest.raises(MessagesFormatError, match="larger than"):
        read_messages_upload("export.zip", zip_of({"messages.csv": "x" * 101}))


# --- captured conversations (the extension's "Capture this conversation") ------------
# ai: claude-opus-5-5 | 2026-11-18

# A made-up member id in LinkedIn's linkedin.com/in/<member id> form.
RITA_MEMBER_URL = "https://www.linkedin.com/in/ACoAAB1rItAx" + "x" * 27


def _capture(*messages: CapturedMessage, conversation_id: str = "c-recruiter") -> CapturedConversation:
    return CapturedConversation(
        conversation_id=conversation_id,
        counterpart_name="Rita Recruiter",
        counterpart_profile_url=RITA_MEMBER_URL,
        messages=messages,
    )


def _captured(sent_at: datetime, text: str, *, from_owner: bool = False, subject: str = "Staff role") -> CapturedMessage:
    return CapturedMessage(
        sent_at=sent_at,
        from_owner=from_owner,
        sender_name="Pat Seeker" if from_owner else "Rita Recruiter",
        subject=subject,
        text=text,
    )


def test_a_capture_and_an_export_give_the_same_message_the_same_ref() -> None:
    """The contract checked live 2026-11-18: the export's DATE is LinkedIn's
    deliveredAt cut to whole seconds, its CONTENT the message text -- but the sender
    is a vanity link in one and a member-id link in the other. The refs must agree
    anyway, or capturing and then importing would log every message twice."""
    [exported] = scan_export(messages_csv(ROWS), None).candidates[:1]
    capture = _capture(
        _captured(datetime(2026, 11, 1, 10, 30, 0, 640_000, tzinfo=UTC), "Sure, call me ", from_owner=True, subject=""),
        _captured(datetime(2026, 11, 1, 9, 0, 0, 120_000, tzinfo=UTC), "Hi Pat,\nA Staff role & more"),
    )

    captured = captured_candidate(capture)

    assert [m.external_ref for m in captured.messages] == [m.external_ref for m in exported.messages]
    assert [m.sent_at for m in captured.messages] == ["2026-11-01T09:00:00+00:00", "2026-11-01T10:30:00+00:00"]
    assert captured.ref == "person:" + RITA_MEMBER_URL.rsplit("/", 1)[1].lower()
    assert (captured.counterpart_profile_url, captured.conversation_ids) == (RITA_MEMBER_URL, ("c-recruiter",))
    assert captured.title == "Staff role"


def test_a_capture_holding_a_message_twice_keeps_it_once() -> None:
    message = _captured(datetime(2026, 11, 1, 9, 0, tzinfo=UTC), "Hello")
    assert len(captured_candidate(_capture(message, message)).messages) == 1


def test_message_refs_name_their_conversation_and_scheme() -> None:
    ref = message_ref("2-abc/def==", "2026-11-01T09:00:00+00:00", False, "Hi")
    assert conversation_of(ref) == "2-abc/def=="  # base64 ids can hold a "/"
    assert is_current_message_ref(ref)
    assert not is_current_message_ref("2-abc/def==/0123456789abcdef01234567")  # scheme 1
    # Direction is part of the identity; the sender's spelling isn't.
    assert ref != message_ref("2-abc/def==", "2026-11-01T09:00:00+00:00", True, "Hi")
