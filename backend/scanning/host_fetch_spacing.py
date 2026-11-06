# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""Keep successive fetches to one host a minimum interval apart, across every process.

Some job sites (app.welcometothejungle.com) start answering HTTP 429 after a burst of
requests. The closed-req scan runs in Celery workers, several processes at once and
possibly for several accounts, so a per-process "last fetched at" only paces each
process on its own. The claim lives in Redis instead: `SET NX PX` succeeds for exactly
one caller per interval, and the rest wait out the key's remaining TTL.

Errors reaching Redis are logged and re-raised: fetching unpaced is what this exists
to prevent, and Celery's broker is the same Redis anyway.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Protocol

import redis

from backend.security.redis_tls import app_redis_client

logger = logging.getLogger(__name__)


class HostClaimStore(Protocol):
    def claim(self, host: str, interval_seconds: float) -> float:
        """Take *host* for *interval_seconds* if nobody holds it: returns 0.0. Otherwise
        returns how many seconds are left on the current holder's claim."""
        ...


class MemoryHostClaimStore:
    """One process only: tests, and nothing else."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._held_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def claim(self, host: str, interval_seconds: float) -> float:
        with self._lock:
            now = self._clock()
            remaining = self._held_until.get(host, now) - now
            if remaining > 0:
                return remaining
            self._held_until[host] = now + interval_seconds
            return 0.0


class RedisHostClaimStore:
    def __init__(self, client: redis.Redis, key_prefix: str = "jha:host-fetch") -> None:
        self._client = client
        self._prefix = key_prefix

    def claim(self, host: str, interval_seconds: float) -> float:
        key = f"{self._prefix}:{host.lower()}"
        try:
            if self._client.set(key, b"1", nx=True, px=max(1, round(interval_seconds * 1000))):
                return 0.0
            remaining_ms = self._client.pttl(key)
        except (redis.RedisError, OSError) as exc:
            logger.warning("Host fetch spacing unavailable (%s): %s", host, exc)
            raise
        # -2: the claim expired between SET and PTTL; try again straight away.
        return max(int(remaining_ms), 0) / 1000


class HostFetchSpacing:
    def __init__(self, store: HostClaimStore, sleep: Callable[[float], None] = time.sleep) -> None:
        self._store = store
        self._sleep = sleep

    def wait_turn(self, host: str, interval_seconds: float) -> None:
        """Return once this caller may fetch from *host*: at least *interval_seconds*
        after the previous fetch to it from any process. Other hosts never wait."""
        if interval_seconds <= 0:
            return
        while True:
            remaining = self._store.claim(host, interval_seconds)
            if remaining <= 0:
                return
            self._sleep(remaining)


_spacing: HostFetchSpacing | None = None
_lock = threading.Lock()


def get_host_fetch_spacing() -> HostFetchSpacing:
    global _spacing
    with _lock:
        if _spacing is None:
            _spacing = HostFetchSpacing(
                RedisHostClaimStore(app_redis_client(socket_timeout=2, socket_connect_timeout=2))
            )
        return _spacing


def set_host_fetch_spacing(spacing: HostFetchSpacing | None) -> None:
    global _spacing
    with _lock:
        _spacing = spacing
