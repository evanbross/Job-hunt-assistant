# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Sliding-window throttling shared by every worker process.

Counters live in Redis so that N gunicorn workers (and the extension listener) enforce one
limit between them rather than N. A `Policy` names a limit; the caller chooses what to key
it on (an address, an account, an address+account pair), and the same request usually
checks several policies at once.

If Redis cannot be reached the limiter degrades to per-process in-memory counters and logs
a warning, then retries Redis after a short pause. That trades some strictness during an
outage for availability: failing closed would lock every user out of login because a cache
was down, and failing open would remove the protection exactly when it might be needed."""

from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol, TypeVar

import redis

from backend.security.redis_tls import app_redis_client

logger = logging.getLogger(__name__)

_KEY_PREFIX = "jha:rl"
_MEMORY_SWEEP_THRESHOLD = 50_000
_REDIS_RETRY_SECONDS = 30.0
_REDIS_TIMEOUT_SECONDS = 0.5

T = TypeVar("T")


@dataclass(frozen=True)
class Policy:
    """At most `limit` events per `window_seconds`, counted under `name`."""

    name: str
    limit: int
    window_seconds: int


@dataclass(frozen=True)
class Decision:
    blocked: bool
    retry_after: int = 0


ALLOWED = Decision(blocked=False)


# ai: claude-sonnet-5 | 2026-09-26
def hash_identifier(value: str, *, casefold: bool = True) -> str:
    """Key material for anything sensitive (an email address, a bearer token): the store
    never sees the value. Emails compare case-insensitively; tokens must not."""
    normalized = value.strip().casefold() if casefold else value
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


class CounterBackend(Protocol):
    def count(self, key: str, window: int, now: float) -> tuple[int, float | None]:
        """(events inside the window, timestamp of the oldest one)."""
        ...

    def add(self, key: str, window: int, now: float) -> None: ...

    def delete(self, key: str) -> None: ...


class MemoryBackend:
    def __init__(self) -> None:
        self._events: dict[str, list[float]] = {}
        self._expires: dict[str, float] = {}
        self._lock = threading.Lock()

    def count(self, key: str, window: int, now: float) -> tuple[int, float | None]:
        with self._lock:
            events = self._prune(key, window, now)
            return len(events), (events[0] if events else None)

    def add(self, key: str, window: int, now: float) -> None:
        with self._lock:
            events = self._prune(key, window, now)
            events.append(now)
            self._events[key] = events
            self._expires[key] = now + window
            if len(self._events) > _MEMORY_SWEEP_THRESHOLD:
                self._sweep(now)

    def delete(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)
            self._expires.pop(key, None)

    def _prune(self, key: str, window: int, now: float) -> list[float]:
        events = [t for t in self._events.get(key, []) if t > now - window]
        if events:
            self._events[key] = events
        else:
            self._events.pop(key, None)
        return events

    def _sweep(self, now: float) -> None:
        # Bounds memory when a caller sprays unique keys. A key expires when its newest
        # event leaves that key's own window, whatever policy triggered the sweep.
        for key in [k for k, expires in self._expires.items() if expires <= now]:
            self._events.pop(key, None)
            del self._expires[key]


class RedisBackend:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def count(self, key: str, window: int, now: float) -> tuple[int, float | None]:
        pipe = self._client.pipeline(transaction=True)
        pipe.zremrangebyscore(key, "-inf", now - window)
        pipe.zcard(key)
        pipe.zrange(key, 0, 0, withscores=True)
        _, total, oldest = pipe.execute()
        return int(total), (float(oldest[0][1]) if oldest else None)

    def add(self, key: str, window: int, now: float) -> None:
        pipe = self._client.pipeline(transaction=True)
        pipe.zadd(key, {f"{now}:{uuid.uuid4().hex[:8]}": now})
        pipe.expire(key, window + 1)
        pipe.execute()

    def delete(self, key: str) -> None:
        self._client.delete(key)


class RateLimiter:
    def __init__(
        self,
        primary: CounterBackend | None = None,
        *,
        clock: Callable[[], float] = time.time,
        retry_seconds: float = _REDIS_RETRY_SECONDS,
    ) -> None:
        self._primary = primary
        self._fallback = MemoryBackend()
        self._clock = clock
        self._retry_seconds = retry_seconds
        self._primary_down_until = 0.0

    @classmethod
    def from_app_redis(cls) -> RateLimiter:
        """Limiter backed by the app's Redis. Building the client does not connect, so an
        unreachable Redis shows up as degraded operation, not a failure to start."""
        client = app_redis_client(
            socket_timeout=_REDIS_TIMEOUT_SECONDS,
            socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
        )
        return cls(RedisBackend(client))

    @staticmethod
    def _key(policy: Policy, identifier: str) -> str:
        return f"{_KEY_PREFIX}:{policy.name}:{identifier}"

    def _run(self, operation: Callable[[CounterBackend], T]) -> T:
        now = self._clock()
        if self._primary is not None and now >= self._primary_down_until:
            try:
                return operation(self._primary)
            except (redis.RedisError, OSError) as exc:
                self._primary_down_until = now + self._retry_seconds
                logger.warning(
                    "Rate-limit store unreachable (%s); using per-process counters for %ds",
                    exc,
                    int(self._retry_seconds),
                )
        return operation(self._fallback)

    def _decide(self, policy: Policy, identifier: str, *, tolerated: int) -> Decision:
        key = self._key(policy, identifier)
        now = self._clock()
        total, oldest = self._run(lambda backend: backend.count(key, policy.window_seconds, now))
        if total <= tolerated:
            return ALLOWED
        # The key frees up when its oldest counted event leaves the window.
        wait = policy.window_seconds - (now - oldest) if oldest is not None else policy.window_seconds
        return Decision(blocked=True, retry_after=max(1, math.ceil(wait)))

    def check(self, policy: Policy, identifier: str) -> Decision:
        """Blocked once `limit` events are on record for this key."""
        return self._decide(policy, identifier, tolerated=policy.limit - 1)

    def record(self, policy: Policy, identifier: str) -> None:
        key = self._key(policy, identifier)
        now = self._clock()
        self._run(lambda backend: backend.add(key, policy.window_seconds, now))

    def reset(self, policy: Policy, identifier: str) -> None:
        key = self._key(policy, identifier)
        self._run(lambda backend: backend.delete(key))

    def hit(self, policy: Policy, identifier: str) -> Decision:
        """Record one event, then report whether it went over the limit. For request-rate
        limits, where every request counts rather than only failures."""
        self.record(policy, identifier)
        return self._decide(policy, identifier, tolerated=policy.limit)

    def check_all(self, checks: Iterable[tuple[Policy, str]]) -> Decision:
        """Blocked if any key is; `retry_after` is the longest wait among the blocked ones."""
        worst = ALLOWED
        for policy, identifier in checks:
            decision = self.check(policy, identifier)
            if decision.blocked and decision.retry_after >= worst.retry_after:
                worst = decision
        return worst

    def record_all(self, checks: Iterable[tuple[Policy, str]]) -> None:
        for policy, identifier in checks:
            self.record(policy, identifier)


_limiter: RateLimiter | None = None
_limiter_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    global _limiter
    with _limiter_lock:
        if _limiter is None:
            _limiter = RateLimiter.from_app_redis()
        return _limiter


def set_rate_limiter(limiter: RateLimiter | None) -> None:
    global _limiter
    with _limiter_lock:
        _limiter = limiter
