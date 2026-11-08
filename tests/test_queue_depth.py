# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-08
"""backend/queue_depth.py (cloud plan 4.3): counts what Celery's Redis transport has
queued -- checked against messages kombu really publishes, priorities included -- and
logs an ERROR (an error-tracker alert) only past the threshold."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator

import pytest
import redis
from kombu import Connection, Exchange, Queue

import config
from backend import queue_depth as queue_depth_module
from backend.celery_app import QUEUE_DEPTH_CHECK, celery_app
from backend.queue_depth import check_queue_depths, queue_depth


@pytest.fixture
def broker() -> Iterator[tuple[redis.Redis, str]]:
    """The real Redis at REDIS_URL and a queue name no worker consumes; skipped when no
    Redis is reachable. Its keys are deleted afterwards."""
    client = redis.Redis.from_url(config.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    try:
        client.ping()
    except (redis.RedisError, OSError):
        pytest.skip("no Redis reachable at REDIS_URL")
    queue = f"jha-test-depth-{uuid.uuid4().hex}"
    yield client, queue
    for key in client.scan_iter(match=f"{queue}*"):
        client.delete(key)


def test_depth_counts_messages_kombu_publishes_at_every_priority(broker: tuple[redis.Redis, str]) -> None:
    client, name = broker
    exchange = Exchange(name, type="direct")
    queue = Queue(name, exchange, routing_key=name, max_priority=9)
    with Connection(config.REDIS_URL) as conn:
        producer = conn.Producer()
        for priority in (0, 0, 3, 9):
            producer.publish(
                {"n": priority}, exchange=exchange, routing_key=name, declare=[queue], priority=priority
            )

    assert queue_depth(client, name) == 4


class _FakeRedis:
    def __init__(self, lengths: dict[str, int]) -> None:
        self.lengths = lengths

    def llen(self, key: str) -> int:
        return self.lengths.get(key, 0)


def test_only_a_queue_over_the_threshold_raises_an_error(caplog: pytest.LogCaptureFixture) -> None:
    client = _FakeRedis({"celery": 250, "maintenance": 3})

    with caplog.at_level(logging.ERROR, logger=queue_depth_module.__name__):
        depths = check_queue_depths(client, ("celery", "maintenance"), threshold=200)

    assert depths == {"celery": 250, "maintenance": 3}
    [record] = caplog.records
    assert "'celery' has 250 tasks waiting" in record.getMessage()


def test_the_check_runs_every_few_minutes() -> None:
    """(Its queue is checked with the others in tests/test_scheduled_work.py.)"""
    [entry] = [e for e in celery_app.conf.beat_schedule.values() if e["task"] == QUEUE_DEPTH_CHECK]

    assert entry["schedule"].total_seconds() <= 15 * 60
