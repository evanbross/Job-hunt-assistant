# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""create_tables() must work on an empty database, not just on one an earlier run
already migrated.

create_tables() runs before migrate_schema() adds the columns older tables gained later
(jobs.user_id among them), so a statement there that names such a column passes against
every long-lived database -- the test database included -- and fails only on a brand-new
one: a first deploy. It happened twice (2026-10-08, and again with
idx_jobs_user_status_date_id, found by the first container deploy on 2026-11-06).

The test user can't create databases, so this builds the tables in an empty schema
placed first on the search path: every CREATE lands there, while the legacy-rename
checks (which look in `public`) still see the real, already-migrated tables and do
nothing, just as they find nothing to rename on a fresh install."""

from __future__ import annotations

import uuid

import psycopg2
import psycopg2.extras
from psycopg2 import sql
from psycopg2.extensions import make_dsn

from backend.db.schema import create_tables


def test_create_tables_succeeds_on_an_empty_database(pg_dsn: str) -> None:
    schema = f"fresh_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(pg_dsn)
    admin.autocommit = True
    admin.cursor().execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        conn = psycopg2.connect(
            make_dsn(pg_dsn, options=f"-c search_path={schema},public"),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        try:
            create_tables(conn)
            cur = conn.cursor()
            cur.execute(
                "SELECT count(*) AS tables FROM pg_tables WHERE schemaname = %s", (schema,)
            )
            assert cur.fetchone()["tables"] > 20
            cur.execute(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_schema = %s AND table_name = 'jobs' AND column_name = 'user_id'",
                (schema,),
            )
            # Still the shape a fresh install has at this point: migrate_schema() adds it.
            assert cur.fetchone() is None
        finally:
            conn.close()
    finally:
        admin.cursor().execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
        )
        admin.close()
