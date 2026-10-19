# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

import pytest

from backend.sql_guard import MAX_ROW_LIMIT, SqlGuardError, validate_select


def test_simple_count_query_passes() -> None:
    result = validate_select("SELECT COUNT(*) AS n FROM jobs WHERE status = 'triage'")
    assert "SELECT COUNT(*) AS n FROM jobs" in result
    assert f"LIMIT {MAX_ROW_LIMIT + 1}" in result


def test_latest_application_stage_view_query_passes() -> None:
    result = validate_select(
        "SELECT COUNT(*) FROM latest_application_stage "
        "WHERE latest_status = 'applied' AND latest_status_date::date <= CURRENT_DATE - 30"
    )
    assert "latest_application_stage" in result


def test_lower_function_is_allowed() -> None:
    result = validate_select("SELECT LOWER(company) FROM jobs WHERE status = 'triage'")
    assert "LOWER" in result.upper()


def test_explicit_small_limit_is_preserved_unchanged() -> None:
    result = validate_select("SELECT id FROM jobs WHERE status = 'triage' LIMIT 10")
    assert "LIMIT 10" in result


def test_explicit_oversized_limit_is_capped() -> None:
    result = validate_select("SELECT id FROM jobs LIMIT 999999")
    assert f"LIMIT {MAX_ROW_LIMIT + 1}" in result
    assert "999999" not in result


def test_missing_limit_gets_capped_at_max_plus_one() -> None:
    result = validate_select("SELECT id FROM jobs WHERE status = 'triage'")
    assert f"LIMIT {MAX_ROW_LIMIT + 1}" in result


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE jobs;",
        "SELECT 1 -- ; DROP TABLE jobs\n; DROP TABLE jobs;",
    ],
)
def test_statement_stacking_is_rejected(sql: str) -> None:
    with pytest.raises(SqlGuardError):
        validate_select(sql)


@pytest.mark.parametrize(
    "sql",
    ["SELECT * FROM jobs", "SELECT jobs.* FROM jobs"],
)
def test_wildcard_projection_is_rejected(sql: str) -> None:
    with pytest.raises(SqlGuardError, match="SELECT \\*"):
        validate_select(sql)


def test_count_star_is_allowed_despite_wildcard_rejection() -> None:
    validate_select("SELECT COUNT(*) FROM jobs")


@pytest.mark.parametrize("table", ["resume", "contacts", "user_settings", "email_sources"])
def test_disallowed_table_is_rejected(table: str) -> None:
    with pytest.raises(SqlGuardError, match="isn't a table"):
        validate_select(f"SELECT id FROM {table}")


def test_disallowed_column_is_rejected() -> None:
    with pytest.raises(SqlGuardError, match="isn't a column"):
        validate_select("SELECT raw_jd_snippet FROM jobs")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_sleep(5) FROM jobs",
        "SELECT * FROM dblink('x', 'y') AS t(a int)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT current_setting('x')",
    ],
)
def test_unrecognized_or_administrative_functions_are_rejected(sql: str) -> None:
    with pytest.raises(SqlGuardError):
        validate_select(sql)


def test_cte_is_rejected() -> None:
    with pytest.raises(SqlGuardError, match="CTE"):
        validate_select("WITH x AS (SELECT * FROM jobs) SELECT COUNT(*) FROM x")


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO jobs (company) VALUES ('x')",
        "DELETE FROM jobs",
        "UPDATE jobs SET status = 'x'",
        "DROP TABLE jobs",
        "SELECT 1 FROM (UPDATE jobs SET status='x' RETURNING id) t",
    ],
)
def test_write_and_ddl_statements_are_rejected(sql: str) -> None:
    with pytest.raises(SqlGuardError):
        validate_select(sql)


@pytest.mark.parametrize("sql", ["", "   "])
def test_empty_sql_is_rejected(sql: str) -> None:
    with pytest.raises(SqlGuardError):
        validate_select(sql)


def test_unparseable_sql_is_rejected() -> None:
    with pytest.raises(SqlGuardError):
        validate_select("not sql at all ???")
