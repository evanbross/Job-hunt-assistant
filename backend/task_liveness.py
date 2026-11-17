# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-17
"""Whether the worker that started a task is still alive to finish it.

A task's stored Celery state says STARTED or PROGRESS until the task itself writes a
final one. A worker that is killed mid-task (stopped from the IDE, crashed, a machine
restart) never writes it, so the result backend keeps saying "running" for the whole
result lifetime (a day), and every "is one already running?" guard
(backend/active_tasks.py) refuses new runs for that long.

So each worker announces itself: on startup it picks a boot id and keeps
`jha:worker:<hostname>` set to it, refreshed every few seconds with a short expiry.
Each task, as it starts, records which worker and which boot of it took it
(`jha:taskrun:<task_id>`). A run is alive only while its worker's key still holds the
same boot id: a restarted worker has a new one, and a dead worker's key expires.

Worker hostnames must be unique per deployment (Celery warns on duplicates, and
app.py never starts a second worker), or two workers would overwrite one key.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any

import redis
from celery import Task
from celery.signals import task_prerun, worker_init, worker_shutdown

from backend.security.redis_tls import app_redis_client

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 10
# Three missed heartbeats: long enough that a busy Redis or a GC pause never makes a
# live worker look dead, short enough that a killed one frees its runs within a minute.
HEARTBEAT_TTL_SECONDS = 3 * HEARTBEAT_INTERVAL_SECONDS
# Matches the result lifetime (celery_app.py result_expires): past it there is no
# stored state left claiming the task runs.
RUN_RECORD_TTL_SECONDS = 24 * 3600


class WorkerLiveness:
    def __init__(self, client: redis.Redis, key_prefix: str = "jha") -> None:
        self._client = client
        self._prefix = key_prefix

    def _worker_key(self, hostname: str) -> str:
        return f"{self._prefix}:worker:{hostname}"

    def _run_key(self, task_id: str) -> str:
        return f"{self._prefix}:taskrun:{task_id}"

    def heartbeat(self, hostname: str, boot_id: str) -> None:
        self._client.set(self._worker_key(hostname), boot_id, ex=HEARTBEAT_TTL_SECONDS)

    def sign_off(self, hostname: str, boot_id: str) -> None:
        """Clear the worker's key on a clean shutdown, unless a newer boot owns it."""
        key = self._worker_key(hostname)
        if self._client.get(key) == boot_id.encode():
            self._client.delete(key)

    def record_run(self, task_id: str, hostname: str) -> None:
        """Note that *hostname*'s current boot took *task_id*. A worker that hasn't
        written its heartbeat records no boot id, and its runs read as dead."""
        boot_id = self._client.get(self._worker_key(hostname))
        payload = json.dumps(
            {"hostname": hostname, "boot_id": boot_id.decode() if boot_id is not None else None}
        )
        self._client.set(self._run_key(task_id), payload, ex=RUN_RECORD_TTL_SECONDS)

    def run_is_alive(self, task_id: str) -> bool:
        """False when no worker recorded taking *task_id*, or the worker that did has
        since stopped or restarted."""
        raw = self._client.get(self._run_key(task_id))
        if raw is None:
            return False
        run = json.loads(raw)
        if run.get("boot_id") is None:
            return False
        current = self._client.get(self._worker_key(str(run["hostname"])))
        return current is not None and current.decode() == run["boot_id"]


_liveness: WorkerLiveness | None = None
_liveness_lock = threading.Lock()


def get_worker_liveness() -> WorkerLiveness:
    global _liveness
    with _liveness_lock:
        if _liveness is None:
            client = app_redis_client(socket_timeout=2, socket_connect_timeout=2)
            _liveness = WorkerLiveness(client)
        return _liveness


def set_worker_liveness(liveness: WorkerLiveness | None) -> None:
    global _liveness
    with _liveness_lock:
        _liveness = liveness


class _Heartbeat:
    """The running worker's own announcement, refreshed from a daemon thread."""

    def __init__(self, liveness: WorkerLiveness, hostname: str) -> None:
        self._liveness = liveness
        self._hostname = hostname
        self._boot_id = uuid.uuid4().hex
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="worker-heartbeat", daemon=True)

    def start(self) -> None:
        # The first beat is written before any task can be taken, so record_run always
        # finds this boot's id.
        self._liveness.heartbeat(self._hostname, self._boot_id)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=HEARTBEAT_INTERVAL_SECONDS)
        try:
            self._liveness.sign_off(self._hostname, self._boot_id)
        except (redis.RedisError, OSError) as exc:
            logger.warning("Could not clear worker heartbeat on shutdown: %s", exc)

    def _run(self) -> None:
        while not self._stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                self._liveness.heartbeat(self._hostname, self._boot_id)
            except (redis.RedisError, OSError) as exc:
                # Keep beating: a Redis blip must not stop the announcements for good.
                logger.warning("Worker heartbeat failed: %s", exc)


_heartbeat: _Heartbeat | None = None


@worker_init.connect
def _start_heartbeat(sender: Any = None, **_kwargs: object) -> None:
    global _heartbeat
    _heartbeat = _Heartbeat(get_worker_liveness(), str(sender.hostname))
    _heartbeat.start()


@worker_shutdown.connect
def _stop_heartbeat(**_kwargs: object) -> None:
    if _heartbeat is not None:
        _heartbeat.stop()


@task_prerun.connect
def _record_run(task_id: str | None = None, task: Task | None = None, **_kwargs: object) -> None:
    """Runs before Celery stores STARTED (celery/app/trace.py), so a task never reads
    as running without a record of who runs it. Eager calls (tests) have no worker."""
    if task_id is None or task is None or task.request.is_eager:
        return
    hostname = task.request.hostname
    if not hostname:
        return
    get_worker_liveness().record_run(task_id, str(hostname))
