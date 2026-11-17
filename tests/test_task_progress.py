# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""backend/task_progress.py: a run's progress, published by the task and read back by
any web worker's status route, for the account that started it only.

The status cases write real results into Celery's result backend (Redis; skipped when
none is reachable) under fresh task ids, and read them back through AsyncResult, as the
status routes do."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
import redis

from backend.active_tasks import (
    ActiveTasks,
    MemoryActiveTaskStore,
    TaskRecord,
    set_active_tasks,
)
from backend.celery_app import celery_app
from backend.task_progress import WORKER_STOPPED_MESSAGE, TaskProgress, run_status

_KIND = "test_run"


@dataclass
class _RunState:
    running: bool = False
    progress: int = 0
    message: str = "Idle"
    found: list[dict] = field(default_factory=list)
    error: str | None = None


_IDLE = {"running": False, "progress": 0, "message": "Idle", "found": [], "error": None}


# ─── publishing ─────────────────────────────────────────────────────────────────


def test_an_update_is_published_when_its_block_ends() -> None:
    published: list[dict[str, Any]] = []
    progress = TaskProgress(_RunState(), published.append)

    with progress.update() as state:
        state.running = True
        state.progress = 3
        assert published == []  # half-made updates are never visible

    assert published == [{**_IDLE, "running": True, "progress": 3}]


def test_a_task_called_directly_publishes_nothing() -> None:
    """A test or synchronous caller runs the task body with no task id; there is no
    result to update and update_state would fail."""
    calls: list[object] = []
    task = SimpleNamespace(request=SimpleNamespace(id=None), update_state=calls.append)
    progress = TaskProgress.for_task(task, _RunState())  # type: ignore[arg-type]

    with progress.update() as state:
        state.progress = 1

    assert calls == []


# ─── reading it back ────────────────────────────────────────────────────────────


@pytest.fixture
def records() -> MemoryActiveTaskStore:
    """The registry the status routes read (task state from Celery, as in the app);
    conftest's autouse registry fixture clears it afterwards."""
    store = MemoryActiveTaskStore()
    set_active_tasks(ActiveTasks(store, worker_alive=lambda task_id: True))  # ai: claude-opus-5-5 | 2026-11-17
    return store


@pytest.fixture
def backend_task_ids() -> Iterator[list[str]]:
    try:
        celery_app.backend.client.ping()
    except (redis.RedisError, OSError):
        pytest.skip("no Redis reachable for Celery's result backend")
    created: list[str] = []
    yield created
    for task_id in created:
        celery_app.backend.forget(task_id)


def _started(
    records: MemoryActiveTaskStore, created: list[str], user_id: int, state: str, result: object
) -> None:
    task_id = f"test-{uuid.uuid4().hex}"
    created.append(task_id)
    celery_app.backend.store_result(task_id, result, state)
    records.set(_KIND, user_id, TaskRecord(task_id, time.time()))


def test_never_run_is_idle(records: MemoryActiveTaskStore) -> None:
    assert run_status(_KIND, 1, _IDLE) == _IDLE


def test_a_running_task_reports_its_latest_progress(
    records: MemoryActiveTaskStore, backend_task_ids: list[str]
) -> None:
    _started(records, backend_task_ids, 1, "PROGRESS",
             {**_IDLE, "running": True, "progress": 4, "found": [{"id": 9}]})

    status = run_status(_KIND, 1, _IDLE)

    assert status["running"] is True
    assert status["progress"] == 4
    assert status["found"] == [{"id": 9}]


def test_another_accounts_run_is_invisible(
    records: MemoryActiveTaskStore, backend_task_ids: list[str]
) -> None:
    """The old single in-process state showed every account the last run anyone
    started, results (companies, job titles) included."""
    _started(records, backend_task_ids, 1, "PROGRESS",
             {**_IDLE, "running": True, "found": [{"company": "Acme"}]})

    assert run_status(_KIND, 2, _IDLE) == _IDLE


def test_a_finished_task_reports_what_it_returned(
    records: MemoryActiveTaskStore, backend_task_ids: list[str]
) -> None:
    _started(records, backend_task_ids, 1, "SUCCESS",
             {**_IDLE, "progress": 10, "message": "Done: 2 fixed"})

    status = run_status(_KIND, 1, _IDLE)

    assert status == {**_IDLE, "progress": 10, "message": "Done: 2 fixed"}


def test_a_task_that_died_reports_the_error(
    records: MemoryActiveTaskStore, backend_task_ids: list[str]
) -> None:
    _started(records, backend_task_ids, 1, "FAILURE", RuntimeError("worker lost"))

    status = run_status(_KIND, 1, _IDLE)

    assert status["running"] is False
    assert "worker lost" in status["error"]


# ai: claude-opus-5-5 | 2026-11-17
def test_a_run_whose_worker_died_is_reported_stopped_not_running(
    backend_task_ids: list[str],
) -> None:
    """A worker killed mid-run leaves PROGRESS stored for a day. The status must not
    show that last progress as still running, nor print it as the error."""
    store = MemoryActiveTaskStore()
    set_active_tasks(ActiveTasks(store, worker_alive=lambda task_id: False))
    _started(store, backend_task_ids, 1, "PROGRESS",
             {**_IDLE, "running": True, "progress": 35, "message": "email 2/5"})

    status = run_status(_KIND, 1, _IDLE)

    assert status["running"] is False
    assert status["error"] == WORKER_STOPPED_MESSAGE


def test_an_expired_result_is_idle(records: MemoryActiveTaskStore) -> None:
    """PENDING long after submission: the backend no longer has anything to report."""
    records.set(_KIND, 1, TaskRecord(f"gone-{uuid.uuid4().hex}", time.time() - 3600))

    assert run_status(_KIND, 1, _IDLE) == _IDLE
