# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""A push signal for "this user's import status changed", so the page re-reads
GET /api/import/status when something happened instead of polling it every
1.2s for as long as it's open.

The signal carries no status. /api/import/status stays the single source of
truth (the Flask process's tracked imports plus each batch's Celery state), so
a dropped connection or a missed message only adds latency: the page also
re-checks whenever its stream (re)connects, and on a slow fallback timer.

An import changes in two processes -- Flask receives, filters, dispatches and
resets it; the Celery worker reports each batch's progress and completion --
so the signal travels over Redis pub/sub, one channel per user."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from typing import Protocol

import redis

import config
from backend.security.redis_tls import redis_ssl_options

logger = logging.getLogger(__name__)

_CHANNEL_PREFIX = "jha:import-status-changed"
_PUBLISH_TIMEOUT_SECONDS = 1.0
# After a failed publish, skip publishing this long: an import publishes several
# times, and each attempt against a down Redis would wait out its timeout.
_PUBLISH_RETRY_SECONDS = 30.0
# How often an idle subscription PINGs Redis, so a connection that died without
# a reset is noticed (the stream then ends and the browser reconnects).
_SUBSCRIBER_HEALTH_CHECK_SECONDS = 30


def _channel(user_id: int) -> str:
    return f"{_CHANNEL_PREFIX}:{user_id}"


# ai: claude-opus-5-5 | 2026-10-24
def _is_message(message: dict | None, message_type: str) -> bool:
    """get_message returns None on a timeout or a health-check reply."""
    return message is not None and message["type"] == message_type


# ai: claude-opus-5-5 | 2026-10-24
class ImportStatusEvents(Protocol):
    def publish(self, user_id: int) -> None:
        """Announce that *user_id*'s import status changed. Never raises."""

    def listen(self, user_id: int, heartbeat_seconds: float) -> Iterator[bool]:
        """Yield False once the subscription is in place -- so a caller that
        re-reads the status after that first item can't miss a change made in
        between -- then True each time *user_id*'s import status changes and
        False after each quiet *heartbeat_seconds*, until the caller closes
        the generator."""


# ai: claude-opus-5-5 | 2026-10-24
class RedisImportStatusEvents:
    """Shared by every process on the same Redis: what production uses."""

    def __init__(
        self,
        publisher: redis.Redis,
        subscriber: redis.Redis,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._publisher = publisher
        self._subscriber = subscriber
        self._clock = clock
        self._publisher_down_until = 0.0

    def publish(self, user_id: int) -> None:
        """Announce that *user_id*'s import status changed. Best effort: an
        import never fails or stalls because the push channel is down -- after
        a failure, publishes are skipped for _PUBLISH_RETRY_SECONDS, and the
        page's fallback re-check picks the change up instead."""
        now = self._clock()
        if now < self._publisher_down_until:
            return
        try:
            self._publisher.publish(_channel(user_id), "changed")
        except (redis.RedisError, OSError) as exc:
            self._publisher_down_until = now + _PUBLISH_RETRY_SECONDS
            logger.warning(
                "Could not publish an import status change for user %s (skipping for %ds): %s",
                user_id,
                int(_PUBLISH_RETRY_SECONDS),
                exc,
            )

    def listen(self, user_id: int, heartbeat_seconds: float) -> Iterator[bool]:
        """See ImportStatusEvents.listen. Raises redis.RedisError or OSError if
        Redis can't be reached."""
        pubsub = self._subscriber.pubsub()
        try:
            pubsub.subscribe(_channel(user_id))
            # SUBSCRIBE is only sent here; until Redis confirms it, a change
            # published from another connection can arrive first and be dropped.
            while not _is_message(pubsub.get_message(timeout=heartbeat_seconds), "subscribe"):
                pass
            yield False
            while True:
                yield _is_message(pubsub.get_message(timeout=heartbeat_seconds), "message")
        finally:
            pubsub.close()


# ai: claude-opus-5-5 | 2026-10-24
class MemoryImportStatusEvents:
    """Within one process only (tests, or a single-process deployment): a
    Celery worker's publishes never reach it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[int, list[queue.SimpleQueue[None]]] = {}

    def publish(self, user_id: int) -> None:
        with self._lock:
            for subscriber in self._subscribers.get(user_id, []):
                subscriber.put(None)

    def listen(self, user_id: int, heartbeat_seconds: float) -> Iterator[bool]:
        subscriber: queue.SimpleQueue[None] = queue.SimpleQueue()
        with self._lock:
            self._subscribers.setdefault(user_id, []).append(subscriber)
        try:
            yield False
            while True:
                try:
                    subscriber.get(timeout=heartbeat_seconds)
                except queue.Empty:
                    yield False
                else:
                    yield True
        finally:
            with self._lock:
                self._subscribers[user_id].remove(subscriber)


_events: ImportStatusEvents | None = None
_lock = threading.Lock()


# ai: claude-opus-5-5 | 2026-10-24
def get_import_status_events() -> ImportStatusEvents:
    """The process-wide instance, built on first use. Building the clients
    doesn't connect, so an unreachable Redis surfaces per call, not at import."""
    global _events
    with _lock:
        if _events is None:
            ssl_options = redis_ssl_options(config.REDIS_URL, config.REDIS_TLS_CA_FILE) or {}
            publisher = redis.Redis.from_url(
                config.REDIS_URL,
                socket_timeout=_PUBLISH_TIMEOUT_SECONDS,
                socket_connect_timeout=_PUBLISH_TIMEOUT_SECONDS,
                **ssl_options,
            )
            # No socket_timeout: a subscription is idle by design, and
            # get_message(timeout=...) bounds each wait instead.
            subscriber = redis.Redis.from_url(
                config.REDIS_URL,
                socket_connect_timeout=_PUBLISH_TIMEOUT_SECONDS,
                health_check_interval=_SUBSCRIBER_HEALTH_CHECK_SECONDS,
                **ssl_options,
            )
            _events = RedisImportStatusEvents(publisher, subscriber)
        return _events


def set_import_status_events(events: ImportStatusEvents | None) -> None:
    """Swap the process-wide instance (tests)."""
    global _events
    with _lock:
        _events = events
