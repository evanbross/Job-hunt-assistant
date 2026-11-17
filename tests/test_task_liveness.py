# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-17
"""backend/task_liveness.py: whether the worker that took a task is still alive.

Against the real Redis under a throwaway key prefix (skipped when none is reachable),
and through Celery's real task tracer, so the task_prerun handler is checked against
the request Celery actually builds (its hostname, and prerun firing before STARTED)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
import redis
from celery import Celery
from celery.app.trace import build_tracer

import config
from backend.task_liveness import (
    HEARTBEAT_TTL_SECONDS,
    WorkerLiveness,
    set_worker_liveness,
)

_HOST = "celery@test-host"


@pytest.fixture
def client() -> Iterator[redis.Redis]:
    redis_client = redis.Redis.from_url(config.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    try:
        redis_client.ping()
    except (redis.RedisError, OSError):
        pytest.skip("no Redis reachable at REDIS_URL")
    yield redis_client


@pytest.fixture
def prefix(client: redis.Redis) -> Iterator[str]:
    key_prefix = f"jha:test-liveness:{uuid.uuid4().hex}"  # never the running app's keys
    yield key_prefix
    for key in client.scan_iter(f"{key_prefix}*"):
        client.delete(key)


@pytest.fixture
def liveness(client: redis.Redis, prefix: str) -> Iterator[WorkerLiveness]:
    """Also what the task_prerun handler records into, for the tracer test."""
    worker_liveness = WorkerLiveness(client, key_prefix=prefix)
    set_worker_liveness(worker_liveness)
    yield worker_liveness
    set_worker_liveness(None)


def test_a_run_taken_by_a_live_worker_is_alive(liveness: WorkerLiveness) -> None:
    liveness.heartbeat(_HOST, "boot-1")
    liveness.record_run("task-1", _HOST)

    assert liveness.run_is_alive("task-1") is True


def test_a_restarted_worker_no_longer_runs_what_it_took_before(liveness: WorkerLiveness) -> None:
    """The case that blocked new email scans: the app (and its worker) restarted
    mid-scan, and the scan's PROGRESS state outlived it."""
    liveness.heartbeat(_HOST, "boot-1")
    liveness.record_run("task-1", _HOST)

    liveness.heartbeat(_HOST, "boot-2")

    assert liveness.run_is_alive("task-1") is False


def test_a_worker_that_stopped_beating_no_longer_runs_anything(
    liveness: WorkerLiveness, client: redis.Redis, prefix: str
) -> None:
    liveness.heartbeat(_HOST, "boot-1")
    liveness.record_run("task-1", _HOST)
    assert 0 < client.ttl(f"{prefix}:worker:{_HOST}") <= HEARTBEAT_TTL_SECONDS

    client.delete(f"{prefix}:worker:{_HOST}")  # what the expiry does to a killed worker

    assert liveness.run_is_alive("task-1") is False


def test_a_task_no_worker_recorded_taking_is_not_alive(liveness: WorkerLiveness) -> None:
    """Includes every run started before workers recorded their runs."""
    liveness.heartbeat(_HOST, "boot-1")

    assert liveness.run_is_alive("never-taken") is False


def test_a_run_recorded_before_any_heartbeat_is_not_alive(liveness: WorkerLiveness) -> None:
    liveness.record_run("task-1", _HOST)
    liveness.heartbeat(_HOST, "boot-1")

    assert liveness.run_is_alive("task-1") is False


def test_signing_off_leaves_a_newer_boots_heartbeat(
    liveness: WorkerLiveness, client: redis.Redis, prefix: str
) -> None:
    """An old worker shutting down late must not erase the new one's announcement."""
    liveness.heartbeat(_HOST, "boot-2")

    liveness.sign_off(_HOST, "boot-1")
    assert client.get(f"{prefix}:worker:{_HOST}") == b"boot-2"

    liveness.sign_off(_HOST, "boot-2")
    assert client.get(f"{prefix}:worker:{_HOST}") is None


def test_a_task_reads_alive_while_its_worker_runs_it(liveness: WorkerLiveness) -> None:
    """Through Celery's own tracer: the prerun handler records the run under the
    request's hostname before STARTED is stored, so a running task is never seen
    without its record."""
    app = Celery("liveness-test", broker="memory://", backend="cache+memory://")
    app.conf.task_track_started = True
    seen: dict[str, object] = {}

    @app.task(bind=True)
    def probe(self: object) -> None:
        task_id = self.request.id  # type: ignore[attr-defined]
        seen["state"] = app.backend.get_state(task_id)
        seen["alive"] = liveness.run_is_alive(task_id)

    liveness.heartbeat(_HOST, "boot-1")
    tracer = build_tracer(probe.name, probe, app=app, hostname=_HOST, eager=False)
    tracer("task-1", (), {}, request={"id": "task-1", "hostname": _HOST})

    assert seen == {"state": "STARTED", "alive": True}

    liveness.heartbeat(_HOST, "boot-2")
    assert liveness.run_is_alive("task-1") is False


def test_an_eager_call_records_nothing(
    liveness: WorkerLiveness, client: redis.Redis, prefix: str
) -> None:
    """A task called eagerly (tests, synchronous callers) runs in no worker."""
    app = Celery("liveness-eager-test", broker="memory://", backend="cache+memory://")

    @app.task
    def probe() -> None:
        return None

    probe.apply(task_id="eager-1")

    assert client.get(f"{prefix}:taskrun:eager-1") is None
