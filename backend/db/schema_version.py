# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Which version of the migration code last migrated this database.

Migrations are a deploy step (`python -m backend.db.migrate`), not something every app
process runs at startup (docs/cloud-deployment-readiness-plan.md 1.6). That leaves one
question a starting server has to answer: has this database actually been migrated by
the code it is running? The migrations are ~2,000 lines of idempotent steps with no
version numbers, so the answer is a hash of the source that defines them. The migrate
command records it; a serving process compares it to its own and refuses to start on a
mismatch. Any edit to these files therefore means "run migrate before starting", which
is the deploy order anyway.

Added 2026-11-06.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

_DB_PACKAGE = Path(__file__).resolve().parent

# Every file whose code decides what migrate_database() does to the database.
MIGRATION_SOURCE_FILES: tuple[Path, ...] = (
    _DB_PACKAGE / "__init__.py",
    _DB_PACKAGE / "schema.py",
    _DB_PACKAGE / "row_level_security.py",
    _DB_PACKAGE / "readonly_role.py",
    _DB_PACKAGE / "app_role.py",
    _DB_PACKAGE / "schema_version.py",
)


class SchemaOutOfDateError(RuntimeError):
    """The database was not migrated by this version of the code."""


def migration_source_hash() -> str:
    """SHA-256 over MIGRATION_SOURCE_FILES. Line endings are normalized so a Windows
    checkout and a Linux image of the same commit agree."""
    digest = hashlib.sha256()
    for path in MIGRATION_SOURCE_FILES:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def create_schema_state_table(conn: Any) -> None:
    """Create the one-row table the migration record lives in. Runs early in
    migrate_database(), so the runtime role's grants (which run before the record is
    written) can already name it."""
    conn.cursor().execute(
        "CREATE TABLE IF NOT EXISTS schema_state ("
        " id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),"
        " migration_hash TEXT NOT NULL,"
        " migrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
    )
    conn.commit()


def record_schema_version(conn: Any) -> None:
    """Mark the database as migrated by this code. The last step of migrate_database(),
    so a migration that fails partway never records success."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO schema_state (id, migration_hash, migrated_at) VALUES (TRUE, %s, NOW())"
        " ON CONFLICT (id) DO UPDATE SET migration_hash = EXCLUDED.migration_hash,"
        " migrated_at = EXCLUDED.migrated_at",
        (migration_source_hash(),),
    )
    conn.commit()


def assert_schema_is_current(conn: Any) -> None:
    """Raise SchemaOutOfDateError unless the database was migrated by this exact code."""
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('public.schema_state') IS NOT NULL AS present")
    if not cur.fetchone()["present"]:
        conn.rollback()
        raise SchemaOutOfDateError(
            "This database has never been migrated by a version of the app that records it."
            " Run `python -m backend.db.migrate` first."
        )
    cur.execute("SELECT migration_hash FROM schema_state WHERE id")
    row = cur.fetchone()
    conn.rollback()
    if row is None or row["migration_hash"] != migration_source_hash():
        raise SchemaOutOfDateError(
            "The database was migrated by a different version of the app than this one."
            " Run `python -m backend.db.migrate` with this version before starting it."
        )
