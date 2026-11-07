# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""Scheduled work on Celery beat (docs/cloud-deployment-readiness-plan.md 1.4):
the schedule names real tasks on the right queues, the ticks fan out per account and
never start a second run, and the cross-account sweep refuses a role RLS applies to."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from celery.signals import beat_init

import config
import routes.career_sites as career_sites
import routes.jd_health_check as jd_health_check
from backend.celery_app import (
    CAREER_SITE_SCAN_TICK,
    DATA_LIFECYCLE_SWEEP,
    DATABASE_BACKUP_TICK,
    DEFAULT_QUEUE,
    JD_HEALTH_CHECK_TICK,
    MAINTENANCE_QUEUE,
    celery_app,
)
from backend.db import AppRoleConfigError, get_connection
from backend.db.repositories.user_accounts import PostgresUserAccountRepository
from backend.scanning.schedulers import for_each_active_account
from backend.security.data_lifecycle import run_data_lifecycle_sweep
from tests.db_roles import runtime_database_url
from tests.task_doubles import RecordedDelays, install_everything_running_registry

# ─── the schedule itself ────────────────────────────────────────────────────────


def test_every_scheduled_task_is_one_the_worker_registers() -> None:
    """beat sends by name; a name no included module defines would queue forever."""
    celery_app.loader.import_default_modules()
    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}

    assert scheduled == {CAREER_SITE_SCAN_TICK, JD_HEALTH_CHECK_TICK, DATA_LIFECYCLE_SWEEP, DATABASE_BACKUP_TICK}
    assert scheduled <= set(celery_app.tasks)


def _queue_for(task_name: str) -> str:
    return celery_app.amqp.router.route({}, task_name)["queue"].name


# ai: claude-opus-5-5 | 2026-11-07 -- and the backup tick: both need the owner's URL
def test_only_the_sweep_and_the_backup_go_to_the_maintenance_queue() -> None:
    assert _queue_for(DATA_LIFECYCLE_SWEEP) == MAINTENANCE_QUEUE
    assert _queue_for(DATABASE_BACKUP_TICK) == MAINTENANCE_QUEUE
    assert _queue_for(CAREER_SITE_SCAN_TICK) == DEFAULT_QUEUE
    assert _queue_for(JD_HEALTH_CHECK_TICK) == DEFAULT_QUEUE


def test_beat_ticks_everything_once_as_it_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old threads checked as they started ("catch up on next launch"); an
    interval schedule alone would wait a full interval first."""
    sent: list[str] = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, *a, **k: sent.append(name))

    beat_init.send(sender=None)

    assert sorted(sent) == sorted(
        [CAREER_SITE_SCAN_TICK, JD_HEALTH_CHECK_TICK, DATA_LIFECYCLE_SWEEP, DATABASE_BACKUP_TICK]
    )


# ─── fan-out: the real accounts query, as the runtime role ─────────────────────


def _registered_user(pg_dsn: str, email: str) -> int:
    conn = get_connection(pg_dsn)
    try:
        user_id = PostgresUserAccountRepository(conn).create_pending(email, f"t-{email}", "2099-01-01")["id"]
        conn.cursor().execute("UPDATE user_accounts SET password_hash = 'x' WHERE id = %s", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return user_id


def test_a_tick_acts_for_every_registered_account_and_survives_one_failing(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ticks run in a Celery worker as the runtime role, with no account selected;
    listing the accounts must still see all of them, or every tick silently does
    nothing."""
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    first = _registered_user(pg_dsn, "first@example.com")
    second = _registered_user(pg_dsn, "second@example.com")
    pending = get_connection(pg_dsn)
    pending_id = PostgresUserAccountRepository(pending).create_pending(
        "pending@example.com", "t-p", "2099-01-01"
    )["id"]
    pending.commit()
    pending.close()
    acted: list[int] = []

    def act(user_id: int) -> None:
        acted.append(user_id)
        if user_id == first:
            raise RuntimeError("one account's broken setting")

    for_each_active_account("test", act)

    # Every registered account (the fresh schema's bootstrap admin too), including the
    # one after the failure; not the pending sign-up, which has nothing to act on.
    assert first in acted and second in acted
    assert pending_id not in acted


# ─── the ticks never start a second run ────────────────────────────────────────


def _accounts(monkeypatch: pytest.MonkeyPatch, *user_ids: int) -> None:
    def for_each(_tick: str, act: Any) -> None:
        for user_id in user_ids:
            act(user_id)

    monkeypatch.setattr(career_sites, "for_each_active_account", for_each)
    monkeypatch.setattr(jd_health_check, "for_each_active_account", for_each)


def test_the_career_tick_skips_an_account_whose_manual_scan_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The manual scan was started by a web worker, the tick runs in a Celery worker;
    they only agree through the shared registry."""
    install_everything_running_registry()
    _accounts(monkeypatch, 1, 2)
    delays = RecordedDelays()
    monkeypatch.setattr(career_sites.run_career_site_scan_task, "delay", delays)
    sites_due = SimpleNamespace(get_due_automatic=lambda user_id: [{"id": 1}])
    monkeypatch.setattr(
        career_sites, "get_repos", lambda user_id=None: _Repos({"career_sites": sites_due})
    )
    career_sites._submit_career_scan(1, "manual")  # what POST /api/career-scan/run does

    career_sites.career_site_scan_tick()

    assert [kwargs.get("trigger") for _args, kwargs in delays.calls] == ["manual", "scheduled"]
    assert delays.calls[1][0] == (2,)


def test_the_health_check_tick_runs_only_where_due_and_not_already_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_everything_running_registry()
    _accounts(monkeypatch, 1, 2, 3)
    delays = RecordedDelays()
    monkeypatch.setattr(jd_health_check.run_jd_health_check_task, "delay", delays)
    monkeypatch.setattr(jd_health_check, "jd_health_check_should_run_now", lambda user_id: user_id != 3)
    jd_health_check.jd_health_check_tick()
    assert [args for args, _kwargs in delays.calls] == [(1,), (2,)]
    delays.calls.clear()

    jd_health_check.jd_health_check_tick()  # both still running: nothing new

    assert delays.calls == []


class _Repos:
    def __init__(self, repos: dict[str, Any]) -> None:
        self._repos = repos

    def __enter__(self) -> dict[str, Any]:
        return self._repos

    def __exit__(self, *_exc: object) -> None:
        return None


# ─── the sweep needs the owner ──────────────────────────────────────────────────


def test_the_sweep_refuses_a_role_rls_applies_to(pg_dsn: str) -> None:
    """As the runtime role it would see no account's rows and delete nothing, every
    time, with no error -- so it must refuse instead."""
    with pytest.raises(AppRoleConfigError, match="DATABASE_OWNER_URL"):
        run_data_lifecycle_sweep(runtime_database_url(pg_dsn))

