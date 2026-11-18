# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-17
"""AI suggestions for application-form questions the browser extension's own
rules and saved answers couldn't fill. The extension fills each suggestion in
a distinct "please check" outline, and the person's review (and the answer
review panel) decides what is kept, so nothing here writes to the database.

The model only sees facts the person already gave the app (the structured
application profile minus its demographic fields, their saved answers, and
their candidate profile), and is told to answer null rather than guess.
Voluntary self-identification, consent and signature questions are dropped
before the model ever sees them."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from backend.ai_provider import AIMessage, AIProviderClient, json_object_from_reply
from backend.settings_service import get_setting_bool
from backend.utils.utils import get_repos

logger = logging.getLogger(__name__)

AI_SUGGESTIONS_SETTING = "autofill_ai_suggestions"

# Profile facts the model may use. The demographic fields (gender, race,
# veteran/disability status, pronouns) are deliberately left out: those
# questions are voluntary and never answered by AI.
_PROFILE_FIELDS: tuple[str, ...] = (
    "legal_name",
    "email",
    "phone",
    "street_address",
    "city",
    "state",
    "postal_code",
    "country",
    "work_authorized",
    "requires_visa_sponsorship",
    "is_18_or_older",
    "linkedin_url",
    "portfolio_url",
    "code_repository_url",
    "current_company",
    "previous_employers",
)

# Questions the AI must never answer: voluntary self-identification (the
# person chooses whether to disclose), consent/attestation checkboxes and
# signatures (they're the person's own legal act), and identity numbers.
_NEVER_SUGGEST = re.compile(
    r"\b(gender|sex|sexual orientation|transgender|race|racial|ethnic\w*|hispanic|latin[oax]\w*"
    r"|veteran\w*|disab\w*|pronouns?|signature|sign(ed)? here|e-?sign\w*"
    r"|i (agree|consent|acknowledge|certify|attest|understand)\w*"
    r"|date of birth|birth ?date|social security|ssn)\b",
    re.IGNORECASE,
)

_MAX_CANDIDATE_PROFILE_CHARS = 8000
_MAX_SAVED_ANSWERS = 150
_MAX_FREE_TEXT_ANSWER_CHARS = 500
_MULTI_ANSWER_SEPARATOR = "; "

_SYSTEM_PROMPT = """You fill in job-application form questions for a job candidate, using ONLY the candidate facts provided.

Rules:
- Answer only when the facts clearly support the answer. Otherwise answer null. Never guess or invent experience, years, dates, numbers, salaries, or credentials.
- A question with "options" must be answered with one option's text copied exactly. Only when the question asks to select all that apply may you give several, separated by "; ".
- Answer null for open-ended essay questions (why this company, describe a time when, cover letter, anything wanting a paragraph).
- Free-text answers are short: a word, a number, a URL, or one sentence.
- A saved answer to a differently worded but equivalent question is a fact; reuse it.

Respond with JSON only, in this shape:
{"answers": [{"id": "<question id>", "answer": "<answer>" or null}]}"""


@dataclass(frozen=True)
class FormQuestion:
    id: str
    question: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnswerSuggestion:
    id: str
    answer: str


@dataclass(frozen=True)
class SuggestionResult:
    suggestions: list[AnswerSuggestion]
    disabled: bool = False


def is_never_suggested(question: str) -> bool:
    return bool(_NEVER_SUGGEST.search(question))


def suggest_application_answers(
    user_id: int, questions: list[FormQuestion], page_title: str | None
) -> SuggestionResult:
    """Suggestions for the questions the model could answer from the
    person's facts. Raises AIProviderError if the provider call fails and
    ValueError if its reply isn't usable JSON."""
    askable = [q for q in questions if not is_never_suggested(q.question)]

    with get_repos(user_id) as repos:
        if not get_setting_bool(AI_SUGGESTIONS_SETTING, user_id, repos, True):
            return SuggestionResult(suggestions=[], disabled=True)
        if not askable:
            return SuggestionResult(suggestions=[])
        profile = repos["application_profile"].get(user_id) or {}
        saved_answers = repos["application_answers"].get_all(user_id)[:_MAX_SAVED_ANSWERS]
        candidate_row = repos["candidate_profiles"].get(user_id) or {}
    # The connection is released before the slow provider call.

    candidate_profile = str(candidate_row.get("content") or "").strip()
    prompt = _build_prompt(askable, profile, saved_answers, candidate_profile, page_title)
    with AIProviderClient(user_id) as client:
        raw = client.complete(
            messages=[AIMessage(role="user", content=prompt)],
            system=_SYSTEM_PROMPT,
            max_tokens=min(4000, 300 + 80 * len(askable)),
            json_response=True,
        )
    return SuggestionResult(suggestions=parse_suggestions(raw, askable))


def _build_prompt(
    questions: list[FormQuestion],
    profile: dict[str, Any],
    saved_answers: list[dict[str, Any]],
    candidate_profile: str,
    page_title: str | None,
) -> str:
    facts = {
        field: profile[field]
        for field in _PROFILE_FIELDS
        if profile.get(field) is not None and str(profile[field]).strip() != ""
    }
    saved = [
        {"question": a["question_text"], "answer": a["answer_text"]}
        for a in saved_answers
        if not is_never_suggested(str(a.get("question_text") or ""))
    ]
    question_payload = [
        {"id": q.id, "question": q.question, **({"options": list(q.options)} if q.options else {})}
        for q in questions
    ]
    sections = [
        f"Application page: {page_title.strip()}" if page_title and page_title.strip() else "",
        "Candidate facts (application profile):\n" + json.dumps(facts, indent=1, default=str),
        "Saved answers to earlier application questions:\n" + json.dumps(saved, indent=1),
        "Candidate profile:\n" + (candidate_profile[:_MAX_CANDIDATE_PROFILE_CHARS] or "(none)"),
        "Questions to answer:\n" + json.dumps(question_payload, indent=1),
    ]
    return "\n\n".join(section for section in sections if section)


def parse_suggestions(raw: str, questions: list[FormQuestion]) -> list[AnswerSuggestion]:
    """The model's usable answers. Drops nulls, unknown ids, and any answer to
    a choice question that isn't one of its options (returned in the
    option's own spelling, so the extension can match it exactly)."""
    reply = json_object_from_reply(raw)
    entries = reply.get("answers")
    if not isinstance(entries, list):
        raise ValueError("AI reply had no answers list")

    by_id = {q.id: q for q in questions}
    suggestions: list[AnswerSuggestion] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        question = by_id.get(str(entry.get("id")))
        answer = entry.get("answer")
        if question is None or question.id in seen or not isinstance(answer, str) or not answer.strip():
            continue
        resolved = _resolve_answer(question, answer.strip())
        if resolved is None:
            logger.info("Dropped AI answer %r: not an option of %r", answer, question.question)
            continue
        seen.add(question.id)
        suggestions.append(AnswerSuggestion(id=question.id, answer=resolved))
    return suggestions


def _resolve_answer(question: FormQuestion, answer: str) -> str | None:
    if not question.options:
        return answer if len(answer) <= _MAX_FREE_TEXT_ANSWER_CHARS else None
    by_text = {_option_key(option): option for option in question.options}
    exact = by_text.get(_option_key(answer))
    if exact is not None:
        return exact
    parts = [by_text.get(_option_key(part)) for part in answer.split(";") if part.strip()]
    if len(parts) > 1 and all(part is not None for part in parts):
        return _MULTI_ANSWER_SEPARATOR.join(part for part in parts if part is not None)
    return None


def _option_key(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()
