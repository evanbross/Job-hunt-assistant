# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-15
"""migrate_schema brings a drifted application_stages table (the live database's,
before 2026-11-15) in line with what create_tables builds: job_id INTEGER with an
ON DELETE CASCADE foreign key to jobs, orphaned stages removed, and no second
status foreign key that would block a catalog key rename."""

from __future__ import annotations

from typing import Any

from backend.db import open_repositories
from backend.db.row_level_security import set_current_user
from backend.db.schema import (
    APPLICATION_STAGES_JOB_FK,
    LATEST_APPLICATION_STAGE_VIEW_SQL,
    migrate_schema,
)


def _one(cur: Any, query: str, params: tuple = ()) -> Any:
    cur.execute(query, params)
    row = cur.fetchone()
    return next(iter(row.values())) if row else None


def test_migrate_aligns_a_drifted_application_stages_table(pg_dsn: str) -> None:
    repos = open_repositories(pg_dsn)
    conn = repos["conn"]
    cur = conn.cursor()
    try:
        user_id = int(repos["user_accounts"].create_pending("stages-fk@example.com", "seed", "2099-01-01")["id"])
        set_current_user(cur, user_id)
        job_id = repos["jobs"].insert(
            user_id,
            {"dedup_key": "fk-1", "company": "Acme", "job_title": "Engineer", "url": "https://example.com/fk",
             "source": "manual", "status": "applied", "on_sheet": 1, "date_found": "2026-11-15"},
        )
        status = _one(cur, "SELECT key FROM application_stage_statuses ORDER BY key LIMIT 1")

        # Recreate the live table's drift.
        cur.execute(f"ALTER TABLE application_stages DROP CONSTRAINT {APPLICATION_STAGES_JOB_FK}")
        cur.execute("DROP VIEW latest_application_stage")
        cur.execute("ALTER TABLE application_stages ALTER COLUMN job_id TYPE BIGINT")
        cur.execute(LATEST_APPLICATION_STAGE_VIEW_SQL)
        cur.execute(
            "ALTER TABLE application_stages ADD CONSTRAINT fk_stage_status"
            " FOREIGN KEY (status) REFERENCES application_stage_statuses(key)"
        )
        cur.execute(
            "INSERT INTO application_stages (user_id, job_id, status, date) VALUES (%s, %s, %s, '2026-04-22')",
            (user_id, job_id, status),
        )
        cur.execute(
            "INSERT INTO application_stages (user_id, job_id, status, date) VALUES (%s, %s, %s, '2026-04-22')",
            (user_id, job_id + 100_000, status),  # an orphan: no such job
        )
        conn.commit()

        migrate_schema(conn)

        assert _one(cur, "SELECT COUNT(*) FROM application_stages WHERE job_id = %s", (job_id + 100_000,)) == 0
        assert _one(
            cur,
            "SELECT data_type FROM information_schema.columns"
            " WHERE table_name = 'application_stages' AND column_name = 'job_id'",
        ) == "integer"
        assert _one(
            cur,
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = %s",
            (APPLICATION_STAGES_JOB_FK,),
        ) == "FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE"
        assert _one(cur, "SELECT COUNT(*) FROM pg_constraint WHERE conname = 'fk_stage_status'") == 0
        assert _one(cur, "SELECT latest_status FROM latest_application_stage WHERE job_id = %s", (job_id,)) == status

        # Deleting a job now takes its stages with it.
        repos["jobs"].delete(user_id, job_id)
        conn.commit()
        assert _one(cur, "SELECT COUNT(*) FROM application_stages WHERE job_id = %s", (job_id,)) == 0

        # Running again changes nothing.
        migrate_schema(conn)
        assert _one(
            cur, "SELECT COUNT(*) FROM pg_constraint WHERE conname = %s", (APPLICATION_STAGES_JOB_FK,)
        ) == 1
    finally:
        conn.close()
