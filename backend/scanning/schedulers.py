# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-08-13
"""Background daemon-thread schedulers for the career-site scan and the JD
parser health check. Both follow the same "configure, then start" DI pattern
and the same Werkzeug-reloader guard."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from datetime import time as time_of_day

import config
from backend.settings_service import get_setting, get_setting_bool, get_setting_int
from backend.utils.utils import get_repos

logger = logging.getLogger(__name__)

_career_scheduler_lock = threading.Lock()
_career_scheduler_started = False
_career_scheduler_submit_run: Callable[..., str | None] | None = None


# ai: claude-sonnet-5 | 2026-08-20
# Simplified from the old is_running/run_scan pair to a single submit_run,
# mirroring configure_jd_health_check_scheduler below -- career-site scanning
# is now a Celery task, and its own dispatch helper (routes/career_sites.py's
# _submit_career_scan) already no-ops if one is active, so this loop doesn't
# need a separate "is one running" check of its own.
def configure_career_site_scheduler(
    *,
    submit_run: Callable[..., str | None],
) -> None:
    global _career_scheduler_submit_run
    _career_scheduler_submit_run = submit_run


# ai: claude-sonnet-5 | 2026-09-26
def _active_user_ids() -> list[int]:
    """Every account a scheduled tick should act for. A tick fans out per
    tenant; a failure looking the list up skips this tick, not the thread."""
    try:
        with get_repos() as repos:
            return repos["user_accounts"].list_active_user_ids()
    except Exception:
        logger.exception("Scheduler could not list active accounts; skipping this tick")
        return []


def start_career_site_scheduler() -> None:
    global _career_scheduler_started

    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    if debug_mode and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    with _career_scheduler_lock:
        if _career_scheduler_started:
            return
        if _career_scheduler_submit_run is None:
            raise RuntimeError("Career-site scheduler has not been configured.")

        # ai: gpt-5 | 2026-10-04
        def career_scheduler_loop() -> None:
            while True:
                for user_id in _active_user_ids():
                    try:
                        _career_scheduler_submit_run(user_id, trigger="scheduled")
                    except Exception:
                        logger.exception(
                            "Career-site scheduler tick failed for user %s", user_id
                        )
                interval_seconds = max(1, config.CAREER_SITE_SCAN_INTERVAL_MINUTES) * 60
                time.sleep(interval_seconds)

        thread = threading.Thread(target=career_scheduler_loop, daemon=True)
        thread.start()
        _career_scheduler_started = True
        logger.info(
            "Career-site scheduler started (interval: %s minute(s)).",
            max(1, config.CAREER_SITE_SCAN_INTERVAL_MINUTES),
        )


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
    launch" mechanism: called both at app startup and on a periodic poll
    while the app is running (see start_jd_health_check_scheduler below), so
    a run that was scheduled for a moment the app was closed fires as soon
    as the app is next open, instead of being silently skipped.

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


_jd_health_scheduler_lock = threading.Lock()
_jd_health_scheduler_started = False
_jd_health_scheduler_should_run_now: Callable[[int], bool] | None = None
_jd_health_scheduler_submit_run: Callable[[int, str], str | None] | None = None
_JD_HEALTH_CHECK_POLL_SECONDS = 15 * 60


def configure_jd_health_check_scheduler(
    *,
    should_run_now: Callable[[int], bool],
    submit_run: Callable[[int, str], str | None],
) -> None:
    global _jd_health_scheduler_should_run_now, _jd_health_scheduler_submit_run
    _jd_health_scheduler_should_run_now = should_run_now
    _jd_health_scheduler_submit_run = submit_run


def start_jd_health_check_scheduler() -> None:
    """Start the daemon thread that fires an overdue JD health check run.

    Mirrors start_career_site_scheduler's reloader guard (only the real
    request-serving process in Werkzeug debug/reloader mode starts the
    thread — the monitor process would otherwise run a duplicate). Unlike
    that scheduler, this one is actually started (see app.py's __main__
    block) — it's check-then-sleep rather than sleep-then-check, so the
    first check happens immediately on thread start, which is what makes
    this "catch up on next launch" rather than "wait one full interval
    before the first check."
    """
    global _jd_health_scheduler_started

    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    if debug_mode and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    with _jd_health_scheduler_lock:
        if _jd_health_scheduler_started:
            return
        if (
            _jd_health_scheduler_should_run_now is None
            or _jd_health_scheduler_submit_run is None
        ):
            raise RuntimeError("JD health check scheduler has not been configured.")

        def jd_health_scheduler_loop() -> None:
            while True:
                for user_id in _active_user_ids():
                    try:
                        if _jd_health_scheduler_should_run_now(user_id):
                            _jd_health_scheduler_submit_run(user_id, "scheduled")
                    except Exception:
                        logger.exception(
                            "JD health check scheduler tick failed for user %s", user_id
                        )
                time.sleep(_JD_HEALTH_CHECK_POLL_SECONDS)

        thread = threading.Thread(target=jd_health_scheduler_loop, daemon=True)
        thread.start()
        _jd_health_scheduler_started = True
        logger.info(
            "JD health check scheduler started (poll interval: %s minute(s)).",
            _JD_HEALTH_CHECK_POLL_SECONDS // 60,
        )
