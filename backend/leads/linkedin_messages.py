# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Reads the messages.csv in a LinkedIn data export (Settings > Data privacy > Get a
copy of your data) and picks out the conversations worth checking for recruiter
outreach (recruiter lead tracking, phase 2). Pure: no database, no AI.

The export has no "this is you" column, so the account owner is the participant in
the most conversations -- every conversation in the file includes them. A
conversation is a candidate when someone else started it one-to-one, it isn't in
the spam folder, and it was active recently; everything else is counted by reason
and dropped before any AI sees it. A person's candidate conversations are merged
into one candidate (one suggestion per person, not per conversation).

Format checked against a real export (2026-09): columns CONVERSATION ID,
CONVERSATION TITLE, FROM, SENDER PROFILE URL, TO, RECIPIENT PROFILE URLS
(comma-separated), DATE ("YYYY-MM-DD HH:MM:SS UTC"), SUBJECT, CONTENT (sometimes
HTML), FOLDER (INBOX / ARCHIVE / SPAM / blank), ATTACHMENTS. Sender URLs are
linkedin.com/in/<vanity> links, the same form Connections.csv uses. Sponsored and
system messages have no sender URL."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePath

from backend.contacts.contact_identity import linkedin_profile_slug
from backend.utils.rich_text import html_to_plain_text

# ai: claude-opus-5-5 | 2026-11-11
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".csv", ".zip"})
# Must stay under deploy/Caddyfile's request_body max_size (16MB), or production
# refuses a bigger upload with a bare 413 before this message can explain it. A
# real export zip (with ~3k messages) was 0.7 MB; messages.csv alone 1.9 MB.
MAX_UPLOAD_BYTES = 15_000_000
# messages.csv once unzipped (not limited by the front door, only by memory).
MAX_MESSAGES_CSV_BYTES = 60_000_000
# The export's other *messages.csv files (learning_coach_messages.csv,
# guide_messages.csv, ...) are LinkedIn's own assistants, not people.
MESSAGES_MEMBER_NAME = "messages.csv"
_REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {"CONVERSATION ID", "FROM", "SENDER PROFILE URL", "RECIPIENT PROFILE URLS", "DATE", "CONTENT"}
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
# Bounds what one stored message (and so one suggestion row) can hold.
MAX_MESSAGE_CHARS = 20_000

# Why a conversation was not a candidate, as reported back to the user.
SKIP_SPAM = "spam"
SKIP_STARTED_BY_YOU = "started_by_you"
SKIP_GROUP = "group"
SKIP_NO_PROFILE = "no_profile"
SKIP_TOO_OLD = "too_old"

# ai: claude-opus-5-5 | 2026-11-16 -- lead_suggestions.source for these rows (here, not in
# lead_suggestions.py, so the DB migration can use it without importing the AI provider)
SUGGESTION_SOURCE = "linkedin_messages"
# Marks a person-keyed suggestion (suggestion_ref).
SUGGESTION_REF_PREFIX = "person:"


class MessagesFormatError(ValueError):
    """An upload this importer can't read."""


# ai: claude-opus-5-5 | 2026-11-11
@dataclass(frozen=True)
class ExportMessage:
    external_ref: str
    sent_at: str  # UTC ISO 8601, seconds precision
    sender_name: str
    from_owner: bool
    subject: str
    text: str

    def as_json(self) -> dict[str, object]:
        return {
            "external_ref": self.external_ref,
            "sent_at": self.sent_at,
            "sender_name": self.sender_name,
            "from_owner": self.from_owner,
            "subject": self.subject,
            "text": self.text,
        }


# ai: claude-opus-5-5 | 2026-11-16
def suggestion_ref(profile_url: str | None, name: str) -> str:
    """A suggestion's key: the person, not the conversation. LinkedIn splits one
    person's outreach across conversations (an InMail and a message, a pitch sent
    twice), and those are one suggestion. Also how the 2026-11-16 migration
    (backend/db/lead_suggestion_person_merge.py) re-keys rows stored per conversation."""
    key = _participant_key(profile_url, name)
    if key is None:
        raise ValueError("a suggestion needs a profile URL or a name")
    return f"{SUGGESTION_REF_PREFIX}{key}"


# ai: claude-opus-5-5 | 2026-11-16 -- was one per conversation (CandidateConversation)
@dataclass(frozen=True)
class SuggestionCandidate:
    """Everyone's candidate conversations with one person, merged."""

    ref: str  # suggestion_ref()
    title: str  # the most recently active conversation's
    counterpart_name: str
    counterpart_profile_url: str | None
    conversation_ids: tuple[str, ...]
    messages: tuple[ExportMessage, ...]  # oldest first, across the conversations

    @property
    def first_message_at(self) -> str:
        return self.messages[0].sent_at

    @property
    def last_message_at(self) -> str:
        return self.messages[-1].sent_at


# ai: claude-opus-5-5 | 2026-11-11
@dataclass
class ExportScan:
    candidates: list[SuggestionCandidate]
    conversation_count: int
    skipped: Counter[str] = field(default_factory=Counter)
    unreadable_rows: int = 0


# ai: claude-opus-5-5 | 2026-11-11
def read_messages_upload(filename: str, raw: bytes) -> str:
    """The text of messages.csv, from the file itself or from the whole export zip."""
    extension = PurePath(filename or "").suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise MessagesFormatError("Upload messages.csv or the export's .zip file")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise MessagesFormatError(
            f"File is larger than {MAX_UPLOAD_BYTES // 1_000_000} MB; upload messages.csv from inside the zip instead"
        )
    if extension == ".zip":
        raw = _messages_csv_from_zip(raw)
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MessagesFormatError("messages.csv isn't UTF-8 text") from exc


def _messages_csv_from_zip(raw: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = [
                info
                for info in archive.infolist()
                if PurePath(info.filename).name.lower() == MESSAGES_MEMBER_NAME
            ]
            if not members:
                raise MessagesFormatError("That zip has no messages.csv")
            # The declared size can lie (a zip bomb), so read one byte past the
            # limit rather than trusting it.
            with archive.open(members[0]) as member:
                data = member.read(MAX_MESSAGES_CSV_BYTES + 1)
    except zipfile.BadZipFile as exc:
        raise MessagesFormatError("Could not read that zip file") from exc
    if len(data) > MAX_MESSAGES_CSV_BYTES:
        raise MessagesFormatError(f"messages.csv is larger than {MAX_MESSAGES_CSV_BYTES // 1_000_000} MB")
    return data


# ai: claude-opus-5-5 | 2026-11-11
@dataclass(frozen=True)
class _Row:
    conversation_id: str
    title: str
    sender_name: str
    sender_url: str | None
    sender_key: str
    recipient_keys: frozenset[str]
    sent_at: datetime
    subject: str
    text: str
    folder: str


def _participant_key(profile_url: str | None, name: str) -> str | None:
    """Who a message is from or to: their profile slug, or for a sender with no
    profile URL (sponsored and system messages) their display name."""
    slug = linkedin_profile_slug(profile_url)
    if slug:
        return slug
    name = name.strip().lower()
    return f"name:{name}" if name else None


def _message_text(content: str) -> str:
    content = content.strip()
    if _HTML_TAG_RE.search(content):
        content = html_to_plain_text(content)
    return content[:MAX_MESSAGE_CHARS]


def _parse_rows(text: str) -> tuple[list[_Row], int]:
    try:
        reader = csv.DictReader(io.StringIO(text))
        columns = set(reader.fieldnames or [])
        missing = _REQUIRED_COLUMNS - columns
        if missing:
            raise MessagesFormatError(
                "That isn't LinkedIn's messages.csv (missing " + ", ".join(sorted(missing)) + ")"
            )
        rows: list[_Row] = []
        unreadable = 0
        for raw in reader:
            conversation_id = (raw.get("CONVERSATION ID") or "").strip()
            sender_name = (raw.get("FROM") or "").strip()
            sender_url = (raw.get("SENDER PROFILE URL") or "").strip() or None
            sender_key = _participant_key(sender_url, sender_name)
            try:
                sent_at = datetime.strptime((raw.get("DATE") or "")[:19], _DATE_FORMAT).replace(tzinfo=UTC)
            except ValueError:
                sent_at = None
            if not conversation_id or sender_key is None or sent_at is None:
                unreadable += 1
                continue
            recipient_keys = {
                key
                for url in (raw.get("RECIPIENT PROFILE URLS") or "").split(",")
                if (key := linkedin_profile_slug(url.strip())) is not None
            }
            rows.append(
                _Row(
                    conversation_id=conversation_id,
                    title=(raw.get("CONVERSATION TITLE") or "").strip(),
                    sender_name=sender_name,
                    sender_url=sender_url,
                    sender_key=sender_key,
                    recipient_keys=frozenset(recipient_keys),
                    sent_at=sent_at,
                    subject=(raw.get("SUBJECT") or "").strip(),
                    text=_message_text(raw.get("CONTENT") or ""),
                    folder=(raw.get("FOLDER") or "").strip().upper(),
                )
            )
    except csv.Error as exc:
        raise MessagesFormatError(f"messages.csv couldn't be read as CSV: {exc}") from exc
    return rows, unreadable


def _owner_key(conversations: dict[str, list[_Row]]) -> str:
    appearances: Counter[str] = Counter()
    for rows in conversations.values():
        participants = {row.sender_key for row in rows}
        for row in rows:
            participants |= row.recipient_keys
        appearances.update(participants)
    ranked = appearances.most_common(2)
    if not ranked or (len(ranked) == 2 and ranked[0][1] == ranked[1][1]):
        raise MessagesFormatError(
            "Couldn't tell which participant is you -- the file needs more than one conversation"
        )
    return ranked[0][0]


def _external_ref(conversation_id: str, row: _Row) -> str:
    """Stable across re-exports: the export has no message id, so a message is
    its conversation, time, sender and text."""
    digest = hashlib.sha256(
        "\x1f".join((conversation_id, row.sent_at.isoformat(), row.sender_key, row.text)).encode("utf-8")
    ).hexdigest()[:24]
    return f"{conversation_id}/{digest}"


# ai: claude-opus-5-5 | 2026-11-11
def scan_export(text: str, active_since: datetime | None) -> ExportScan:
    """Group messages.csv into conversations and keep the candidates: started by
    someone else, one-to-one, not spam, with a sender profile, and (when
    *active_since* is given) with a message on or after it."""
    rows, unreadable = _parse_rows(text)
    conversations: dict[str, list[_Row]] = defaultdict(list)
    for row in rows:
        conversations[row.conversation_id].append(row)
    scan = ExportScan(candidates=[], conversation_count=len(conversations), unreadable_rows=unreadable)
    if not conversations:
        return scan
    owner = _owner_key(conversations)

    by_person: dict[str, list[tuple[str, list[_Row]]]] = defaultdict(list)
    for conversation_id, convo_rows in conversations.items():
        convo_rows.sort(key=lambda r: r.sent_at)
        first = convo_rows[0]
        others = {r.sender_key for r in convo_rows} | {k for r in convo_rows for k in r.recipient_keys}
        others.discard(owner)
        if any(r.folder == "SPAM" for r in convo_rows):
            scan.skipped[SKIP_SPAM] += 1
        elif first.sender_key == owner:
            scan.skipped[SKIP_STARTED_BY_YOU] += 1
        elif first.sender_url is None:
            scan.skipped[SKIP_NO_PROFILE] += 1
        elif len(others) != 1:
            scan.skipped[SKIP_GROUP] += 1
        elif active_since is not None and convo_rows[-1].sent_at < active_since:
            scan.skipped[SKIP_TOO_OLD] += 1
        else:
            by_person[first.sender_key].append((conversation_id, convo_rows))  # ai: claude-opus-5-5 | 2026-11-16
    scan.candidates = [_candidate(person, owner) for person in by_person.values()]
    scan.candidates.sort(key=lambda c: c.last_message_at, reverse=True)
    return scan


# ai: claude-opus-5-5 | 2026-11-16 -- one candidate per person, from all their conversations
def _candidate(person: list[tuple[str, list[_Row]]], owner: str) -> SuggestionCandidate:
    """`person` is one sender's candidate conversations, each sorted oldest first."""
    by_activity = sorted(person, key=lambda convo: (convo[1][-1].sent_at, convo[0]))
    latest = by_activity[-1][1][0]  # the most recently active conversation's opening message
    messages = sorted(
        (
            ExportMessage(
                external_ref=_external_ref(conversation_id, row),
                sent_at=row.sent_at.isoformat(timespec="seconds"),
                sender_name=row.sender_name,
                from_owner=row.sender_key == owner,
                subject=row.subject,
                text=row.text,
            )
            for conversation_id, rows in by_activity
            for row in rows
        ),
        key=lambda m: (m.sent_at, m.external_ref),
    )
    return SuggestionCandidate(
        ref=suggestion_ref(latest.sender_url, latest.sender_name),
        title=latest.title,
        counterpart_name=latest.sender_name or "Unknown",
        counterpart_profile_url=latest.sender_url,
        conversation_ids=tuple(conversation_id for conversation_id, _rows in by_activity),
        messages=tuple(messages),
    )
