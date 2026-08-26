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

"""Static validation for LLM-generated SELECT statements (routes/voice.py's
voice-query feature). Uses sqlglot to parse into a real AST rather than
regex, since regex can't reliably catch statement-stacking, comments hiding
a second statement, or a CTE wrapping a write.

This is one of three independent defense layers for that feature -- the
others are backend/db/readonly_role.py's SELECT-only DB role, and a
READ ONLY transaction + statement timeout applied where the validated SQL is
executed. All three default-deny."""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

MAX_ROW_LIMIT = 200

ALLOWED_TABLES: frozenset[str] = frozenset(
    {"jobs", "application_stages", "application_stage_statuses", "latest_application_stage"}
)

# Curated per-table "safe/useful to read aloud" columns -- deliberately
# narrower than the full schema (e.g. excludes jobs.raw_jd_snippet and
# jobs.job_description: not sensitive, just not useful to speak or count on).
# Checked as one flat set rather than per-table since a few names (e.g.
# "status", "created_at") legitimately appear in more than one allowed table.
ALLOWED_COLUMNS: frozenset[str] = frozenset(
    {
        # jobs
        "id",
        "status",
        "company",
        "job_title",
        "fit_score",
        "url",
        "source",
        "date_found",
        "on_sheet",
        "duplicate_hint",
        "duplicate_of_id",
        "skip_reason",
        "salary_min",
        "salary_max",
        # application_stages
        "job_id",
        "date",
        "notes",
        "created_at",
        # application_stage_statuses
        "key",
        "label",
        "sort_order",
        "ghost_eligible",
        "is_terminal",
        # latest_application_stage
        "latest_status",
        "latest_status_date",
    }
)

# Node types indicating a write/DDL/administrative statement -- checked
# anywhere in the tree as defense beyond the single-statement and
# root-must-be-Select checks below (e.g. a write hidden in a subquery).
_FORBIDDEN_NODE_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.TruncateTable,
    exp.Command,
    exp.Copy,
)


class SqlGuardError(ValueError):
    """A generated SQL statement failed validation. The message is written
    to be safe to show the user directly."""


def validate_select(sql: str) -> str:
    """Validate `sql` is a single, read-only SELECT against ALLOWED_TABLES/
    ALLOWED_COLUMNS and reject unrecognized functions, and return it with a
    row limit applied. Raises SqlGuardError on any violation."""
    sql = (sql or "").strip()
    if not sql:
        raise SqlGuardError("No SQL was generated.")

    try:
        statements = [s for s in sqlglot.parse(sql, dialect="postgres") if s is not None]
    except ParseError as exc:
        raise SqlGuardError(f"Could not parse the generated SQL: {exc}") from exc

    if len(statements) != 1:
        raise SqlGuardError("Only a single SELECT statement is allowed.")
    root = statements[0]

    if not isinstance(root, exp.Select):
        raise SqlGuardError("Only SELECT statements are allowed.")
    if root.find(exp.With) is not None:
        raise SqlGuardError(
            "Queries with CTEs (WITH ...) aren't supported here -- "
            "latest_application_stage already covers latest-stage-per-job questions."
        )
    forbidden = root.find(*_FORBIDDEN_NODE_TYPES)
    if forbidden is not None:
        raise SqlGuardError("That isn't a read-only query.")

    _validate_no_wildcards(root)
    _validate_tables(root)
    _validate_columns(root)
    _validate_functions(root)

    return _apply_row_limit(root).sql(dialect="postgres")


def _validate_no_wildcards(root: exp.Select) -> None:
    for star in root.find_all(exp.Star):
        # COUNT(*) (star.parent is a Func, e.g. exp.Count) is fine; a bare
        # "*" (parent is the Select itself) or "table.*" (parent is a
        # Column) is not -- either would bypass the column allowlist below.
        if isinstance(star.parent, exp.Func):
            continue
        raise SqlGuardError("SELECT * isn't allowed -- name the columns you need.")


def _validate_tables(root: exp.Select) -> None:
    for table in root.find_all(exp.Table):
        if table.name not in ALLOWED_TABLES:
            raise SqlGuardError(f"'{table.name}' isn't a table this feature can read.")


def _validate_columns(root: exp.Select) -> None:
    for column in root.find_all(exp.Column):
        if column.name and column.name not in ALLOWED_COLUMNS:
            raise SqlGuardError(f"'{column.name}' isn't a column this feature can read.")


def _validate_functions(root: exp.Select) -> None:
    # Default-deny by construction, not a blocklist: sqlglot gives every
    # function it specifically recognizes (COUNT, SUM, LOWER, DATE_TRUNC,
    # CONCAT, ...) its own typed node; anything it *doesn't* recognize --
    # which is where pg_sleep, dblink, lo_import, pg_read_file, set_config,
    # etc. all land, verified individually -- falls through to exp.Anonymous.
    # A name-based blocklist of "dangerous" functions would always be
    # incomplete; this instead only allows functions sqlglot's own maintainers
    # already curated as standard, well-understood SQL.
    for func in root.find_all(exp.Anonymous):
        raise SqlGuardError(f"The function '{func.name}' isn't allowed in this query.")


def _apply_row_limit(root: exp.Select) -> exp.Select:
    """Leaves an explicit LIMIT <= MAX_ROW_LIMIT alone (the model chose that
    number deliberately, e.g. "top 10"). Otherwise caps at MAX_ROW_LIMIT + 1,
    not MAX_ROW_LIMIT itself: the extra row lets the caller (routes/voice.py)
    detect whether real results were truncated without a second COUNT query,
    then trim back to MAX_ROW_LIMIT before returning them."""
    existing_limit = root.args.get("limit")
    row_cap = None
    if existing_limit is not None:
        try:
            row_cap = int(existing_limit.expression.this)
        except (TypeError, ValueError, AttributeError):
            row_cap = None
    if row_cap is not None and row_cap <= MAX_ROW_LIMIT:
        return root
    root.set("limit", exp.Limit(expression=exp.Literal.number(MAX_ROW_LIMIT + 1)))
    return root
