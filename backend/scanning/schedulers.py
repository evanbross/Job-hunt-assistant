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
from backend.utils.utils import get_repos

logger = logging.getLogger(__name__)

_career_scheduler_lock = threading.Lock()
_career_scheduler_started = False
_career_scheduler_is_running: Callable[[], bool] | None = None
_career_scheduler_run_scan: Callable[..., None] | None = None


def configure_career_site_scheduler(
    *,
    is_running: Callable[[], bool],
    run_scan: Callable[..., None],
) -> None:
    global _career_scheduler_is_running, _career_scheduler_run_scan
    _career_scheduler_is_running = is_running
    _career_scheduler_run_scan = run_scan


def start_career_site_scheduler() -> None:
    global _career_scheduler_started

    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    if debug_mode and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    with _career_scheduler_lock:
        if _career_scheduler_started:
            return
        if _career_scheduler_is_running is None or _career_scheduler_run_scan is None:
            raise RuntimeError("Career-site scheduler has not been configured.")

        def career_scheduler_loop() -> None:
            while True:
                interval_seconds = max(1, config.CAREER_SITE_SCAN_INTERVAL_MINUTES) * 60
                time.sleep(interval_seconds)
                if not _career_scheduler_is_running():
                    _career_scheduler_run_scan(trigger="scheduled")

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


def jd_health_check_should_run_now() -> bool:
    """Read config + last-run state and decide if a scheduled run is due now."""
    cfg = config.load_runtime_config()
    if not cfg.get("jd_health_check_enabled"):
        return False

    time_str = str(cfg.get("jd_health_check_time_utc") or "03:00")
    try:
        hour, minute = (int(part) for part in time_str.split(":", 1))
        scheduled_time = time_of_day(hour=hour, minute=minute, tzinfo=UTC)
    except (ValueError, TypeError):
        logger.warning("Invalid jd_health_check_time_utc %r; defaulting to 03:00", time_str)
        scheduled_time = time_of_day(hour=3, minute=0, tzinfo=UTC)

    frequency_days = int(cfg.get("jd_health_check_frequency_days") or 1)

    with get_repos() as repos:
        # ai: claude-sonnet-5 | 2026-08-03
        # A run with zero enabled targets never writes a jd_health_results row
        # (see run_jd_health_check_task), so last_run_at would stay None forever
        # and this function would keep reporting "overdue" on every poll --
        # submitting a new no-op Celery task every 15 minutes indefinitely.
        if not repos["jd_health_targets"].get_enabled():
            return False
        last_run_at_raw = repos["jd_health_results"].get_last_run_at()

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
_jd_health_scheduler_should_run_now: Callable[[], bool] | None = None
_jd_health_scheduler_submit_run: Callable[[str], str | None] | None = None
_JD_HEALTH_CHECK_POLL_SECONDS = 15 * 60


def configure_jd_health_check_scheduler(
    *,
    should_run_now: Callable[[], bool],
    submit_run: Callable[[str], str | None],
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
                try:
                    if _jd_health_scheduler_should_run_now():
                        _jd_health_scheduler_submit_run("scheduled")
                except Exception:
                    logger.exception("JD health check scheduler tick failed")
                time.sleep(_JD_HEALTH_CHECK_POLL_SECONDS)

        thread = threading.Thread(target=jd_health_scheduler_loop, daemon=True)
        thread.start()
        _jd_health_scheduler_started = True
        logger.info(
            "JD health check scheduler started (poll interval: %s minute(s)).",
            _JD_HEALTH_CHECK_POLL_SECONDS // 60,
        )
