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

from __future__ import annotations

import pytest

from backend.voice_query import (
    VoiceIntentError,
    _parse_tagged_block,
    format_answer_text,
)


def test_valid_tagged_block_is_parsed() -> None:
    raw = '<VOICE_INTENT>{"type": "sql_query", "sql": "SELECT 1"}</VOICE_INTENT>'
    assert _parse_tagged_block(raw) == {"type": "sql_query", "sql": "SELECT 1"}


def test_tagged_block_wrapped_in_prose_is_parsed() -> None:
    raw = (
        'Sure thing! <VOICE_INTENT>{"type": "action", "action": "start_career_scan"}'
        "</VOICE_INTENT> done."
    )
    assert _parse_tagged_block(raw) == {
        "type": "action",
        "action": "start_career_scan",
    }


def test_bare_json_without_tag_falls_back() -> None:
    raw = '{"type": "chat", "reply": "bare json, no tag"}'
    assert _parse_tagged_block(raw) == {"type": "chat", "reply": "bare json, no tag"}


def test_json_in_code_fence_without_tag_falls_back() -> None:
    raw = '```json\n{"type": "chat", "reply": "hi"}\n```'
    assert _parse_tagged_block(raw) == {"type": "chat", "reply": "hi"}


def test_unparseable_reply_raises() -> None:
    with pytest.raises(VoiceIntentError):
        _parse_tagged_block("no json here at all")


def test_missing_tag_and_malformed_json_raises() -> None:
    with pytest.raises(VoiceIntentError):
        _parse_tagged_block('{"type": "chat", "reply": unterminated')


def test_format_answer_single_row_single_column() -> None:
    assert format_answer_text(["n"], [{"n": 7}], False) == "That's 7."


def test_format_answer_single_row_multiple_columns() -> None:
    text = format_answer_text(["company", "n"], [{"company": "Acme", "n": 3}], False)
    assert text == "company: Acme; n: 3."


def test_format_answer_no_rows() -> None:
    assert format_answer_text(["n"], [], False) == "No results."


def test_format_answer_multiple_rows() -> None:
    rows = [{"date_found": "2026-08-01", "n": 2}, {"date_found": "2026-08-02", "n": 5}]
    assert format_answer_text(["date_found", "n"], rows, False) == "Found 2 result(s)."


def test_format_answer_multiple_rows_truncated_notes_it() -> None:
    rows = [{"id": i} for i in range(200)]
    text = format_answer_text(["id"], rows, True)
    assert "200 result(s)" in text
    assert "first 200" in text
