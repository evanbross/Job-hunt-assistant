# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-03
"""backend/db/keyset_pagination.py's pure parts: cursors and page splitting.
Whether the generated predicates order rows correctly is checked against real
Postgres in tests/test_job_queue_listing.py (every sort, every page size)."""

from __future__ import annotations

import base64
import json

import pytest

from backend.db.keyset_pagination import (
    InvalidCursorError,
    SortKey,
    after_cursor_sql,
    cursor_columns_sql,
    decode_cursor,
    encode_cursor,
    split_keyset_page,
)


def _raw_cursor(payload: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def test_a_cursor_round_trips_including_nulls() -> None:
    token = encode_cursor("job-queue:fit_asc", [None, "2026-10-01", "42"])
    assert "=" not in token  # safe as a bare query-string value
    assert decode_cursor(token, "job-queue:fit_asc", 3) == [None, "2026-10-01", "42"]


@pytest.mark.parametrize(
    "token",
    [
        "%%%",
        base64.urlsafe_b64encode(b"\xff\xfe").decode(),  # not UTF-8 JSON
        _raw_cursor({"listing": "job-history"}),
        _raw_cursor(["job-history"]),
        _raw_cursor(["job-queue:date_desc", ["2026-10-01", "1"]]),  # another listing
        _raw_cursor(["job-history", ["2026-10-01"]]),  # too few values
        _raw_cursor(["job-history", ["2026-10-01", 1]]),  # values are always text
        _raw_cursor(["job-history", "2026-10-01,1"]),
    ],
)
def test_anything_but_a_cursor_for_this_listing_is_refused(token: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(token, "job-history", 2)


def test_split_moves_the_cursor_columns_out_of_the_rows() -> None:
    keys = (SortKey("date_found", descending=True), SortKey("id", descending=True))
    rows = [
        {"id": n, "keyset_cursor_0": f"2026-10-0{n}", "keyset_cursor_1": str(n)} for n in (3, 2, 1)
    ]

    page = split_keyset_page(rows, limit=2, key_count=len(keys))

    assert page.rows == [{"id": 3}, {"id": 2}]
    assert (page.has_more, page.next_after) == (True, ["2026-10-02", "2"])
    last = split_keyset_page([{"id": 1, "keyset_cursor_0": "x", "keyset_cursor_1": "1"}], 2, 2)
    assert (last.has_more, last.next_after) == (False, None)
    empty = split_keyset_page([], 2, 2)
    assert (empty.rows, empty.has_more, empty.next_after) == ([], False, None)


def test_cursor_columns_are_each_key_as_text() -> None:
    keys = (SortKey("lower(label)"), SortKey("id"))
    assert cursor_columns_sql(keys) == "(lower(label))::text AS keyset_cursor_0, (id)::text AS keyset_cursor_1"


@pytest.mark.parametrize(
    ("key", "value", "expected_sql", "expected_params"),
    [
        # Explicit NULLS placement, and Postgres's default (ASC: LAST, DESC: FIRST) when unset.
        (SortKey("fit_score", descending=True, nulls_last=True), "70", "((fit_score) < %s OR (fit_score) IS NULL)", ["70"]),
        (SortKey("fit_score", descending=True, nulls_last=True), None, "FALSE", []),
        (SortKey("date_found", nulls_last=False), "2026-10-01", "(date_found) > %s", ["2026-10-01"]),
        (SortKey("date_found", nulls_last=False), None, "(date_found) IS NOT NULL", []),
        (SortKey("date_found", descending=True), None, "(date_found) IS NOT NULL", []),
        (SortKey("id"), "5", "((id) > %s OR (id) IS NULL)", ["5"]),
    ],
)
def test_after_sql_puts_nulls_where_order_by_does(
    key: SortKey, value: str | None, expected_sql: str, expected_params: list[str]
) -> None:
    assert key.after_sql(value) == (expected_sql, expected_params)


def test_order_sql_always_states_nulls_placement() -> None:
    assert SortKey("date_found", descending=True).order_sql() == "date_found DESC NULLS FIRST"
    assert SortKey("fit_score", nulls_last=True).order_sql() == "fit_score ASC NULLS LAST"


def test_after_cursor_sql_expands_into_tie_breaking_disjuncts() -> None:
    keys = (SortKey("fit_score", descending=True, nulls_last=True), SortKey("id", descending=True))

    sql, params = after_cursor_sql(keys, [None, "7"])

    assert sql == "((FALSE) OR ((fit_score) IS NULL AND (id) < %s))"
    assert params == ["7"]
    with pytest.raises(InvalidCursorError):
        after_cursor_sql(keys, ["1"])
