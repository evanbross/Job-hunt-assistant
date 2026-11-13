# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Progress of each captured-page import, readable from any process.

An import is queued by whichever process received the capture -- the web app (a capture
pasted in the UI) or the browser-extension listener -- and polled through the web app's
/api/import/status. In a hosted deployment those are different processes (and several
gunicorn workers), so the state lives in Redis, next to the Celery result backend the
status route already reads, the same way backend/security/task_ownership.py keeps task
owners. Moved here from a dict in routes/import_.py on 2026-11-06
(docs/cloud-deployment-readiness-plan.md 1.3/1.5).

If Redis is unreachable, writes are logged and dropped and reads find nothing: the import
itself still runs (Celery needs the same Redis anyway), only its progress display is lost.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Protocol

import redis

from backend.security.redis_tls import app_redis_client

logger = logging.getLogger(__name__)

# How many imports each account keeps, newest first, and for how long. Status pollers only
# ever need the newest few; finished entries just stop mattering after a while.
MAX_TRACKED_IMPORTS_PER_USER = 50
_TTL_SECONDS = 24 * 3600
_KEY_PREFIX = "jha:import"


@dataclass
class ImportProgressState:
    running: bool
    phase: str
    progress: int
    message: str
    trigger: str
    started_at: str
    # ai: claude-opus-5-5 | 2026-10-14 -- owner of this import; /import/status and
    # /import/reset only ever see the calling user's entries.
    user_id: int
    total: int = 0
    result: dict[str, Any] | None = None
    current_job_url: str = ""
    current_platform: str = ""
    capture_source: dict[str, Any] | None = None
    capture_origin: dict[str, Any] | None = None  # ai: claude-opus-5-5 | 2026-10-14
    steps: list[dict[str, str]] = field(default_factory=list)
    task_id: str | None = None  # ai: claude-sonnet-5 | 2026-08-12 — set once the Celery task is dispatched
    task_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "phase": self.phase,
            "progress": self.progress,
            "message": self.message,
            "total": self.total,
            "result": self.result,
            "current_job_url": self.current_job_url,
            "current_platform": self.current_platform,
            "trigger": self.trigger,
            "started_at": self.started_at,
            "capture_source": self.capture_source,
            "capture_origin": self.capture_origin,
            "steps": self.steps or import_steps(self.phase, self.running),
            "task_id": self.task_id,
            "task_ids": self.task_ids,
        }


def import_steps(phase: str, running: bool) -> list[dict[str, str]]:
    """The three-step progress display (received / filtered / matched) for a phase."""
    step_keys = ["receiving", "filtering", "matching"]
    labels = {
        "receiving": "Received",
        "filtering": "Filtered",
        "matching": "Matched",
    }
    normalized_phase = "matching" if phase in {"processing", "done"} else phase
    active_index = next(
        (index for index, key in enumerate(step_keys) if key == normalized_phase),
        0,
    )

    steps: list[dict[str, str]] = []
    for index, key in enumerate(step_keys):
        status = "pending"
        if phase == "done" or index < active_index:
            status = "complete"
        elif index == active_index and running:
            status = "active"
        elif index == active_index and phase == "error":
            status = "error"
        steps.append({"key": key, "label": labels[key], "status": status})
    return steps


def _started_at_score(started_at: str) -> float:
    try:
        return datetime.fromisoformat(started_at).timestamp()
    except ValueError:
        return 0.0


class ImportProgressStore(Protocol):
    def put(self, import_id: str, state: ImportProgressState) -> None: ...

    def update(self, import_id: str, fields: dict[str, Any]) -> None: ...

    def get(self, import_id: str) -> ImportProgressState | None: ...

    def ids_for_user(self, user_id: int) -> list[str]: ...

    def delete(self, import_id: str) -> None: ...


class MemoryImportProgressStore:
    """In-process store: tests, and a single-process run with no Redis."""

    def __init__(self) -> None:
        self._states: dict[str, ImportProgressState] = {}
        self._lock = threading.Lock()

    def put(self, import_id: str, state: ImportProgressState) -> None:
        with self._lock:
            self._states[import_id] = ImportProgressState(**asdict(state))
            own = sorted(
                (key for key, value in self._states.items() if value.user_id == state.user_id),
                key=lambda key: _started_at_score(self._states[key].started_at),
                reverse=True,
            )
            for key in own[MAX_TRACKED_IMPORTS_PER_USER:]:
                del self._states[key]

    def update(self, import_id: str, fields: dict[str, Any]) -> None:
        with self._lock:
            state = self._states.get(import_id)
            if state is not None:  # never resurrect an import that was reset
                for name, value in fields.items():
                    setattr(state, name, value)

    def get(self, import_id: str) -> ImportProgressState | None:
        with self._lock:
            state = self._states.get(import_id)
            return ImportProgressState(**asdict(state)) if state is not None else None

    def ids_for_user(self, user_id: int) -> list[str]:
        with self._lock:
            return sorted(
                (key for key, value in self._states.items() if value.user_id == user_id),
                key=lambda key: _started_at_score(self._states[key].started_at),
                reverse=True,
            )

    def delete(self, import_id: str) -> None:
        with self._lock:
            self._states.pop(import_id, None)


class RedisImportProgressStore:
    """`<prefix>:<import_id>` holds the state as JSON; `<prefix>-user:<user_id>` is a
    sorted set of that account's import ids by start time. Both expire."""

    def __init__(self, client: redis.Redis, key_prefix: str = _KEY_PREFIX) -> None:
        self._client = client
        self._prefix = key_prefix

    def _state_key(self, import_id: str) -> str:
        return f"{self._prefix}:{import_id}"

    def _user_key(self, user_id: int) -> str:
        return f"{self._prefix}-user:{user_id}"

    def put(self, import_id: str, state: ImportProgressState) -> None:
        user_key = self._user_key(state.user_id)
        pipe = self._client.pipeline()
        pipe.set(self._state_key(import_id), json.dumps(asdict(state)), ex=_TTL_SECONDS)
        pipe.zadd(user_key, {import_id: _started_at_score(state.started_at)})
        pipe.expire(user_key, _TTL_SECONDS)
        pipe.zrange(user_key, 0, -(MAX_TRACKED_IMPORTS_PER_USER + 1))  # the oldest beyond the cap
        overflow = pipe.execute()[-1]
        if overflow:
            pipe = self._client.pipeline()
            pipe.zrem(user_key, *overflow)
            pipe.delete(*(self._state_key(self._decode(member)) for member in overflow))
            pipe.execute()

    def update(self, import_id: str, fields: dict[str, Any]) -> None:
        # One writer per import (the process that queued it), so read-modify-write is
        # safe; xx=True keeps a reset (delete) from being undone by a late update.
        state = self.get(import_id)
        if state is None:
            return
        for name, value in fields.items():
            setattr(state, name, value)
        self._client.set(
            self._state_key(import_id), json.dumps(asdict(state)), ex=_TTL_SECONDS, xx=True
        )

    def get(self, import_id: str) -> ImportProgressState | None:
        raw = self._client.get(self._state_key(import_id))
        return ImportProgressState(**json.loads(raw)) if raw is not None else None

    def ids_for_user(self, user_id: int) -> list[str]:
        return [self._decode(member) for member in self._client.zrevrange(self._user_key(user_id), 0, -1)]

    def delete(self, import_id: str) -> None:
        state = self.get(import_id)
        pipe = self._client.pipeline()
        pipe.delete(self._state_key(import_id))
        if state is not None:
            pipe.zrem(self._user_key(state.user_id), import_id)
        pipe.execute()

    @staticmethod
    def _decode(member: bytes | str) -> str:
        return member.decode() if isinstance(member, bytes) else member


class ImportProgress:
    """Every import's progress, scoped to its owner. Store failures never break an import."""

    def __init__(self, store: ImportProgressStore) -> None:
        self._store = store

    def put(self, import_id: str, state: ImportProgressState) -> None:
        try:
            self._store.put(import_id, state)
        except (redis.RedisError, OSError) as exc:
            logger.warning("Could not record progress of import %s: %s", import_id, exc)

    def update(self, import_id: str, **fields: Any) -> None:
        try:
            self._store.update(import_id, fields)
        except (redis.RedisError, OSError) as exc:
            logger.warning("Could not update progress of import %s: %s", import_id, exc)

    def find(self, import_id: str, user_id: int) -> tuple[str, ImportProgressState] | None:
        """That import if it is `user_id`'s; with no import_id, `user_id`'s newest.

        Another account's import_id reads as unknown, and the fallback never crosses
        accounts. The newest is the latest *started* import: whether one is still running
        is decided from Celery by the status route, not from this state."""
        try:
            if import_id:
                state = self._store.get(import_id)
                return (import_id, state) if state is not None and state.user_id == user_id else None
            for key in self._store.ids_for_user(user_id):
                state = self._store.get(key)  # an id whose state expired is skipped
                if state is not None and state.user_id == user_id:
                    return key, state
            return None
        except (redis.RedisError, OSError) as exc:
            logger.warning("Could not read import progress: %s", exc)
            return None

    def delete_for_user(self, user_id: int, import_id: str = "") -> None:
        """Forget `user_id`'s import `import_id`, or all of their imports."""
        try:
            for key in self._store.ids_for_user(user_id):
                if not import_id or key == import_id:
                    self._store.delete(key)
        except (redis.RedisError, OSError) as exc:
            logger.warning("Could not reset import progress: %s", exc)


_progress: ImportProgress | None = None
_lock = threading.Lock()


def get_import_progress() -> ImportProgress:
    global _progress
    with _lock:
        if _progress is None:
            client = app_redis_client(socket_timeout=1, socket_connect_timeout=1)
            _progress = ImportProgress(RedisImportProgressStore(client))
        return _progress


def set_import_progress(progress: ImportProgress | None) -> None:
    global _progress
    with _lock:
        _progress = progress
