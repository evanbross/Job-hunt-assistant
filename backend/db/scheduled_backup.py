# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-07
"""Beat's hourly backup tick (docs/cloud-deployment-readiness-plan.md 4.1).

The tick only decides; backend/db/backup.py does the work. It runs on the maintenance
queue, whose worker holds the schema owner's URL. A backup is due when the last
successful one is BACKUP_INTERVAL_HOURS old, so a failed run is simply retried at the next
tick, and beat restarting (it ticks everything on start) never takes an extra backup.

Kept apart from backup.py so the restore command line, which runs on an operator's
machine, never imports Celery."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import config
from backend.app_mode import is_hosted
from backend.celery_app import celery_app
from backend.db.backup import (
    BackupConfigError,
    BackupSettings,
    last_success_at,
    run_backup,
)

logger = logging.getLogger(__name__)


def backup_if_due(owner_dsn: str, environ: Mapping[str, str], *, now: datetime | None = None) -> bool:
    """Take a backup if one is due. True if a backup was taken. Raises BackupFailed
    (already recorded in backup_runs) if it was attempted and failed."""
    try:
        settings = BackupSettings.from_env(environ)
    except BackupConfigError as exc:
        logger.error("%s", exc)
        return False
    if settings is None:
        if is_hosted():
            # deploy/docker-compose.prod.yml requires these, so this is a deployment
            # that bypassed it. Every tick says so until it's fixed.
            logger.error("No database backups are configured: set BACKUP_S3_BUCKET and BACKUP_AGE_RECIPIENTS.")
        return False
    current = now or datetime.now(UTC)
    last = last_success_at(owner_dsn)
    if last is not None and current - last < timedelta(hours=settings.interval_hours):
        return False
    return run_backup(owner_dsn, settings, now=current) is not None


@celery_app.task
def database_backup_tick() -> None:
    """Beat's hourly tick, on the maintenance queue."""
    backup_if_due(config.DATABASE_OWNER_URL, os.environ)
