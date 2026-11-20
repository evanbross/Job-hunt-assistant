# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-10-24
# ai: claude-opus-5-5 | 2026-10-28 — generalized from import_status_events.py:
# a signal now names its topic, so one stream per browser carries every kind.
"""Push signals of the form "this user's <topic> changed", so an open page
re-reads that topic's endpoint when something happened instead of polling it.

A signal carries no data. Each topic's own endpoint stays the single source of
truth, so a dropped connection or a missed message only adds latency: the page
also re-checks whenever its stream (re)connects, and on a slow fallback timer.

Signals travel over Redis pub/sub, one channel per user, because a change can
come from more than one process: an import is received and reset in Flask but
reports each batch's progress from the Celery worker."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Generator, Hashable
from enum import StrEnum
from typing import Protocol

import redis

from backend.security.redis_tls import app_redis_client

logger = logging.getLogger(__name__)

_CHANNEL_PREFIX = "jha:user-events"
_PUBLISH_TIMEOUT_SECONDS = 1.0
_PUBLISH_RETRY_SECONDS = 30.0
_SUBSCRIBER_HEALTH_CHECK_SECONDS = 30


class UserEventTopic(StrEnum):
    """What changed. The value is the event name the page's stream sees."""

    # GET /api/import/status
    IMPORT_STATUS = "import"
    # GET /api/candidate-profile/file-sync
    CANDIDATE_PROFILE_FILE = "candidate-profile-file"
    # GET /api/lead-suggestions  # ai: claude-opus-5-5 | 2026-11-11
    LEAD_SUGGESTIONS = "lead-suggestions"
    # GET /api/jobs/queue and its counts/companies  # ai: claude-opus-5-5 | 2026-11-20
    JOBS = "jobs"


# What listen() returns: a generator, so a caller can close() it to unsubscribe.
TopicStream = Generator[UserEventTopic | None, None, None]


def _channel(user_id: int) -> str:
    return f"{_CHANNEL_PREFIX}:{user_id}"


def _is_message(message: dict | None, message_type: str) -> bool:
    """get_message returns None on a timeout or a health-check reply."""
    return message is not None and message["type"] == message_type


def _topic_of(data: bytes | str) -> UserEventTopic | None:
    """The topic a published message names, or None for one this build
    doesn't know (a newer process sharing the same Redis)."""
    value = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    try:
        return UserEventTopic(value)
    except ValueError:
        return None


class UserEvents(Protocol):
    def publish(self, user_id: int, topic: UserEventTopic) -> None:
        """Announce that *user_id*'s *topic* changed. Never raises."""
        ...

    def listen(self, user_id: int, heartbeat_seconds: float) -> TopicStream:
        """Yield None once the subscription is in place -- so a caller that
        re-reads after that first item can't miss a change made in between --
        then the topic each time one of *user_id*'s topics changes, and None
        after each quiet *heartbeat_seconds*, until the caller closes the
        generator."""
        ...


class RedisUserEvents:
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

    def publish(self, user_id: int, topic: UserEventTopic) -> None:
        """See UserEvents.publish. Best effort: nothing fails or stalls because
        the push channel is down -- after a failure, publishes are skipped for
        _PUBLISH_RETRY_SECONDS, and the page's fallback re-check picks the
        change up instead."""
        now = self._clock()
        if now < self._publisher_down_until:
            return
        try:
            self._publisher.publish(_channel(user_id), topic.value)
        except (redis.RedisError, OSError) as exc:
            self._publisher_down_until = now + _PUBLISH_RETRY_SECONDS
            logger.warning(
                "Could not publish a %s change for user %s (skipping for %ds): %s",
                topic.value,
                user_id,
                int(_PUBLISH_RETRY_SECONDS),
                exc,
            )

    def listen(self, user_id: int, heartbeat_seconds: float) -> TopicStream:
        """See UserEvents.listen. Raises redis.RedisError or OSError if Redis
        can't be reached."""
        pubsub = self._subscriber.pubsub()
        try:
            pubsub.subscribe(_channel(user_id))
            # SUBSCRIBE is only sent here; until Redis confirms it, a change
            # published from another connection can arrive first and be dropped.
            while not _is_message(pubsub.get_message(timeout=heartbeat_seconds), "subscribe"):
                pass
            yield None
            while True:
                message = pubsub.get_message(timeout=heartbeat_seconds)
                if message is not None and _is_message(message, "message"):
                    yield _topic_of(message["data"])
                else:
                    yield None
        finally:
            pubsub.close()


class MemoryUserEvents:
    """Within one process only (tests, or a single-process deployment): a
    Celery worker's publishes never reach it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[int, list[queue.SimpleQueue[UserEventTopic]]] = {}

    def publish(self, user_id: int, topic: UserEventTopic) -> None:
        with self._lock:
            for subscriber in self._subscribers.get(user_id, []):
                subscriber.put(topic)

    def listen(self, user_id: int, heartbeat_seconds: float) -> TopicStream:
        subscriber: queue.SimpleQueue[UserEventTopic] = queue.SimpleQueue()
        with self._lock:
            self._subscribers.setdefault(user_id, []).append(subscriber)
        try:
            yield None
            while True:
                try:
                    yield subscriber.get(timeout=heartbeat_seconds)
                except queue.Empty:
                    yield None
        finally:
            with self._lock:
                self._subscribers[user_id].remove(subscriber)


_events: UserEvents | None = None
_lock = threading.Lock()


def get_user_events() -> UserEvents:
    """The process-wide instance, built on first use. Building the clients
    doesn't connect, so an unreachable Redis surfaces per call, not at import."""
    global _events
    with _lock:
        if _events is None:
            publisher = app_redis_client(
                socket_timeout=_PUBLISH_TIMEOUT_SECONDS,
                socket_connect_timeout=_PUBLISH_TIMEOUT_SECONDS,
            )
            # No socket_timeout: a subscription is idle by design, and
            # get_message(timeout=...) bounds each wait instead.
            subscriber = app_redis_client(
                socket_connect_timeout=_PUBLISH_TIMEOUT_SECONDS,
                health_check_interval=_SUBSCRIBER_HEALTH_CHECK_SECONDS,
            )
            _events = RedisUserEvents(publisher, subscriber)
        return _events


def set_user_events(events: UserEvents | None) -> None:
    """Swap the process-wide instance (tests)."""
    global _events
    with _lock:
        _events = events


# ai: claude-opus-5-5 | 2026-11-20
class _AfterCommitConnection(Protocol):
    def after_commit(self, key: Hashable, callback: Callable[[], None]) -> None: ...


# ai: claude-opus-5-5 | 2026-11-20
def announce_jobs_changed_after_commit(conn: _AfterCommitConnection, user_id: int) -> None:
    """Signal *user_id*'s open pages that their jobs changed, once *conn*'s
    transaction commits (backend/db/connection.py's after_commit) -- not before,
    or a page could re-read and still see the old rows. Called by every jobs
    write in the repositories, so a background import, capture, re-score or
    repair refreshes the Job Queue without each caller remembering to."""
    conn.after_commit(
        (UserEventTopic.JOBS, user_id),
        lambda: get_user_events().publish(user_id, UserEventTopic.JOBS),
    )
