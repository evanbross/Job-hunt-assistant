# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Account deletion (after its grace period) and log-table retention pruning.

Both are cross-tenant by nature -- one sweep acts across every account, the same
shape as a migration or a key rotation -- so both connect as the schema owner
(config.DATABASE_OWNER_URL), which row-level security exempts by design (see
backend/db/row_level_security.py). A per-request connection (get_repos(), the
runtime role scoped to one account) is the wrong tool for either of these.

Pruning is deliberately narrow: only tables that are purely informational logs,
where deleting an old row can only shorten history a user could scroll back
through, never change what a feature does. See config.py's *_RETENTION_DAYS
comment for which tables were deliberately left out and why.

Celery beat triggers the sweep (backend/celery_app.py). It is routed to the
`maintenance` queue, whose worker is the only one given the owner's URL; the
ordinary workers serve as the runtime role and never hold it."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import config
from backend.celery_app import celery_app
from backend.db import get_connection
from backend.db.app_role import assert_exempt_from_rls
from backend.db.repositories.user_accounts import PostgresUserAccountRepository

logger = logging.getLogger(__name__)

# table -> (timestamp column, is that column TIMESTAMPTZ (vs. this project's more
# common ISO-string-in-TEXT convention), the config attribute naming its retention in
# days). Every entry here is a plain log: nothing else in the app treats an old row as
# meaningful once it ages out. The column's real type matters for how the cutoff below
# gets bound (psycopg2 adapts a datetime object straight to TIMESTAMPTZ; comparing that
# against a TEXT column has no implicit cast and raises, so TEXT columns get an ISO
# string instead). The config attribute is read fresh on every call, not captured here
# at import time, so changing it (a test, or a future runtime-config source) takes
# effect on the next sweep without a process restart.
_RETENTION_TABLES: dict[str, tuple[str, bool, str]] = {
    "audit_log": ("created_at", True, "AUDIT_LOG_RETENTION_DAYS"),
    "import_repair_history": ("run_at", False, "IMPORT_REPAIR_HISTORY_RETENTION_DAYS"),
    "jd_health_check_results": ("run_at", False, "JD_HEALTH_CHECK_RESULTS_RETENTION_DAYS"),
    "career_scan_failures": ("occurred_at", False, "CAREER_SCAN_FAILURES_RETENTION_DAYS"),
}


def sweep_scheduled_deletions(conn: Any) -> int:
    """Hard-delete every account whose grace period has ended. Returns the count.
    Each account's own child rows go with it via ON DELETE CASCADE."""
    # deletion_requested_at is a real TIMESTAMPTZ column; pass a datetime object,
    # not a string, so psycopg2 adapts it straight to that type.
    cutoff = datetime.now(UTC) - timedelta(days=config.ACCOUNT_DELETION_GRACE_DAYS)
    accounts_repo = PostgresUserAccountRepository(conn)
    due = accounts_repo.get_accounts_past_deletion_grace_period(cutoff)
    for account in due:
        # Logged here, not to audit_log: that table's own row for this account is
        # about to cascade-delete along with everything else, so it would never be
        # read back. This is the one record of the deletion that survives it.
        logger.warning(
            "Deleting account %s (%s): past its %sd grace period (requested %s)",
            account["id"], account["email"], config.ACCOUNT_DELETION_GRACE_DAYS,
            account["deletion_requested_at"],
        )
        accounts_repo.delete(account["id"])
    return len(due)


def prune_retention_tables(conn: Any) -> dict[str, int]:
    """Delete rows older than each table's configured retention window. Returns the
    number of rows removed per table. A retention period of 0 or less disables
    pruning for that table (kept forever) rather than deleting everything."""
    cur = conn.cursor()
    removed: dict[str, int] = {}
    for table, (column, is_timestamptz, days_attr) in _RETENTION_TABLES.items():
        days = getattr(config, days_attr)
        if days <= 0:
            removed[table] = 0
            continue
        cutoff_at = datetime.now(UTC) - timedelta(days=days)
        cutoff = cutoff_at if is_timestamptz else cutoff_at.isoformat(timespec="seconds")
        cur.execute(f"DELETE FROM {table} WHERE {column} < %s", (cutoff,))  # noqa: S608 - table/column from the fixed dict above, never user input
        removed[table] = cur.rowcount
    return removed


# ai: claude-opus-5-5 | 2026-11-06 -- `owner_dsn` instead of switching RLS off globally;
# refuses a role RLS applies to, which would otherwise sweep nothing without an error.
def run_data_lifecycle_sweep(owner_dsn: str) -> None:
    """One full sweep: deletions past their grace period, then retention pruning, in
    one transaction. `owner_dsn` must be the schema owner's: it needs every account's
    rows (see module docstring)."""
    conn = get_connection(owner_dsn)
    try:
        assert_exempt_from_rls(conn)
        deleted = sweep_scheduled_deletions(conn)
        pruned = prune_retention_tables(conn)
        conn.commit()
    finally:
        conn.close()
    if deleted or any(pruned.values()):
        logger.info("Data lifecycle sweep: %d account(s) deleted, pruned %s", deleted, pruned)


# ai: claude-opus-5-5 | 2026-11-06 -- replaces the daemon-thread loop in each web process.
@celery_app.task
def data_lifecycle_sweep_task() -> None:
    """Beat's every-DATA_LIFECYCLE_SWEEP_INTERVAL_HOURS sweep, on the maintenance queue."""
    run_data_lifecycle_sweep(config.DATABASE_OWNER_URL)
