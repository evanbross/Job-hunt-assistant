# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""backend/email/google_oauth_pending.py: a started Google OAuth flow, found by the
callback whichever web worker it lands on, exactly once.

Checked against the in-memory store and the real Redis store (skipped when no Redis is
reachable). "Another process" is a second store with its own Redis client."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator

import pytest
import redis

import config
from backend.email.google_oauth_pending import (
    PENDING_AUTH_TTL_SECONDS,
    MemoryPendingAuthStore,
    PendingAuthStore,
    PendingGoogleAuth,
    RedisPendingAuthStore,
)

_FLOW = PendingGoogleAuth(verifier="pkce-verifier", user_id=7, return_origin="http://localhost:5174")


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(config.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)


@pytest.fixture
def redis_prefix() -> Iterator[str]:
    client = _redis_client()
    try:
        client.ping()
    except (redis.RedisError, OSError):
        pytest.skip("no Redis reachable at REDIS_URL")
    prefix = f"jha:test-oauth:{uuid.uuid4().hex}"  # never the running app's keys
    yield prefix
    for key in client.scan_iter(f"{prefix}*"):
        client.delete(key)


@pytest.fixture(params=["memory", "redis"])
def make_store(request: pytest.FixtureRequest) -> Callable[[], PendingAuthStore]:
    """A factory: each call is another web worker looking at the same shared state."""
    if request.param == "memory":
        shared = MemoryPendingAuthStore()
        return lambda: shared
    prefix = request.getfixturevalue("redis_prefix")
    return lambda: RedisPendingAuthStore(_redis_client(), key_prefix=prefix)


def test_the_callback_finds_a_flow_another_worker_started(
    make_store: Callable[[], PendingAuthStore],
) -> None:
    starting_worker, callback_worker = make_store(), make_store()
    starting_worker.put("state-1", _FLOW)

    assert callback_worker.take("state-1") == _FLOW


def test_a_state_token_is_good_for_one_exchange(make_store: Callable[[], PendingAuthStore]) -> None:
    store = make_store()
    store.put("state-1", _FLOW)
    store.take("state-1")

    assert make_store().take("state-1") is None


def test_an_unknown_state_finds_nothing(make_store: Callable[[], PendingAuthStore]) -> None:
    make_store().put("state-1", _FLOW)

    assert make_store().take("someone-elses-guess") is None


def test_racing_callbacks_get_the_verifier_once(make_store: Callable[[], PendingAuthStore]) -> None:
    make_store().put("state-1", _FLOW)
    gate = threading.Barrier(4)
    results: list[PendingGoogleAuth | None] = []
    lock = threading.Lock()

    def callback() -> None:
        store = make_store()
        gate.wait()
        found = store.take("state-1")
        with lock:
            results.append(found)

    threads = [threading.Thread(target=callback) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert sorted(results, key=lambda r: r is None) == [_FLOW, None, None, None]


def test_redis_flows_expire(redis_prefix: str) -> None:
    client = _redis_client()
    RedisPendingAuthStore(client, key_prefix=redis_prefix).put("state-1", _FLOW)

    ttl = client.ttl(f"{redis_prefix}:state-1")

    assert 0 < ttl <= PENDING_AUTH_TTL_SECONDS


def test_an_unreachable_redis_raises_rather_than_losing_the_flow_silently() -> None:
    dead = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2, socket_timeout=0.2)
    store = RedisPendingAuthStore(dead)

    with pytest.raises(redis.RedisError):
        store.put("state-1", _FLOW)
    with pytest.raises(redis.RedisError):
        store.take("state-1")
