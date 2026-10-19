# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================


"""Which account submitted a background (Celery) task.

A task id is a random UUID, but that is not an access control: ids appear in logs and
responses, and a status endpoint that answers for any id would hand one account another's
task state and result. Submitting routes record the owner; status routes ask before
answering, and say "not found" (never "not yours") when the answer is no.

Ownership lives in Redis, next to the task's own state, so any worker process can answer
for a task another process submitted. If the store cannot be reached the answer is "no":
refusing a poll during an outage is better than guessing."""

from __future__ import annotations

import logging
import threading
from typing import Protocol

import redis

import config
from backend.security.redis_tls import redis_ssl_options

logger = logging.getLogger(__name__)

_KEY_PREFIX = "jha:task-owner"
_TTL_SECONDS = 24 * 3600


class OwnerStore(Protocol):
    def set(self, task_id: str, user_id: int) -> None: ...

    def get(self, task_id: str) -> int | None: ...


class MemoryOwnerStore:
    def __init__(self) -> None:
        self._owners: dict[str, int] = {}

    def set(self, task_id: str, user_id: int) -> None:
        self._owners[task_id] = user_id

    def get(self, task_id: str) -> int | None:
        return self._owners.get(task_id)


class RedisOwnerStore:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def set(self, task_id: str, user_id: int) -> None:
        self._client.set(f"{_KEY_PREFIX}:{task_id}", user_id, ex=_TTL_SECONDS)

    def get(self, task_id: str) -> int | None:
        value = self._client.get(f"{_KEY_PREFIX}:{task_id}")
        return int(value) if value is not None else None


# ai: claude-sonnet-5 | 2026-09-26
class TaskOwnership:
    def __init__(self, store: OwnerStore) -> None:
        self._store = store

    def record(self, task_id: str, user_id: int) -> None:
        try:
            self._store.set(task_id, user_id)
        except (redis.RedisError, OSError) as exc:
            # The task is already queued; losing the record only stops the submitter polling it.
            logger.warning("Could not record the owner of task %s: %s", task_id, exc)

    def owns(self, task_id: str, user_id: int) -> bool:
        try:
            return self._store.get(task_id) == user_id
        except (redis.RedisError, OSError) as exc:
            logger.warning("Could not check the owner of task %s: %s", task_id, exc)
            return False


_ownership: TaskOwnership | None = None
_lock = threading.Lock()


def get_task_ownership() -> TaskOwnership:
    global _ownership
    with _lock:
        if _ownership is None:
            client = redis.Redis.from_url(
                config.REDIS_URL,
                socket_timeout=1,
                socket_connect_timeout=1,
                **(redis_ssl_options(config.REDIS_URL, config.REDIS_TLS_CA_FILE) or {}),
            )
            _ownership = TaskOwnership(RedisOwnerStore(client))
        return _ownership


def set_task_ownership(ownership: TaskOwnership | None) -> None:
    global _ownership
    with _lock:
        _ownership = ownership
