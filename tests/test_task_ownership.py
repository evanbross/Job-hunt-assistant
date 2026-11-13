# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================


from __future__ import annotations

import uuid

import pytest
import redis

import config
from backend.security.task_ownership import (
    MemoryOwnerStore,
    RedisOwnerStore,
    TaskOwnership,
)
from tests.redis_doubles import unreachable_redis

# ai: claude-sonnet-5 | 2026-09-26


def test_only_the_recording_account_owns_a_task() -> None:
    ownership = TaskOwnership(MemoryOwnerStore())
    ownership.record("task-1", 7)

    assert ownership.owns("task-1", 7)
    assert not ownership.owns("task-1", 8)


def test_an_unknown_task_belongs_to_nobody() -> None:
    assert not TaskOwnership(MemoryOwnerStore()).owns("never-recorded", 1)


def test_an_unreachable_store_answers_no_rather_than_yes() -> None:
    dead = unreachable_redis()
    ownership = TaskOwnership(RedisOwnerStore(dead))

    ownership.record("task-1", 7)  # must not raise: the task is already queued

    assert not ownership.owns("task-1", 7)


def test_ownership_survives_across_processes_through_redis() -> None:
    """Two worker processes are two TaskOwnership objects over one Redis."""
    client = redis.Redis.from_url(config.REDIS_URL, socket_timeout=1, socket_connect_timeout=1)
    try:
        client.ping()
    except redis.RedisError:
        pytest.skip("no Redis reachable at REDIS_URL")
    task_id = f"test-{uuid.uuid4().hex}"
    try:
        TaskOwnership(RedisOwnerStore(client)).record(task_id, 7)

        other_worker = TaskOwnership(RedisOwnerStore(client))

        assert other_worker.owns(task_id, 7)
        assert not other_worker.owns(task_id, 8)
        assert 0 < client.ttl(f"jha:task-owner:{task_id}") <= 24 * 3600
    finally:
        client.delete(f"jha:task-owner:{task_id}")
