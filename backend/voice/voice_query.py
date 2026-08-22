# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# LICENSE: Personal Use Only
# This source code is made available for personal, non-commercial use only.
# You may read, study, and run this code for personal purposes.
# You may NOT:
#   - Use this code, in whole or in part, in any commercial product or service
#   - Sell, sublicense, or otherwise monetize this code or derivatives of it
#   - Redistribute this code without explicit written permission from ZombieTech LLC
#
# No warranty is provided, express or implied. Use at your own risk.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Natural-language -> intent classification for the voice-query feature
(routes/voice.py). One AIProviderClient call per utterance, reused unchanged
from backend/ai_provider.py -- no new AI plumbing.

The model never sees query results; it only classifies intent and, for a
reporting question, writes SQL. Turning rows into a spoken sentence is done
deterministically by format_answer_text() below, not by a second LLM call --
that removes a whole class of read-back hallucination risk and keeps this
simple. Neither backend/sql_guard.py's validation nor SQL execution happens
in this module; classify() only decides what the user meant."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from backend.ai_provider import AIMessage, AIProviderClient
from backend.voice.voice_actions import VOICE_ACTIONS

logger = logging.getLogger(__name__)

_TAG = "VOICE_INTENT"
_TAG_RE = re.compile(rf"<{_TAG}>(.*?)</{_TAG}>", re.DOTALL)

_SCHEMA_SUMMARY = """
Tables/views you may query (read-only):
- jobs(id, status, company, job_title, fit_score, url, source, date_found, on_sheet,
  duplicate_hint, duplicate_of_id, skip_reason, salary_min, salary_max)
  status is one of: pending_review (in the job queue awaiting review), triage,
  possible_duplicate, applied, skipped.
- application_stages(id, job_id, status, date, notes, created_at) -- one row per
  application-stage transition. status is one of: applied, waiting_call,
  waiting_second, eval_1, eval_2, eval_3, rejected, job_filled, req_closed, ghosted.
- stage_statuses(key, label, sort_order, ghost_eligible, is_terminal) -- lookup
  table describing the application_stages.status values above.
- latest_application_stage(job_id, latest_status, latest_status_date, notes,
  created_at) -- a view giving each job's MOST RECENT stage only. Always use this
  view, not a hand-written window query, for "latest stage" / "should this be
  marked ghosted" questions.
No other tables are queryable. Do not use WITH/CTEs, SELECT *, or any function
other than standard aggregates (COUNT, SUM, AVG, MIN, MAX) and date functions
(DATE_TRUNC, CURRENT_DATE, NOW, EXTRACT) -- name the columns you need instead.
""".strip()

_FEW_SHOT = f"""
Examples:
Q: How many jobs are in triage?
<{_TAG}>{{"type": "sql_query", "sql": "SELECT COUNT(*) AS n FROM jobs WHERE status = 'triage'"}}</{_TAG}>

Q: How many are waiting for review?
<{_TAG}>{{"type": "sql_query", "sql": "SELECT COUNT(*) AS n FROM jobs WHERE status = 'pending_review'"}}</{_TAG}>

Q: How many applications are already marked ghosted?
<{_TAG}>{{"type": "sql_query", "sql": "SELECT COUNT(*) AS n FROM application_stages WHERE status = 'ghosted'"}}</{_TAG}>

Q: How many should be marked ghosted but haven't been yet?
<{_TAG}>{{"type": "sql_query", "sql": "SELECT COUNT(*) AS n FROM latest_application_stage WHERE latest_status = 'applied' AND latest_status_date::date <= CURRENT_DATE - 30"}}</{_TAG}>

Q: Has the number of new jobs found gone up recently?
<{_TAG}>{{"type": "sql_query", "sql": "SELECT date_found, COUNT(*) AS n FROM jobs WHERE date_found >= (CURRENT_DATE - INTERVAL '7 days')::text GROUP BY date_found ORDER BY date_found"}}</{_TAG}>

Q: Start scanning job boards.
<{_TAG}>{{"type": "action", "action": "start_career_scan"}}</{_TAG}>

Q: What's the weather like?
<{_TAG}>{{"type": "chat", "reply": "I can only answer questions about your job pipeline, or start a job-board scan."}}</{_TAG}>
""".strip()


class VoiceIntentError(RuntimeError):
    """The model's reply couldn't be parsed into a usable intent."""


def classify(text: str, user_id: int) -> dict[str, Any]:
    """Classify `text` into one of:
    - {"type": "sql_query", "sql": str}
    - {"type": "action", "action": str}
    - {"type": "chat", "reply": str}
    Raises VoiceIntentError if the model's reply can't be parsed at all.
    Doesn't validate the sql/action values themselves -- that's
    backend/sql_guard.py and VOICE_ACTIONS's job, in routes/voice.py.
    """
    raw = AIProviderClient(user_id).complete(
        system=_build_system_prompt(),
        messages=[AIMessage(role="user", content=text)],
        max_tokens=400,
        json_response=True,
    )
    parsed = _parse_tagged_block(raw)
    intent_type = parsed.get("type")

    if intent_type == "sql_query" and isinstance(parsed.get("sql"), str):
        return {"type": "sql_query", "sql": parsed["sql"]}
    if intent_type == "action" and isinstance(parsed.get("action"), str):
        return {"type": "action", "action": parsed["action"]}
    if intent_type == "chat":
        reply = str(parsed.get("reply") or "").strip()
        return {"type": "chat", "reply": reply or "I'm not sure how to help with that."}
    raise VoiceIntentError(f"Model returned an unrecognized intent: {parsed!r}")


def format_answer_text(columns: list[str], rows: list[dict], truncated: bool) -> str:
    """Deterministic (no LLM call) formatter turning query results into a
    short spoken/display sentence."""
    if not rows:
        return "No results."
    if len(rows) == 1 and len(columns) == 1:
        return f"That's {rows[0][columns[0]]}."
    if len(rows) == 1:
        return "; ".join(f"{col}: {rows[0][col]}" for col in columns) + "."
    suffix = " (showing the first 200)" if truncated else ""
    return f"Found {len(rows)} result(s){suffix}."


def _build_system_prompt() -> str:
    actions = "\n".join(
        f"- {name}: {action.description}" for name, action in VOICE_ACTIONS.items()
    )
    return (
        "You are a voice assistant for a personal job-search tracking app. The "
        "user will ask a spoken question or give a spoken command. Decide "
        "whether it's:\n"
        "1. A reporting question answerable by a single read-only SQL SELECT "
        "against the schema below, or\n"
        "2. A command to run one of the actions below, or\n"
        "3. Neither (chit-chat, unsupported, or unclear) -- reply "
        "conversationally instead.\n\n"
        f"{_SCHEMA_SUMMARY}\n\n"
        f"Actions you may trigger:\n{actions}\n\n"
        "Respond with exactly one structured block and nothing else "
        "(no other text before or after it):\n"
        f'<{_TAG}>{{"type": "sql_query", "sql": "<a single SELECT statement>"}}</{_TAG}>\n'
        f'<{_TAG}>{{"type": "action", "action": "<one of the action names above>"}}</{_TAG}>\n'
        f'<{_TAG}>{{"type": "chat", "reply": "<a short spoken reply>"}}</{_TAG}>\n\n'
        f"{_FEW_SHOT}"
    )


def _parse_tagged_block(raw: str) -> dict[str, Any]:
    match = _TAG_RE.search(raw)
    candidate = match.group(1).strip() if match else raw.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Model may ignore the "nothing else" instruction and wrap the block
        # in prose/a code fence -- same defensive extraction fit_scorer.py
        # already uses, since neither Anthropic nor OpenAI guarantee JSON
        # mode here, and Ollama's format="json" isn't reliably honored by a
        # small local model either.
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, flags=re.DOTALL)
        if fence:
            candidate = fence.group(1).strip()
        else:
            brace = candidate.find("{")
            if brace >= 0:
                candidate = candidate[brace:]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise VoiceIntentError(
                f"Could not parse the model's reply: {raw[:300]!r}"
            ) from exc
