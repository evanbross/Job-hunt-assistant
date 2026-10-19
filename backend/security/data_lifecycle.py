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
shape as a migration or a key rotation -- so both run inside the same RLS-free
bracket `backend/db/row_level_security.py`'s disable_for_migrations()/
enable_and_force() give init_db() and rotate_all(). A per-request connection
(get_repos(), scoped to one account) is the wrong tool for either of these.

Pruning is deliberately narrow: only tables that are purely informational logs,
where deleting an old row can only shorten history a user could scroll back
through, never change what a feature does. See config.py's *_RETENTION_DAYS
comment for which tables were deliberately left out and why."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import config
from backend.db import get_connection
from backend.db.repositories.user_accounts import PostgresUserAccountRepository
from backend.db.row_level_security import disable_for_migrations, enable_and_force

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


def run_data_lifecycle_sweep(dsn: str) -> None:
    """One full sweep: deletions past their grace period, then retention pruning.
    Both need the RLS-free bracket (see module docstring); always re-armed before
    the connection closes, success or failure, so the database is never left
    unprotected because a sweep raised partway through."""
    conn = get_connection(dsn)
    try:
        disable_for_migrations(conn)
        try:
            deleted = sweep_scheduled_deletions(conn)
            pruned = prune_retention_tables(conn)
            conn.commit()
        finally:
            enable_and_force(conn)
    finally:
        conn.close()
    if deleted or any(pruned.values()):
        logger.info("Data lifecycle sweep: %d account(s) deleted, pruned %s", deleted, pruned)


_scheduler_lock = threading.Lock()
_scheduler_started = False


def start_data_lifecycle_scheduler() -> None:
    """Start the daemon thread that runs run_data_lifecycle_sweep() on a fixed
    interval. Mirrors backend/scanning/schedulers.py's reloader guard and
    check-then-sleep shape (a sweep runs once at startup, not only after the
    first full interval, so a deletion that fell due while the app was closed
    doesn't wait a whole extra interval to be picked up)."""
    global _scheduler_started

    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    if debug_mode and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    with _scheduler_lock:
        if _scheduler_started:
            return

        def sweep_loop() -> None:
            interval_seconds = max(1.0, config.DATA_LIFECYCLE_SWEEP_INTERVAL_HOURS) * 3600
            while True:
                try:
                    run_data_lifecycle_sweep(config.DATABASE_URL)
                except Exception:
                    logger.exception("Data lifecycle sweep failed; retrying next interval")
                time.sleep(interval_seconds)

        thread = threading.Thread(target=sweep_loop, daemon=True)
        thread.start()
        _scheduler_started = True
        logger.info(
            "Data lifecycle scheduler started (interval: %s hour(s)).",
            config.DATA_LIFECYCLE_SWEEP_INTERVAL_HOURS,
        )
