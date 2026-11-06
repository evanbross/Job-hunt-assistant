# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""backend/active_tasks.py: which run each account last started, shared by processes.

Checked against the in-memory store and the real Redis store (skipped when no Redis is
reachable). The cross-process cases use two registries with their own Redis clients,
which is what a web worker and a Celery worker are."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterator

import pytest
import redis

import config
from backend.active_tasks import (
    ActiveTasks,
    ActiveTaskStore,
    MemoryActiveTaskStore,
    RedisActiveTaskStore,
    TaskRecord,
)

_KIND = "career_scan"
_USER = 1


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(config.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)


@pytest.fixture
def redis_prefix() -> Iterator[str]:
    client = _redis_client()
    try:
        client.ping()
    except (redis.RedisError, OSError):
        pytest.skip("no Redis reachable at REDIS_URL")
    prefix = f"jha:test-task:{uuid.uuid4().hex}"  # never the running app's keys
    yield prefix
    for key in client.scan_iter(f"{prefix}*"):
        client.delete(key)


@pytest.fixture(params=["memory", "redis"])
def make_store(request: pytest.FixtureRequest) -> Callable[[], ActiveTaskStore]:
    """A factory: each call is another "process" looking at the same shared state."""
    if request.param == "memory":
        shared = MemoryActiveTaskStore()
        return lambda: shared
    prefix = request.getfixturevalue("redis_prefix")
    return lambda: RedisActiveTaskStore(_redis_client(), key_prefix=prefix)


class _Celery:
    """Task states as Celery's result backend would report them."""

    def __init__(self) -> None:
        self.states: dict[str, str] = {}
        self.dispatched: list[str] = []

    def state(self, task_id: str) -> str:
        return self.states.get(task_id, "PENDING")

    def dispatch(self, state: str = "STARTED") -> str:
        task_id = f"task-{len(self.dispatched) + 1}"
        self.states[task_id] = state
        self.dispatched.append(task_id)
        return task_id


def test_nothing_recorded_dispatches(make_store: Callable[[], ActiveTaskStore]) -> None:
    celery = _Celery()
    tasks = ActiveTasks(make_store(), celery.state)

    task_id = tasks.submit_unless_running(_KIND, _USER, celery.dispatch)

    assert task_id == "task-1"
    record = tasks.latest(_KIND, _USER)
    assert record is not None and record.task_id == "task-1"


def test_a_running_task_started_by_another_process_blocks_a_second(
    make_store: Callable[[], ActiveTaskStore],
) -> None:
    celery = _Celery()
    web_worker = ActiveTasks(make_store(), celery.state)
    celery_worker = ActiveTasks(make_store(), celery.state)
    web_worker.submit_unless_running(_KIND, _USER, celery.dispatch)

    assert celery_worker.submit_unless_running(_KIND, _USER, celery.dispatch) is None
    assert celery.dispatched == ["task-1"]


def test_a_finished_task_does_not_block(make_store: Callable[[], ActiveTaskStore]) -> None:
    celery = _Celery()
    tasks = ActiveTasks(make_store(), celery.state)
    tasks.submit_unless_running(_KIND, _USER, celery.dispatch)
    celery.states["task-1"] = "SUCCESS"

    assert tasks.submit_unless_running(_KIND, _USER, celery.dispatch) == "task-2"


def test_runs_are_tracked_per_account_and_per_kind(make_store: Callable[[], ActiveTaskStore]) -> None:
    celery = _Celery()
    tasks = ActiveTasks(make_store(), celery.state)
    tasks.submit_unless_running(_KIND, _USER, celery.dispatch)

    assert tasks.submit_unless_running(_KIND, _USER + 1, celery.dispatch) == "task-2"
    assert tasks.submit_unless_running("jd_health_check", _USER, celery.dispatch) == "task-3"


def test_pending_is_running_only_within_the_pickup_grace(make_store: Callable[[], ActiveTaskStore]) -> None:
    """PENDING is both "queued, not picked up yet" and "no result left at all"; the
    submit time (wall clock, since it crosses processes) tells them apart."""
    celery = _Celery()
    store = make_store()
    tasks = ActiveTasks(store, celery.state)

    store.set(_KIND, _USER, TaskRecord(task_id="queued", submitted_at=time.time()))
    assert tasks.submit_unless_running(_KIND, _USER, celery.dispatch) is None

    store.set(_KIND, _USER, TaskRecord(task_id="expired", submitted_at=time.time() - 3600))
    assert tasks.submit_unless_running(_KIND, _USER, celery.dispatch) == "task-1"


def test_two_processes_racing_dispatch_exactly_once(make_store: Callable[[], ActiveTaskStore]) -> None:
    celery = _Celery()
    gate = threading.Barrier(4)
    results: list[str | None] = []
    lock = threading.Lock()

    def slow_dispatch() -> str:
        time.sleep(0.05)  # widen the window a check-then-dispatch race would need
        return celery.dispatch()

    def submit() -> None:
        tasks = ActiveTasks(make_store(), celery.state)
        gate.wait()
        task_id = tasks.submit_unless_running(_KIND, _USER, slow_dispatch)
        with lock:
            results.append(task_id)

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert len(celery.dispatched) == 1
    assert sorted(results, key=str) == sorted([*celery.dispatched, None, None, None], key=str)


def test_redis_records_expire(redis_prefix: str) -> None:
    client = _redis_client()
    RedisActiveTaskStore(client, key_prefix=redis_prefix).set(
        _KIND, _USER, TaskRecord(task_id="t", submitted_at=time.time())
    )

    ttl = client.ttl(f"{redis_prefix}:{_KIND}:{_USER}")

    assert 0 < ttl <= 24 * 3600


def test_an_unreachable_redis_raises_instead_of_dispatching_unchecked() -> None:
    celery = _Celery()
    dead = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2, socket_timeout=0.2)
    tasks = ActiveTasks(RedisActiveTaskStore(dead), celery.state)

    with pytest.raises(redis.RedisError):
        tasks.submit_unless_running(_KIND, _USER, celery.dispatch)
    assert celery.dispatched == []
