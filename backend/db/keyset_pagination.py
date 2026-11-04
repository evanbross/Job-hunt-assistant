# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-03
"""Keyset ("seek") pagination: each page continues after the previous page's
last row's sort values, instead of skipping OFFSET rows.

OFFSET pagination counts rows from the top on every request, so when a row
above the boundary leaves the listing or moves below it between requests
(re-scored, edited, approved in another tab, dropped out of a filter), every
row after it shifts up one and the next page silently skips one row. A
keyset page asks for "rows that sort after these values" -- it does not care
what happened above them. (code-review-2026-11-02 finding #1.)

A listing describes its order once, as a tuple of SortKey. Both its ORDER BY
and its "after this cursor" predicate are generated from that tuple, so the
two cannot disagree. The last key must make the order total (normally the
primary key), or rows that tie on every key could be skipped or repeated.

The cursor handed to the client is opaque: the last row's sort values as
Postgres itself rendered them (`::text`), so they compare back exactly, plus
the listing's name so a cursor from one listing is refused by another. Values
are only ever bound as parameters.

A leaf module: no imports from other project modules.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

CursorValues = list[str | None]

_CURSOR_COLUMN_PREFIX = "keyset_cursor_"


class InvalidCursorError(ValueError):
    """A cursor that wasn't issued for this listing, or isn't a cursor at all."""


@dataclass(frozen=True)
class SortKey:
    """One ORDER BY term. *expr* is SQL from a module constant -- never request
    input. *nulls_last* None means Postgres's default for the direction
    (ASC: NULLS LAST, DESC: NULLS FIRST)."""

    expr: str
    descending: bool = False
    nulls_last: bool | None = None

    @property
    def _nulls_sort_last(self) -> bool:
        return (not self.descending) if self.nulls_last is None else self.nulls_last

    def order_sql(self) -> str:
        direction = "DESC" if self.descending else "ASC"
        nulls = "LAST" if self._nulls_sort_last else "FIRST"
        return f"{self.expr} {direction} NULLS {nulls}"

    def equal_sql(self, value: str | None) -> tuple[str, list[str]]:
        if value is None:
            return f"({self.expr}) IS NULL", []
        return f"({self.expr}) = %s", [value]

    def after_sql(self, value: str | None) -> tuple[str, list[str]]:
        """Rows strictly after *value* in this key's order."""
        if value is None:
            # Within a NULLS LAST key nothing sorts after a NULL but more NULLs
            # (equal, so decided by a later key); within NULLS FIRST, every non-NULL does.
            return ("FALSE", []) if self._nulls_sort_last else (f"({self.expr}) IS NOT NULL", [])
        comparison = f"({self.expr}) {'<' if self.descending else '>'} %s"
        if self._nulls_sort_last:
            return f"({comparison} OR ({self.expr}) IS NULL)", [value]
        return comparison, [value]


def order_by_sql(keys: Sequence[SortKey]) -> str:
    return ", ".join(key.order_sql() for key in keys)


def cursor_columns_sql(keys: Sequence[SortKey]) -> str:
    """Extra SELECT columns carrying each row's sort values, for its cursor."""
    return ", ".join(
        f"({key.expr})::text AS {_CURSOR_COLUMN_PREFIX}{index}" for index, key in enumerate(keys)
    )


def after_cursor_sql(keys: Sequence[SortKey], values: CursorValues) -> tuple[str, list[str]]:
    """The predicate for rows after the row whose sort values are *values*:
    (k1 after v1) OR (k1 = v1 AND k2 after v2) OR ... -- the expanded form,
    because mixed directions and NULLS placement rule out a row comparison."""
    if len(values) != len(keys):
        raise InvalidCursorError("cursor does not match this listing's sort")
    disjuncts: list[str] = []
    params: list[str] = []
    for index, key in enumerate(keys):
        terms: list[str] = []
        for earlier_key, earlier_value in zip(keys[:index], values[:index], strict=True):
            term, term_params = earlier_key.equal_sql(earlier_value)
            terms.append(term)
            params.extend(term_params)
        term, term_params = key.after_sql(values[index])
        terms.append(term)
        params.extend(term_params)
        disjuncts.append(f"({' AND '.join(terms)})")
    return f"({' OR '.join(disjuncts)})", params


@dataclass(frozen=True)
class KeysetPage:
    rows: list[dict[str, Any]]
    has_more: bool
    # The last row's sort values: where the next page starts. None when there's no next page.
    next_after: CursorValues | None


def split_keyset_page(rows: list[dict[str, Any]], limit: int, key_count: int) -> KeysetPage:
    """*rows* fetched with LIMIT limit + 1 and cursor_columns_sql(): the page,
    whether there is more, and the cursor columns moved out of the rows."""
    page = rows[:limit]
    last_values: CursorValues | None = None
    for row in page:
        last_values = [row.pop(f"{_CURSOR_COLUMN_PREFIX}{index}") for index in range(key_count)]
    has_more = len(rows) > limit
    return KeysetPage(rows=page, has_more=has_more, next_after=last_values if has_more else None)


def encode_cursor(listing: str, values: CursorValues) -> str:
    payload = json.dumps([listing, values], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(token: str, listing: str, key_count: int) -> CursorValues:
    """The values encode_cursor() packed for *listing*; InvalidCursorError for
    anything else (another listing's cursor, a stale sort, garbage)."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidCursorError("malformed cursor") from exc
    if not (isinstance(payload, list) and len(payload) == 2 and payload[0] == listing):
        raise InvalidCursorError("cursor is for a different listing or sort")
    values = payload[1]
    if not (
        isinstance(values, list)
        and len(values) == key_count
        and all(value is None or isinstance(value, str) for value in values)
    ):
        raise InvalidCursorError("cursor does not match this listing's sort")
    return values
