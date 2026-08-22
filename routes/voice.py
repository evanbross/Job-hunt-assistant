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

from backend.db.readonly_role import get_readonly_database_url
from backend.sql_guard import MAX_ROW_LIMIT, SqlGuardError, validate_select
from backend.utils.utils import ai_error_http_status, current_user_id, safe_error_message
from backend.voice.voice_actions import VOICE_ACTIONS
from backend.voice.voice_query import VoiceIntentError, classify, format_answer_text

logger = logging.getLogger(__name__)

voice_bp = Blueprint("voice", __name__)

_MAX_QUERY_TEXT_LENGTH = 500
_STATEMENT_TIMEOUT_MS = 5000


@voice_bp.route("/voice/query", methods=["POST"])
def voice_query() -> ResponseReturnValue:
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    if len(text) > _MAX_QUERY_TEXT_LENGTH:
        return jsonify({"error": "text is too long"}), 400

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
        columns, rows, truncated = _execute_readonly(validated_sql)
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


def _execute_readonly(sql: str) -> tuple[list[str], list[dict], bool]:
    """Runs `sql` (already validated by backend.sql_guard) against the
    SELECT-only jobhunt_readonly role, in its own short-lived connection --
    not the pooled/shared connection get_repos() uses. SET TRANSACTION READ
    ONLY is redundant with the role's grants but cheap and independent
    defense (belt-and-suspenders per the plan's three-layer design)."""
    conn = psycopg2.connect(
        get_readonly_database_url(), cursor_factory=psycopg2.extras.RealDictCursor
    )
    try:
        cur = conn.cursor()
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT_MS}ms'")
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
