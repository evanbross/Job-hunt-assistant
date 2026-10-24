# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-10-23
"""normalize_company_name_sql() must compute exactly what
normalize_company_name() does, on real Postgres -- the Job Queue's
"Know someone" filter and Company view grouping run it in SQL, while the
contacts it matches against were keyed by the Python version at import time."""

from __future__ import annotations

import psycopg2

from backend.scoring.company_matching import (
    normalize_company_name,
    normalize_company_name_sql,
)

_NAMES: list[str | None] = [
    None,
    "",
    "   ",
    "Acme, Inc.",
    "ACME INC",
    "Acme Incorporated",
    "Coinbase",
    "Co-op Co.",
    "Coca-Cola Company",
    "The Home Depot Corp.",
    "Seeq Corporation",
    "Unknown Company",
    "Inc.",
    "foo_inc",
    "Ltd Ltd. LLC",
    "AT&T",
    "Société Générale Inc",
    "3M Co",
    "co",
    "Cohere",
    "Block (Cash App), LLC",
    "  Spaced   Out  Ltd  ",
]


def test_sql_normalizer_matches_python(pg_dsn: str) -> None:
    mismatches = []
    with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
        for name in _NAMES:
            cur.execute(f"SELECT {normalize_company_name_sql('%s')}", (name,))
            sql_value = cur.fetchone()[0]
            if sql_value != normalize_company_name(name):
                mismatches.append((name, sql_value, normalize_company_name(name)))
    assert mismatches == []
