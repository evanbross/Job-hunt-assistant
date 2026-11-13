# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""Progress of a per-account background run, readable from any web worker.

A run keeps its state in a dataclass of its own (one per run, so one account's run
never shows up in another's status) and publishes it as the Celery task's PROGRESS
meta; the status route finds the account's last run through backend/active_tasks.py
and reads that meta back, or the task's return value once it has finished.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any, Generic, TypeVar

from celery import Task
from celery.result import AsyncResult

from backend.active_tasks import get_active_tasks
from backend.celery_app import celery_app

S = TypeVar("S")


class TaskProgress(Generic[S]):
    """A run's state, published each time an `update()` block finishes."""

    def __init__(self, state: S, publish: Callable[[dict[str, Any]], None]) -> None:
        self.state = state
        self._publish = publish

    @classmethod
    def for_task(cls, task: Task, state: S) -> TaskProgress[S]:
        """Publishes as *task*'s PROGRESS meta. Called directly (a test, or a
        synchronous caller) the task has no id and nothing is published."""

        def publish(meta: dict[str, Any]) -> None:
            if task.request.id is not None:
                task.update_state(state="PROGRESS", meta=meta)

        return cls(state, publish)

    @contextmanager
    def update(self) -> Iterator[S]:
        yield self.state
        self._publish(asdict(self.state))  # type: ignore[call-overload]


def run_status(kind: str, user_id: int, idle: dict[str, Any]) -> dict[str, Any]:
    """The account's last run of *kind*, as a status payload shaped like *idle*.

    Running: the latest published progress. Finished: the task's return value.
    Never run, or its result has expired: *idle*. Died without returning (the
    worker was killed): *idle* plus the error."""
    active_tasks = get_active_tasks()
    record = active_tasks.latest(kind, user_id)
    if record is None:
        return idle
    result = AsyncResult(record.task_id, app=celery_app)
    state = result.state
    if active_tasks.is_running(record, state):
        meta = result.info if isinstance(result.info, dict) else {}
        return {**idle, **meta, "running": True}
    # PENDING that is no longer "just queued": the result backend has nothing on
    # this task any more (expired), so there is nothing to report.
    if state == "PENDING":
        return idle
    if state == "SUCCESS":
        final = result.result if isinstance(result.result, dict) else {}
        return {**idle, **final, "running": False}
    error = str(result.result or "unknown error")
    return {**idle, "running": False, "error": error, "message": f"Failed unexpectedly: {error}"}
