# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Call-notes import (recruiter lead tracking, phase 2): read a pasted or uploaded
transcript / AI note-taker summary, keep it verbatim as a transcript interaction,
and ask the AI provider for a *proposal* of lead updates -- role, company, comp,
next steps, a follow-up date. Nothing here writes a proposal anywhere: the user
reviews it and applies what they accept through the ordinary lead routes
(confirm-first, like the candidate-profile file sync)."""

from __future__ import annotations

import html
import io
import json
import logging
import re
import zipfile
from datetime import date
from pathlib import PurePath

import docx
from docx.opc.exceptions import OpcError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.ai_provider import AIMessage, AIProviderClient

logger = logging.getLogger(__name__)

# ai: claude-opus-5-5 | 2026-11-11
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".txt", ".md", ".vtt", ".docx"})
MAX_UPLOAD_BYTES = 2_000_000
# Stored verbatim (as an interaction body), so held to the rich-text limit's order.
MAX_NOTES_CHARS = 150_000
# A long call's transcript is ~10k words; more than this is trimmed before the AI sees it.
_MAX_AI_INPUT_CHARS = 60_000
_MAX_LIST_ITEMS = 12
_MAX_ITEM_CHARS = 300

_VTT_TIMING_RE = re.compile(r"^\s*(\d{1,2}:)?\d{2}:\d{2}[.,]\d{3}\s+-->\s+")
_VTT_CUE_NUMBER_RE = re.compile(r"^\s*\d+\s*$")
_VTT_VOICE_TAG_RE = re.compile(r"<v\s+([^>]+)>")
_VTT_TAG_RE = re.compile(r"</?[^>]+>")


class CallNotesFormatError(ValueError):
    """An upload this importer can't read (wrong type, too big, not text)."""


# ai: claude-opus-5-5 | 2026-11-11
def read_call_notes_upload(filename: str, raw: bytes) -> str:
    """The plain text of an uploaded notes file. .vtt captions lose their cue
    numbers and timings (speaker names are kept as "Name: line"); .docx is read
    paragraph by paragraph."""
    extension = PurePath(filename or "").suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise CallNotesFormatError(
            f"Unsupported file type; use one of {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    if len(raw) > MAX_UPLOAD_BYTES:
        raise CallNotesFormatError(f"File is larger than {MAX_UPLOAD_BYTES // 1_000_000} MB")
    if extension == ".docx":
        try:
            document = docx.Document(io.BytesIO(raw))
        # A corrupt or renamed file is a BadZipFile; a zip that isn't a Word
        # package is an OpcError (PackageNotFoundError).
        except (zipfile.BadZipFile, OpcError, KeyError, ValueError) as exc:
            raise CallNotesFormatError("Could not read that Word document") from exc
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CallNotesFormatError("The file isn't UTF-8 text") from exc
        if extension == ".vtt":
            text = _vtt_to_text(text)
    return normalize_notes_text(text)


def _vtt_to_text(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if (
            not stripped
            or stripped.upper().startswith("WEBVTT")
            or stripped.startswith(("NOTE", "STYLE", "REGION"))
            or _VTT_TIMING_RE.match(stripped)
            or _VTT_CUE_NUMBER_RE.match(stripped)
        ):
            continue
        speaker = _VTT_VOICE_TAG_RE.search(stripped)
        spoken = _VTT_TAG_RE.sub("", stripped).strip()
        lines.append(f"{speaker.group(1).strip()}: {spoken}" if speaker else spoken)
    return "\n".join(lines)


def normalize_notes_text(text: str) -> str:
    """Trim, collapse runs of blank lines, and enforce MAX_NOTES_CHARS."""
    collapsed = re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n")).strip()
    if not collapsed:
        raise CallNotesFormatError("There's no text in those notes")
    if len(collapsed) > MAX_NOTES_CHARS:
        raise CallNotesFormatError(f"Notes are limited to {MAX_NOTES_CHARS} characters")
    return collapsed


def notes_to_html(text: str) -> str:
    """Verbatim notes as escaped HTML paragraphs (blank line = new paragraph,
    single line break kept), for an interaction's body_html."""
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    return "".join(
        "<p>" + "<br>".join(html.escape(line) for line in p.split("\n")) + "</p>" for p in paragraphs
    )


# ai: claude-opus-5-5 | 2026-11-11
class CallNotesProposal(BaseModel):
    """What the AI read out of the notes. Every field is optional: absent means the
    notes didn't say. Validated here because it is a third-party response."""

    model_config = ConfigDict(extra="ignore")

    job_title: str | None = Field(default=None, max_length=_MAX_ITEM_CHARS)
    hiring_company: str | None = Field(default=None, max_length=_MAX_ITEM_CHARS)
    compensation: str | None = Field(default=None, max_length=_MAX_ITEM_CHARS)
    location: str | None = Field(default=None, max_length=_MAX_ITEM_CHARS)
    interview_steps: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    recruiter_requests: list[str] = Field(default_factory=list)
    summary_bullets: list[str] = Field(default_factory=list)
    one_line_summary: str | None = Field(default=None, max_length=_MAX_ITEM_CHARS)
    next_follow_up: str | None = None

    @field_validator("job_title", "hiring_company", "compensation", "location", "one_line_summary", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value[:_MAX_ITEM_CHARS] or None
        return value

    @field_validator("interview_steps", "next_steps", "recruiter_requests", "summary_bullets", mode="before")
    @classmethod
    def _clean_list(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        items = [str(item).strip()[:_MAX_ITEM_CHARS] for item in value if str(item).strip()]
        return items[:_MAX_LIST_ITEMS]

    @field_validator("next_follow_up", mode="before")
    @classmethod
    def _iso_date_or_none(cls, value: object) -> str | None:
        """A date the model couldn't pin down is dropped, not guessed at."""
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return date.fromisoformat(value.strip()[:10]).isoformat()
        except ValueError:
            return None


# ai: claude-opus-5-5 | 2026-11-11
_SYSTEM_PROMPT = (
    "You read notes from a job seeker's call or message thread with a recruiter and "
    "extract facts about the role. Use only what the notes state; never invent "
    "details. Respond with one JSON object and nothing else."
)


def _extraction_prompt(notes: str, lead_context: dict[str, str | None], today: date) -> str:
    known = "\n".join(f"- {key}: {value or 'unknown'}" for key, value in lead_context.items())
    return (
        f"Today is {today.isoformat()}. What the job seeker already has on this lead:\n{known}\n\n"
        "From the notes below, return a JSON object with these keys (use null or [] when "
        "the notes don't say):\n"
        '- "job_title": the role\'s title\n'
        '- "hiring_company": the company hiring (not the recruiting agency)\n'
        '- "compensation": pay or rate as stated, e.g. "90/hr W2" or "$180-200k base"\n'
        '- "location": location / remote / hybrid arrangement\n'
        '- "interview_steps": list of interview steps described\n'
        '- "next_steps": list of agreed next steps, each with who does it\n'
        '- "recruiter_requests": list of things the recruiter asked the job seeker to send or do\n'
        '- "summary_bullets": 3-6 short bullets worth keeping about the role and call\n'
        '- "one_line_summary": one line (under 120 characters) describing the call\n'
        '- "next_follow_up": the date (YYYY-MM-DD) the job seeker should next follow up, '
        "only if the notes give or clearly imply one (resolve relative dates against today); "
        "otherwise null\n\n"
        f"Notes:\n<<<\n{notes[:_MAX_AI_INPUT_CHARS]}\n>>>"
    )


def _json_object(raw: str) -> dict[str, object]:
    """The JSON object in a model reply, tolerating a code fence or leading prose."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("AI reply contained no JSON object") from None
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"AI reply was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("AI reply was not a JSON object")
    return parsed


# ai: claude-opus-5-5 | 2026-11-11
def extract_call_notes(
    user_id: int, notes: str, lead_context: dict[str, str | None], today: date
) -> CallNotesProposal:
    """Ask the user's AI provider for a proposal. Call with no DB connection held
    (PYTHON_STANDARDS: no pooled connection across slow external calls). Raises
    AIProviderError for a provider failure and ValueError for an unusable reply."""
    with AIProviderClient(user_id) as client:
        raw = client.complete(
            messages=[AIMessage(role="user", content=_extraction_prompt(notes, lead_context, today))],
            system=_SYSTEM_PROMPT,
            max_tokens=1500,
            json_response=True,
        )
    try:
        return CallNotesProposal.model_validate(_json_object(raw))
    except ValidationError as exc:
        logger.warning("Call-notes extraction reply failed validation: %s", exc)
        raise ValueError("AI reply didn't have the expected fields") from exc
