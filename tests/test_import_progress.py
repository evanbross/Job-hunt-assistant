# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""backend/scanning/import_progress.py: import progress shared across processes.

The same behaviour is checked against the in-memory store and the real Redis store
(skipped when no Redis is reachable), since in a hosted deployment the listener process
writes what a web worker reads, and only Redis can carry that."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
import redis

import config
from backend.scanning.import_progress import (
    MAX_TRACKED_IMPORTS_PER_USER,
    ImportProgress,
    ImportProgressState,
    ImportProgressStore,
    MemoryImportProgressStore,
    RedisImportProgressStore,
)

_USER = 1
_OTHER = 2


def _state(started_at: str, user_id: int = _USER, **fields: object) -> ImportProgressState:
    return ImportProgressState(
        running=True,
        phase="receiving",
        progress=5,
        message="",
        trigger="listener",
        started_at=started_at,
        user_id=user_id,
        **fields,  # type: ignore[arg-type]
    )


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(config.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)


@pytest.fixture(params=["memory", "redis"])
def store(request: pytest.FixtureRequest) -> Iterator[ImportProgressStore]:
    if request.param == "memory":
        yield MemoryImportProgressStore()
        return
    client = _redis_client()
    try:
        client.ping()
    except (redis.RedisError, OSError):
        pytest.skip("no Redis reachable at REDIS_URL")
    prefix = f"jha:test-import:{uuid.uuid4().hex}"  # never the running app's keys
    yield RedisImportProgressStore(client, key_prefix=prefix)
    for key in client.scan_iter(f"{prefix}*"):
        client.delete(key)


def test_a_state_round_trips(store: ImportProgressStore) -> None:
    progress = ImportProgress(store)
    progress.put("a", _state("2026-11-06T10:00:00+00:00", capture_origin={"label": "x"}))

    found = progress.find("a", _USER)

    assert found is not None and found[0] == "a"
    assert found[1].capture_origin == {"label": "x"} and found[1].trigger == "listener"


def test_find_without_an_id_is_the_newest_of_that_account_only(store: ImportProgressStore) -> None:
    progress = ImportProgress(store)
    progress.put("old", _state("2026-11-06T10:00:00+00:00"))
    progress.put("new", _state("2026-11-06T10:05:00+00:00"))
    progress.put("theirs", _state("2026-11-06T10:09:00+00:00", user_id=_OTHER))

    assert progress.find("", _USER)[0] == "new"  # type: ignore[index]
    assert progress.find("theirs", _USER) is None  # another account's id reads as unknown
    assert progress.find("", 99) is None


def test_update_changes_fields_but_never_resurrects_a_reset_import(store: ImportProgressStore) -> None:
    progress = ImportProgress(store)
    progress.put("a", _state("2026-11-06T10:00:00+00:00"))

    progress.update("a", task_id="t1", task_ids=["t1"])
    assert progress.find("a", _USER)[1].task_ids == ["t1"]  # type: ignore[index]

    progress.delete_for_user(_USER, "a")
    progress.update("a", phase="error")
    assert progress.find("a", _USER) is None


def test_reset_without_an_id_clears_only_that_account(store: ImportProgressStore) -> None:
    progress = ImportProgress(store)
    progress.put("mine", _state("2026-11-06T10:00:00+00:00"))
    progress.put("theirs", _state("2026-11-06T10:00:00+00:00", user_id=_OTHER))

    progress.delete_for_user(_USER)

    assert progress.find("mine", _USER) is None
    assert progress.find("theirs", _OTHER) is not None


def test_each_account_keeps_only_its_newest_imports(store: ImportProgressStore) -> None:
    progress = ImportProgress(store)
    total = MAX_TRACKED_IMPORTS_PER_USER + 3
    for i in range(total):
        progress.put(f"i{i}", _state(f"2026-11-06T10:{i:02d}:00+00:00"))

    assert progress.find(f"i{total - 1}", _USER) is not None
    assert progress.find("i0", _USER) is None
    assert len(store.ids_for_user(_USER)) == MAX_TRACKED_IMPORTS_PER_USER


def test_an_unreachable_redis_loses_progress_but_never_raises() -> None:
    dead = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2, socket_timeout=0.2)
    progress = ImportProgress(RedisImportProgressStore(dead, key_prefix="jha:test-import:dead"))

    progress.put("a", _state("2026-11-06T10:00:00+00:00"))
    progress.update("a", phase="error")
    progress.delete_for_user(_USER)

    assert progress.find("a", _USER) is None
