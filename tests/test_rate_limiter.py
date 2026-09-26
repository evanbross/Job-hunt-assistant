# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# LICENSE: Personal Use Only
# This source code is made available for personal, non-commercial use only.
# You may read, study, and run this code for personal purposes.
# You may NOT:
#   - Use this code, in whole or in part, in any commercial product or service
#   - Sell, sublicense, or otherwise monetize this code or derivatives of it
#   - Redistribute this code without explicit written permission from ZombieTech LLC
#
# No warranty is provided, express or implied. Use at your own risk.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
import redis

import config
from backend.security.rate_limiter import (
    MemoryBackend,
    Policy,
    RateLimiter,
    RedisBackend,
    hash_identifier,
)
from tests.conftest import FakeClock

# ai: claude-sonnet-5 | 2026-09-26

POLICY = Policy("test-policy", limit=3, window_seconds=60)


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(config.REDIS_URL, socket_timeout=1, socket_connect_timeout=1)


@pytest.fixture
def redis_client() -> Iterator[redis.Redis]:
    """The real Redis the app uses, not a fake: the atomicity and expiry behaviour under
    test is Redis's. Skipped where none is running; keys are unique per test and removed."""
    client = _redis_client()
    try:
        client.ping()
    except redis.RedisError:
        pytest.skip("no Redis reachable at REDIS_URL")
    yield client
    for key in client.scan_iter("jha:rl:test-*"):
        client.delete(key)


@pytest.fixture(params=["memory", "redis"])
def limiter(request: pytest.FixtureRequest, rate_clock: FakeClock) -> RateLimiter:
    if request.param == "memory":
        return RateLimiter(clock=rate_clock)
    client = request.getfixturevalue("redis_client")
    return RateLimiter(RedisBackend(client), clock=rate_clock)


@pytest.fixture
def who() -> str:
    return f"user-{uuid.uuid4().hex}"


# ─── behaviour shared by both backends ──────────────────────────────────────────


def test_allowed_until_the_limit_is_reached(limiter: RateLimiter, who: str) -> None:
    for _ in range(POLICY.limit):
        assert not limiter.check(POLICY, who).blocked
        limiter.record(POLICY, who)

    assert limiter.check(POLICY, who).blocked


def test_keys_are_independent(limiter: RateLimiter, who: str) -> None:
    for _ in range(POLICY.limit):
        limiter.record(POLICY, who)

    assert not limiter.check(POLICY, f"{who}-other").blocked
    assert not limiter.check(Policy("test-other", 3, 60), who).blocked


def test_events_age_out_of_the_window(limiter: RateLimiter, rate_clock: FakeClock, who: str) -> None:
    for _ in range(POLICY.limit):
        limiter.record(POLICY, who)
    rate_clock.advance(POLICY.window_seconds + 1)

    assert not limiter.check(POLICY, who).blocked


def test_the_window_slides_rather_than_resetting_all_at_once(
    limiter: RateLimiter, rate_clock: FakeClock, who: str
) -> None:
    limiter.record(POLICY, who)
    rate_clock.advance(40)
    limiter.record(POLICY, who)
    limiter.record(POLICY, who)
    assert limiter.check(POLICY, who).blocked

    rate_clock.advance(25)  # the first event is now 65s old, the other two 25s

    assert not limiter.check(POLICY, who).blocked


def test_retry_after_says_when_the_oldest_event_expires(
    limiter: RateLimiter, rate_clock: FakeClock, who: str
) -> None:
    limiter.record(POLICY, who)
    rate_clock.advance(20)
    limiter.record(POLICY, who)
    limiter.record(POLICY, who)

    decision = limiter.check(POLICY, who)

    assert decision.blocked
    assert decision.retry_after == 40


def test_reset_clears_a_key(limiter: RateLimiter, who: str) -> None:
    for _ in range(POLICY.limit):
        limiter.record(POLICY, who)
    limiter.reset(POLICY, who)

    assert not limiter.check(POLICY, who).blocked


def test_hit_allows_exactly_the_limit_then_refuses(limiter: RateLimiter, who: str) -> None:
    results = [limiter.hit(POLICY, who).blocked for _ in range(POLICY.limit + 2)]

    assert results == [False, False, False, True, True]


def test_check_all_reports_the_longest_wait(limiter: RateLimiter, rate_clock: FakeClock, who: str) -> None:
    short = Policy("test-short", 1, 10)
    long_ = Policy("test-long", 1, 100)
    limiter.record(short, who)
    limiter.record(long_, who)

    decision = limiter.check_all([(short, who), (long_, who)])

    assert decision.blocked and decision.retry_after == 100


# ─── the Redis backend specifically ─────────────────────────────────────────────


def test_two_limiters_on_one_redis_share_counts(
    redis_client: redis.Redis, rate_clock: FakeClock, who: str
) -> None:
    """Two worker processes behave as two limiter objects over one store."""
    worker_a = RateLimiter(RedisBackend(redis_client), clock=rate_clock)
    worker_b = RateLimiter(RedisBackend(redis_client), clock=rate_clock)

    for _ in range(POLICY.limit):
        worker_a.record(POLICY, who)

    assert worker_b.check(POLICY, who).blocked


def test_redis_keys_expire_on_their_own(redis_client: redis.Redis, rate_clock: FakeClock, who: str) -> None:
    RateLimiter(RedisBackend(redis_client), clock=rate_clock).record(POLICY, who)

    ttl = redis_client.ttl(f"jha:rl:{POLICY.name}:{who}")

    assert 0 < ttl <= POLICY.window_seconds + 1


# ─── when the store is down ─────────────────────────────────────────────────────


def _dead_redis_limiter(clock: FakeClock) -> RateLimiter:
    client = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2, socket_timeout=0.2)
    return RateLimiter(RedisBackend(client), clock=clock, retry_seconds=30)


def test_an_unreachable_store_degrades_to_local_counting_rather_than_failing(
    rate_clock: FakeClock, who: str, caplog: pytest.LogCaptureFixture
) -> None:
    limiter = _dead_redis_limiter(rate_clock)

    for _ in range(POLICY.limit):
        limiter.record(POLICY, who)  # must not raise

    assert limiter.check(POLICY, who).blocked  # still enforced, per process
    assert "unreachable" in caplog.text


def test_the_store_is_not_retried_on_every_request_during_an_outage(
    rate_clock: FakeClock, who: str, caplog: pytest.LogCaptureFixture
) -> None:
    limiter = _dead_redis_limiter(rate_clock)

    for _ in range(5):
        limiter.check(POLICY, who)

    assert caplog.text.count("unreachable") == 1

    rate_clock.advance(31)
    limiter.check(POLICY, who)

    assert caplog.text.count("unreachable") == 2


# ─── in-memory bounds and key hygiene ───────────────────────────────────────────


def test_the_memory_backend_drops_expired_keys_when_it_grows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.security.rate_limiter._MEMORY_SWEEP_THRESHOLD", 10)
    backend = MemoryBackend()
    for n in range(10):
        backend.add(f"stale-{n}", 60, now=0.0)

    backend.add("fresh", 60, now=1_000.0)  # crosses the threshold and triggers a sweep

    assert set(backend._events) == {"fresh"}


def test_a_sweep_keeps_keys_whose_own_window_has_not_elapsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.security.rate_limiter._MEMORY_SWEEP_THRESHOLD", 3)
    backend = MemoryBackend()
    backend.add("long-window", 900, now=0.0)
    backend.add("filler-1", 60, now=0.0)
    backend.add("filler-2", 60, now=0.0)

    backend.add("trigger", 60, now=100.0)  # a 60s call must not evict a 900s key

    assert "long-window" in backend._events


def test_identifiers_are_hashed_and_emails_are_case_insensitive() -> None:
    assert hash_identifier(" Alice@Example.com ") == hash_identifier("alice@example.com")
    assert "alice" not in hash_identifier("alice@example.com")
    assert hash_identifier("AbC", casefold=False) != hash_identifier("abc", casefold=False)
