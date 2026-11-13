# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""backend/scanning/host_fetch_spacing.py: fetches to one host stay a minimum interval
apart even when they come from different worker processes; other hosts never wait.

The spacing rules run against a fake clock; the Redis claim runs against the real Redis
(skipped when none is reachable), with one client per "process"."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator

import pytest
import redis

import config
from backend.scanning.host_fetch_spacing import (
    HostFetchSpacing,
    MemoryHostClaimStore,
    RedisHostClaimStore,
)
from tests.redis_doubles import unreachable_redis

_WTTJ = "app.welcometothejungle.com"


class _FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> _FakeClock:
    return _FakeClock()


@pytest.fixture
def spacing(clock: _FakeClock) -> HostFetchSpacing:
    return HostFetchSpacing(MemoryHostClaimStore(clock=lambda: clock.now), sleep=clock.sleep)


def test_the_first_fetch_to_a_host_never_waits(spacing: HostFetchSpacing, clock: _FakeClock) -> None:
    spacing.wait_turn(_WTTJ, 1.0)

    assert clock.sleeps == []


def test_a_different_host_never_waits(spacing: HostFetchSpacing, clock: _FakeClock) -> None:
    spacing.wait_turn(_WTTJ, 1.0)
    spacing.wait_turn("www.linkedin.com", 1.0)

    assert clock.sleeps == []


def test_the_same_host_within_the_interval_waits_out_the_rest(
    spacing: HostFetchSpacing, clock: _FakeClock
) -> None:
    spacing.wait_turn(_WTTJ, 1.0)
    clock.now += 0.2

    spacing.wait_turn(_WTTJ, 1.0)

    assert clock.sleeps == [pytest.approx(0.8)]


def test_a_zero_interval_never_touches_the_store() -> None:
    class Unreachable:
        def claim(self, host: str, interval_seconds: float) -> float:
            raise AssertionError("claimed with no spacing configured")

    HostFetchSpacing(Unreachable()).wait_turn(_WTTJ, 0.0)


# ─── the real Redis claim ──────────────────────────────────────────────────────


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(config.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)


@pytest.fixture
def redis_prefix() -> Iterator[str]:
    client = _redis_client()
    try:
        client.ping()
    except (redis.RedisError, OSError):
        pytest.skip("no Redis reachable at REDIS_URL")
    prefix = f"jha:test-host-fetch:{uuid.uuid4().hex}"  # never the running app's keys
    yield prefix
    for key in client.scan_iter(f"{prefix}*"):
        client.delete(key)


def test_a_claim_another_process_holds_makes_this_one_wait(redis_prefix: str) -> None:
    worker_a = RedisHostClaimStore(_redis_client(), key_prefix=redis_prefix)
    worker_b = RedisHostClaimStore(_redis_client(), key_prefix=redis_prefix)

    assert worker_a.claim(_WTTJ, 5.0) == 0.0
    remaining = worker_b.claim(_WTTJ, 5.0)

    assert 4.0 < remaining <= 5.0
    assert worker_b.claim("www.linkedin.com", 5.0) == 0.0


def test_hosts_match_case_insensitively(redis_prefix: str) -> None:
    store = RedisHostClaimStore(_redis_client(), key_prefix=redis_prefix)
    store.claim(_WTTJ, 5.0)

    assert store.claim(_WTTJ.upper(), 5.0) > 0


def test_two_processes_end_up_spaced_apart(redis_prefix: str) -> None:
    worker_a = HostFetchSpacing(RedisHostClaimStore(_redis_client(), key_prefix=redis_prefix))
    worker_b = HostFetchSpacing(RedisHostClaimStore(_redis_client(), key_prefix=redis_prefix))

    worker_a.wait_turn(_WTTJ, 0.3)
    started = time.monotonic()
    worker_b.wait_turn(_WTTJ, 0.3)

    assert time.monotonic() - started >= 0.25


def test_an_unreachable_redis_raises_instead_of_fetching_unpaced() -> None:
    dead = unreachable_redis()

    with pytest.raises(redis.RedisError):
        HostFetchSpacing(RedisHostClaimStore(dead)).wait_turn(_WTTJ, 1.0)
