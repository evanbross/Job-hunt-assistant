# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Run database migrations once, as a deploy step:

    python -m backend.db.migrate

Connects as the schema owner (config.DATABASE_OWNER_URL), migrates, provisions the
runtime role named in config.DATABASE_URL, and records which code did it. Serving
processes then check that record and refuse to start against a database this version
never migrated (backend/db/schema_version.py). Run it before starting new app
containers, not alongside them.

Unlike the desktop launcher's migrate-at-startup, there is no retry or degraded mode:
a deploy step should fail loudly and stop the deploy. Exit status 0 on success, 1 on
failure.

Added 2026-11-06 (docs/cloud-deployment-readiness-plan.md 1.6).
"""

from __future__ import annotations

import logging
import sys

import psycopg2

import config
from backend.db import AppRoleConfigError, migrate_database

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
        stream=sys.stdout,
    )
    try:
        migrate_database(config.DATABASE_OWNER_URL, config.DATABASE_URL)
    except AppRoleConfigError as exc:
        logger.critical("%s", exc)
        return 1
    except psycopg2.Error as exc:
        logger.critical("Migration failed: %s", exc)
        return 1
    logger.info("Database migrated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
