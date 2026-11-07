# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-07
"""Generated-SQL shapes that try to switch the row-level-security identity
(`app.current_user_id`) to another account mid-query -- the voice-query attack in
docs/cloud-deployment-readiness-plan.md 2.6. backend/sql_guard.py is the only layer
that stops these: the read-only role and the READ ONLY transaction both still allow
set_config(), and on a managed Postgres it can't be revoked from that role.

Shared by tests/test_sql_guard.py (each must be rejected) and
tests/test_row_level_security.py (each, sent through POST /api/voice/query against
real Postgres, must return none of the other account's rows). `{target}` is the
other account's user id."""

from __future__ import annotations

GUC = "'app.current_user_id'"

IDENTITY_SWITCH_SQL: tuple[str, ...] = (
    # set_config() in each clause a function call can appear in
    f"SELECT set_config({GUC}, '{{target}}', false)",
    f"SELECT url FROM jobs WHERE set_config({GUC}, '{{target}}', false) IS NOT NULL",
    f"SELECT url FROM jobs, (SELECT set_config({GUC}, '{{target}}', false)) AS s",
    f"SELECT url FROM jobs WHERE id IN"
    f" (SELECT id FROM jobs WHERE set_config({GUC}, '{{target}}', true) = '{{target}}')",
    f"SELECT url FROM jobs WHERE EXISTS (SELECT set_config({GUC}, '{{target}}', false))",
    f"SELECT url FROM jobs ORDER BY set_config({GUC}, '{{target}}', false)",
    f"SELECT company FROM jobs GROUP BY company HAVING set_config({GUC}, '{{target}}', false) <> ''",
    f"SELECT COUNT(*) FILTER (WHERE set_config({GUC}, '{{target}}', false) = '{{target}}') FROM jobs",
    f"SELECT url FROM jobs UNION SELECT set_config({GUC}, '{{target}}', false)",
    f"SELECT url FROM jobs WHERE url = ANY(ARRAY[set_config({GUC}, '{{target}}', false)])",
    f"SELECT CAST(set_config({GUC}, '{{target}}', false) AS INT)",
    f"SELECT COALESCE(set_config({GUC}, '{{target}}', false), 'x')",
    f"SELECT url FROM set_config({GUC}, '{{target}}', false) AS jobs(url)",
    # spellings of the function name
    f"SELECT pg_catalog.set_config({GUC}, '{{target}}', false)",
    f"SELECT \"set_config\"({GUC}, '{{target}}', false)",
    f"SELECT SET_CONFIG({GUC}, '{{target}}', false)",
    # statements rather than a function call
    "SET app.current_user_id = '{target}'",
    "SET LOCAL app.current_user_id = '{target}'",
    "RESET app.current_user_id",
    "RESET ALL",
    "SET ROLE NONE",
    "DISCARD ALL",
    f"DO $$BEGIN PERFORM set_config({GUC}, '{{target}}', false); END$$",
    # a valid SELECT stacked after the switch (psycopg2 runs both and returns the last)
    f"SELECT set_config({GUC}, '{{target}}', false); SELECT url FROM jobs",
    "SET app.current_user_id = '{target}'; SELECT url FROM jobs",
)


def identity_switch_sql(target_user_id: int) -> list[str]:
    """IDENTITY_SWITCH_SQL with the other account's id filled in."""
    return [sql.format(target=target_user_id) for sql in IDENTITY_SWITCH_SQL]
