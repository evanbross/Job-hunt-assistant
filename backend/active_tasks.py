# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""Which background task of a given kind each account last started, shared by every
process.

A "Run now" button in a web worker and a scheduled tick in a Celery worker must agree
on whether a run is already going, or they start two. A module-level dict only answers
that for the process it lives in, so the record lives in Redis instead, keyed
`jha:task:<kind>:<user_id>`, and the check-then-dispatch runs under a Redis lock so two
processes can't both see "nothing running" and both dispatch.

Errors reaching Redis are logged and re-raised: the caller decides what the user sees.
(Dispatching without the check is exactly the double run this exists to prevent, and
Celery's broker is the same Redis, so the dispatch would fail anyway.)
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Protocol

import redis
from celery.result import AsyncResult

from backend.celery_app import celery_app
from backend.security.redis_tls import app_redis_client
from backend.utils.utils import is_celery_task_active

logger = logging.getLogger(__name__)

# Long enough to outlive any run (Celery keeps results for a day, celery_app.py); a
# record older than that has no result left to say whether it is running anyway.
_RECORD_TTL_SECONDS = 24 * 3600
# The lock covers reading the record and the dispatch: usually milliseconds, but a
# dispatch may clear old results first (routes/scan.py's rescan-all). The expiry
# only matters when a process dies holding the lock, so err long: a lock that
# expired mid-dispatch would let a second run in.
_LOCK_TIMEOUT_SECONDS = 120
_LOCK_WAIT_SECONDS = 10


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    submitted_at: float  # time.time(): comparable across processes, unlike monotonic


class ActiveTaskStore(Protocol):
    def get(self, kind: str, user_id: int) -> TaskRecord | None: ...

    def set(self, kind: str, user_id: int, record: TaskRecord) -> None: ...

    def lock(self, kind: str, user_id: int) -> AbstractContextManager[object]: ...


class MemoryActiveTaskStore:
    """One process only: tests, and nothing else."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, int], TaskRecord] = {}
        self._locks: dict[tuple[str, int], threading.Lock] = {}
        self._guard = threading.Lock()

    def get(self, kind: str, user_id: int) -> TaskRecord | None:
        return self._records.get((kind, user_id))

    def set(self, kind: str, user_id: int, record: TaskRecord) -> None:
        self._records[(kind, user_id)] = record

    def lock(self, kind: str, user_id: int) -> AbstractContextManager[object]:
        with self._guard:
            return self._locks.setdefault((kind, user_id), threading.Lock())


class RedisActiveTaskStore:
    def __init__(self, client: redis.Redis, key_prefix: str = "jha:task") -> None:
        self._client = client
        self._prefix = key_prefix

    def _key(self, kind: str, user_id: int) -> str:
        return f"{self._prefix}:{kind}:{user_id}"

    def get(self, kind: str, user_id: int) -> TaskRecord | None:
        raw = self._client.get(self._key(kind, user_id))
        if raw is None:
            return None
        data = json.loads(raw)
        return TaskRecord(task_id=str(data["task_id"]), submitted_at=float(data["submitted_at"]))

    def set(self, kind: str, user_id: int, record: TaskRecord) -> None:
        payload = json.dumps({"task_id": record.task_id, "submitted_at": record.submitted_at})
        self._client.set(self._key(kind, user_id), payload, ex=_RECORD_TTL_SECONDS)

    def lock(self, kind: str, user_id: int) -> AbstractContextManager[object]:
        return self._client.lock(
            f"{self._key(kind, user_id)}:lock",
            timeout=_LOCK_TIMEOUT_SECONDS,
            blocking_timeout=_LOCK_WAIT_SECONDS,
        )


def _celery_state(task_id: str) -> str:
    return AsyncResult(task_id, app=celery_app).state


class ActiveTasks:
    def __init__(
        self, store: ActiveTaskStore, task_state: Callable[[str], str] = _celery_state
    ) -> None:
        self._store = store
        self._task_state = task_state

    def latest(self, kind: str, user_id: int) -> TaskRecord | None:
        """The last task of this kind the account started, running or not."""
        with self._logged(kind, user_id):
            return self._store.get(kind, user_id)

    def is_running(self, record: TaskRecord, state: str | None = None) -> bool:
        """Whether *record*'s task is still going. Pass *state* when the caller has
        already read it (a status route reading the task's progress anyway)."""
        current_state = self._task_state(record.task_id) if state is None else state
        return is_celery_task_active(current_state, record.submitted_at)

    def submit_unless_running(
        self, kind: str, user_id: int, dispatch: Callable[[], str]
    ) -> str | None:
        """Call *dispatch* (which queues the task and returns its id) unless a task of
        this kind is already running for the account. Returns the new id, or None."""
        with self._logged(kind, user_id), self._store.lock(kind, user_id):
            existing = self._store.get(kind, user_id)
            if existing is not None and self.is_running(existing):
                return None
            task_id = dispatch()
            self._store.set(kind, user_id, TaskRecord(task_id=task_id, submitted_at=time.time()))
            return task_id

    @staticmethod
    @contextmanager
    def _logged(kind: str, user_id: int) -> Iterator[None]:
        try:
            yield
        except (redis.RedisError, OSError) as exc:
            logger.warning("Active-task registry unavailable (%s, user %s): %s", kind, user_id, exc)
            raise


_active_tasks: ActiveTasks | None = None
_lock = threading.Lock()


def get_active_tasks() -> ActiveTasks:
    global _active_tasks
    with _lock:
        if _active_tasks is None:
            client = app_redis_client(socket_timeout=2, socket_connect_timeout=2)
            _active_tasks = ActiveTasks(RedisActiveTaskStore(client))
        return _active_tasks


def set_active_tasks(active_tasks: ActiveTasks | None) -> None:
    global _active_tasks
    with _lock:
        _active_tasks = active_tasks
