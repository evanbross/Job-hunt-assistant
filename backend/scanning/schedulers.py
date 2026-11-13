# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06 -- the daemon-thread loops became Celery beat ticks.
"""Shared pieces of the scheduled work that Celery beat triggers (the schedule is
backend/celery_app.py's beat_schedule; the tick tasks live next to the routes whose
runs they start, in routes/career_sites.py and routes/jd_health_check.py).

A tick is a fan-out: one beat message, then one decision per account. Beat is the
single timer for every process, so it fires once however many web or Celery workers
are running, which daemon threads in each web process could not do."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from datetime import time as time_of_day

import config
from backend.settings_service import get_setting, get_setting_bool, get_setting_int
from backend.utils.utils import get_repos

logger = logging.getLogger(__name__)


def for_each_active_account(tick: str, act: Callable[[int], object]) -> None:
    """Run *act* for every registered account. One account's failure is logged and
    the tick carries on: a broken setting in one account must not stop the others'
    scheduled runs. (Failing to list the accounts at all raises; the Celery worker
    logs it, and the next tick tries again.)"""
    with get_repos() as repos:
        user_ids = repos["user_accounts"].list_active_user_ids()
    for user_id in user_ids:
        try:
            act(user_id)
        except Exception:
            logger.exception("Scheduled %s failed for user %s", tick, user_id)


# ai: claude-sonnet-5 | 2026-08-03
def is_jd_health_check_overdue(
    *,
    now: datetime,
    scheduled_time: time_of_day,
    frequency_days: int,
    last_run_at: datetime | None,
) -> bool:
    """Pure "is a JD health check run due" check — no DB/Flask, unit-testable
    with plain datetime/time arguments. This is the entire "catch up on next
    launch" mechanism: asked when Celery beat starts and on every tick after
    (routes/jd_health_check.py's jd_health_check_tick), so a run that was
    scheduled for a moment nothing was running fires as soon as beat is next
    up, instead of being silently skipped.

    now/last_run_at must be UTC-aware (this project's datetimes always are —
    see PYTHON_STANDARDS.md's UTC-only rule).
    """
    if last_run_at is None:
        return True  # never run before (e.g. first launch after upgrade) — run now
    next_due_date = (last_run_at + timedelta(days=max(1, frequency_days))).date()
    next_due = datetime.combine(next_due_date, scheduled_time, tzinfo=UTC)
    return now >= next_due


def jd_health_check_should_run_now(user_id: int) -> bool:
    """Read config + last-run state and decide if a scheduled run is due now."""
    with get_repos(user_id) as repos:
        if not get_setting_bool(
            "jd_health_check_enabled", user_id, repos,
            config.DEFAULT_JD_HEALTH_CHECK_ENABLED,
        ):
            return False

        time_str = get_setting("jd_health_check_time_utc", user_id, repos)
        frequency_days = get_setting_int(
            "jd_health_check_frequency_days", user_id, repos,
            config.DEFAULT_JD_HEALTH_CHECK_FREQUENCY_DAYS,
        )

        # ai: claude-sonnet-5 | 2026-08-03
        # A run with zero enabled targets never writes a jd_health_results row
        # (see run_jd_health_check_task), so last_run_at would stay None forever
        # and this function would keep reporting "overdue" on every poll --
        # submitting a new no-op Celery task every 15 minutes indefinitely.
        if not repos["jd_health_targets"].get_enabled(user_id):
            return False
        last_run_at_raw = repos["jd_health_results"].get_last_run_at(user_id)

    try:
        hour, minute = (int(part) for part in time_str.split(":", 1))
        scheduled_time = time_of_day(hour=hour, minute=minute, tzinfo=UTC)
    except (ValueError, TypeError):
        logger.warning("Invalid jd_health_check_time_utc %r; defaulting to 03:00", time_str)
        scheduled_time = time_of_day(hour=3, minute=0, tzinfo=UTC)

    last_run_at = None
    if last_run_at_raw:
        try:
            last_run_at = datetime.fromisoformat(str(last_run_at_raw)).replace(tzinfo=UTC)
        except ValueError:
            logger.warning("Could not parse jd_health_check last run_at %r", last_run_at_raw)

    return is_jd_health_check_overdue(
        now=datetime.now(UTC),
        scheduled_time=scheduled_time,
        frequency_days=frequency_days,
        last_run_at=last_run_at,
    )
