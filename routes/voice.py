# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Voice-query blueprint: POST /voice/query takes a transcribed utterance and
returns a validated read-only SQL query's results, a triggered action's
result, or a chat fallback. See backend/voice/voice_query.py for intent
classification, backend/sql_guard.py for SQL validation, and
backend/db/readonly_role.py for the SELECT-only DB role this executes
against."""

from __future__ import annotations

import logging

import psycopg2
import psycopg2.extras
from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from backend.db.readonly_role import get_readonly_database_url
from backend.db.row_level_security import set_current_user
from backend.sql_guard import MAX_ROW_LIMIT, SqlGuardError, validate_select
from backend.utils.utils import (
    ai_error_http_status,
    current_user_id,
    invalid_request_response,
    safe_error_message,
)
from backend.voice.voice_actions import VOICE_ACTIONS
from backend.voice.voice_query import VoiceIntentError, classify, format_answer_text

logger = logging.getLogger(__name__)

voice_bp = Blueprint("voice", __name__)

_MAX_QUERY_TEXT_LENGTH = 500
_STATEMENT_TIMEOUT_MS = 5000


# ai: claude-sonnet-5 | 2026-09-29
class VoiceQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    text: str = ""

    @field_validator("text")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text is required")
        if len(value) > _MAX_QUERY_TEXT_LENGTH:
            raise ValueError("text is too long")
        return value


@voice_bp.route("/voice/query", methods=["POST"])
def voice_query() -> ResponseReturnValue:
    try:
        body = VoiceQueryRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return invalid_request_response(exc)
    text = body.text

    try:
        intent = classify(text, current_user_id())
    except VoiceIntentError as exc:
        logger.warning("Voice query intent unparseable: %s", exc)
        return jsonify(
            {
                "intent": "chat",
                "answer_text": "I didn't quite catch that -- could you rephrase?",
            }
        )
    except Exception as exc:
        logger.warning("Voice query classification failed: %s", exc, exc_info=True)
        return jsonify(
            {"error": safe_error_message(exc, "Voice query failed")}
        ), ai_error_http_status(exc)

    if intent["type"] == "chat":
        return jsonify({"intent": "chat", "answer_text": intent["reply"]})
    if intent["type"] == "action":
        return _handle_action(intent["action"])
    return _handle_sql_query(intent["sql"])


def _handle_action(action_name: str) -> ResponseReturnValue:
    action = VOICE_ACTIONS.get(action_name)
    if action is None:
        return jsonify(
            {
                "intent": "action",
                "action": action_name,
                "error": "That action isn't recognized.",
            }
        ), 422

    result, status = action.handler()
    return jsonify(
        {
            "intent": "action",
            "action": action_name,
            "action_result": result,
            "action_http_status": status,
            "answer_text": _format_action_answer(action_name, result),
        }
    ), status


def _format_action_answer(action_name: str, result: dict) -> str:
    if "error" in result:
        return str(result["error"])
    if action_name == "start_career_scan":
        return "Starting a scan of your job boards now."
    if action_name == "mark_ghosts":
        count = int(result.get("count") or 0)
        if count:
            return f"Marked {count} application{'s' if count != 1 else ''} as ghosted."
        return "No applications needed to be marked ghosted."
    return "Done."


def _handle_sql_query(sql: str) -> ResponseReturnValue:
    try:
        validated_sql = validate_select(sql)
    except SqlGuardError as exc:
        # SQL is shown even on rejection -- transparency into what was
        # attempted matters as much as transparency into what ran.
        return jsonify({"intent": "sql_query", "sql": sql, "error": str(exc)}), 422

    try:
        columns, rows, truncated = execute_readonly(validated_sql, current_user_id())
    except psycopg2.Error as exc:
        logger.warning("Voice query SQL execution failed: %s", exc, exc_info=True)
        return jsonify(
            {
                "intent": "sql_query",
                "sql": validated_sql,
                "error": "That query couldn't be run.",
            }
        ), 422

    return jsonify(
        {
            "intent": "sql_query",
            "sql": validated_sql,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "answer_text": format_answer_text(columns, rows, truncated),
        }
    )


# ai: claude-opus-5-5 | 2026-11-07 -- public (was _execute_readonly): the RLS tests drive it directly
def execute_readonly(sql: str, user_id: int) -> tuple[list[str], list[dict], bool]:
    """Runs `sql` (already validated by backend.sql_guard) against the
    SELECT-only jobhunt_readonly role, in its own short-lived connection --
    not the pooled/shared connection get_repos() uses. SET TRANSACTION READ
    ONLY is redundant with the role's grants but cheap and independent
    defense (belt-and-suspenders per the plan's three-layer design).

    The LLM-generated SQL is never told about, and never asked to filter by, `user_id`
    (backend/sql_guard.py's ALLOWED_COLUMNS excludes it) -- row-level security is what
    actually confines every query to the caller's own rows, by the same mechanism
    get_repos() uses for the rest of the app (backend/db/row_level_security.py)."""
    conn = psycopg2.connect(
        get_readonly_database_url(), cursor_factory=psycopg2.extras.RealDictCursor
    )
    try:
        cur = conn.cursor()
        # SET TRANSACTION must be the transaction's first statement -- set_current_user
        # below runs a query (SELECT set_config(...)) and would otherwise disqualify it.
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT_MS}ms'")
        set_current_user(cur, user_id)
        cur.execute(sql)
        columns = [desc.name for desc in cur.description] if cur.description else []
        rows = [dict(row) for row in cur.fetchmany(MAX_ROW_LIMIT + 1)]
        truncated = len(rows) > MAX_ROW_LIMIT
        if truncated:
            rows = rows[:MAX_ROW_LIMIT]
        return columns, rows, truncated
    finally:
        conn.rollback()
        conn.close()
