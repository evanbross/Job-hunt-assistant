# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, time

from backend.scanning.schedulers import is_jd_health_check_overdue

_SCHEDULED_3AM = time(3, 0, tzinfo=UTC)


def test_never_run_before_is_always_overdue() -> None:
    assert is_jd_health_check_overdue(
        now=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
        scheduled_time=_SCHEDULED_3AM,
        frequency_days=1,
        last_run_at=None,
    )


def test_recent_run_within_frequency_is_not_overdue() -> None:
    assert not is_jd_health_check_overdue(
        now=datetime(2026, 8, 3, 4, 0, tzinfo=UTC),
        scheduled_time=_SCHEDULED_3AM,
        frequency_days=1,
        last_run_at=datetime(2026, 8, 3, 3, 0, tzinfo=UTC),
    )


def test_boundary_exact_next_due_moment_is_overdue() -> None:
    # Ran at 03:00 on day 1, daily frequency -> next due exactly 03:00 day 2.
    assert is_jd_health_check_overdue(
        now=datetime(2026, 8, 4, 3, 0, tzinfo=UTC),
        scheduled_time=_SCHEDULED_3AM,
        frequency_days=1,
        last_run_at=datetime(2026, 8, 3, 3, 0, tzinfo=UTC),
    )


def test_one_minute_before_next_due_is_not_overdue() -> None:
    assert not is_jd_health_check_overdue(
        now=datetime(2026, 8, 4, 2, 59, tzinfo=UTC),
        scheduled_time=_SCHEDULED_3AM,
        frequency_days=1,
        last_run_at=datetime(2026, 8, 3, 3, 0, tzinfo=UTC),
    )


def test_catch_up_after_app_closed_through_scheduled_time() -> None:
    # App was closed at 3am and only reopened two days later -- this is the
    # actual "catch up on next launch" behavior the feature exists to provide.
    assert is_jd_health_check_overdue(
        now=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
        scheduled_time=_SCHEDULED_3AM,
        frequency_days=1,
        last_run_at=datetime(2026, 8, 2, 3, 5, tzinfo=UTC),
    )


def test_weekly_frequency_not_overdue_after_two_days() -> None:
    assert not is_jd_health_check_overdue(
        now=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
        scheduled_time=_SCHEDULED_3AM,
        frequency_days=7,
        last_run_at=datetime(2026, 8, 2, 3, 5, tzinfo=UTC),
    )


def test_weekly_frequency_overdue_after_eight_days() -> None:
    assert is_jd_health_check_overdue(
        now=datetime(2026, 8, 10, 10, 0, tzinfo=UTC),
        scheduled_time=_SCHEDULED_3AM,
        frequency_days=7,
        last_run_at=datetime(2026, 8, 2, 3, 5, tzinfo=UTC),
    )
