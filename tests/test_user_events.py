# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-10-24
# ai: claude-opus-5-5 | 2026-10-28 — renamed from test_import_status_events.py
# when signals gained topics.
"""The per-user push signal (backend/user_events.py): Redis pub/sub across
processes, the /api/events stream, and every place an import's status changes
announcing it."""

from __future__ import annotations

import time
import uuid

import pytest
import redis
from celery.signals import task_postrun
from flask import Flask

import backend.tasks as tasks_module
import config
import routes.events as events_routes
import routes.import_ as import_routes
from backend.scanning.import_progress import ImportProgressState
from backend.tasks import run_import_task, run_jd_health_check_task
from backend.user_events import RedisUserEvents, UserEventTopic
from routes.events import events_bp
from routes.import_ import import_bp
from tests.conftest import RecordingUserEvents

_USER_ID = 1
_OTHER_USER_ID = 2
_IMPORT = UserEventTopic.IMPORT_STATUS
_PROFILE_FILE = UserEventTopic.CANDIDATE_PROFILE_FILE


def _unique_user_id() -> int:
    """A channel no running app or other test is using."""
    return 10**9 + uuid.uuid4().int % 10**9


def _redis_client(**options: float) -> redis.Redis:
    return redis.Redis.from_url(config.REDIS_URL, socket_connect_timeout=1, **options)


def _require_redis() -> None:
    try:
        _redis_client(socket_timeout=1).ping()
    except (redis.RedisError, OSError):
        pytest.skip("no Redis reachable at REDIS_URL")


def test_redis_publish_reaches_only_that_users_listener_with_its_topic() -> None:
    _require_redis()
    events = RedisUserEvents(_redis_client(socket_timeout=1), _redis_client())
    user_id = _unique_user_id()
    listener = events.listen(user_id, heartbeat_seconds=0.5)
    try:
        assert next(listener) is None  # subscribed
        events.publish(user_id + 1, _IMPORT)
        assert next(listener) is None  # another user's change: just a quiet heartbeat
        events.publish(user_id, _PROFILE_FILE)
        assert next(listener) == _PROFILE_FILE
        events.publish(user_id, _IMPORT)
        assert next(listener) == _IMPORT
    finally:
        listener.close()


def test_redis_listener_is_subscribed_when_it_says_so() -> None:
    """The first item means Redis confirmed the subscription, not merely that
    SUBSCRIBE was sent: the confirmation is consumed before it, so it can't
    surface afterwards as an early heartbeat."""
    _require_redis()
    events = RedisUserEvents(_redis_client(socket_timeout=1), _redis_client())
    user_id = _unique_user_id()
    listener = events.listen(user_id, heartbeat_seconds=0.5)
    try:
        assert next(listener) is None
        started = time.monotonic()
        assert next(listener) is None
        assert time.monotonic() - started >= 0.4
        events.publish(user_id, _IMPORT)
        assert next(listener) == _IMPORT
    finally:
        listener.close()


def test_redis_listener_ignores_a_topic_it_does_not_know() -> None:
    """A newer process sharing the same Redis may publish a topic this build
    has never heard of; that is a heartbeat here, not a crash."""
    _require_redis()
    events = RedisUserEvents(_redis_client(socket_timeout=1), _redis_client())
    user_id = _unique_user_id()
    listener = events.listen(user_id, heartbeat_seconds=0.5)
    try:
        assert next(listener) is None
        _redis_client(socket_timeout=1).publish(f"jha:user-events:{user_id}", "from-the-future")
        assert next(listener) is None
    finally:
        listener.close()


class _DeadPublisher:
    def __init__(self) -> None:
        self.calls = 0

    def publish(self, channel: str, message: str) -> None:
        self.calls += 1
        raise redis.ConnectionError("Redis is down")


def test_publish_backs_off_after_a_failure_instead_of_waiting_on_every_call() -> None:
    now = [100.0]
    publisher = _DeadPublisher()
    events = RedisUserEvents(publisher, _redis_client(), clock=lambda: now[0])  # type: ignore[arg-type]

    events.publish(_USER_ID, _IMPORT)  # fails; must not raise
    events.publish(_USER_ID, _IMPORT)
    assert publisher.calls == 1

    now[0] += 31
    events.publish(_USER_ID, _IMPORT)
    assert publisher.calls == 2


def _app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(import_bp, url_prefix="/api")
    app.register_blueprint(events_bp, url_prefix="/api")
    return app


def _state(user_id: int) -> ImportProgressState:
    return ImportProgressState(
        running=True, phase="receiving", progress=5, message="", trigger="ui",
        started_at="2026-10-24T10:00:00+00:00", user_id=user_id,
    )


def test_stream_names_each_event_after_its_topic_for_the_caller_only(
    monkeypatch: pytest.MonkeyPatch, isolated_user_events: RecordingUserEvents
) -> None:
    monkeypatch.setattr(events_routes, "_HEARTBEAT_SECONDS", 0.05)
    client = _app().test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = _USER_ID

    response = client.get("/api/events", buffered=False)
    try:
        assert response.mimetype == "text/event-stream"
        chunks = response.iter_encoded()
        assert next(chunks) == b"retry: 10000\n\n"
        # Published before the next pull: only delivered if the stream was
        # already subscribed when it sent its first bytes.
        isolated_user_events.publish(_USER_ID, _IMPORT)
        assert next(chunks) == b"event: import\ndata: changed\n\n"
        isolated_user_events.publish(_USER_ID, _PROFILE_FILE)
        assert next(chunks) == b"event: candidate-profile-file\ndata: changed\n\n"

        isolated_user_events.publish(_OTHER_USER_ID, _IMPORT)
        assert next(chunks) == b": keep-alive\n\n"
    finally:
        response.close()
    assert isolated_user_events._subscribers[_USER_ID] == []  # unsubscribed on close


def test_every_tracked_import_change_is_announced_to_its_owner(
    isolated_user_events: RecordingUserEvents,
) -> None:
    # tests/conftest.py gives this test its own empty import-progress store.
    import_routes._set_import_progress("import-a", _state(_OTHER_USER_ID))
    assert isolated_user_events.published == [(_OTHER_USER_ID, _IMPORT)]

    client = _app().test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = _OTHER_USER_ID
    assert client.post("/api/import/reset", json={}).status_code == 200
    assert isolated_user_events.published == [(_OTHER_USER_ID, _IMPORT)] * 2


class _ProgressTask:
    def __init__(self) -> None:
        self.states: list[str] = []

    def update_state(self, *, state: str, meta: dict) -> None:
        self.states.append(state)


def test_a_worker_progress_report_is_recorded_then_announced(
    isolated_user_events: RecordingUserEvents,
) -> None:
    task = _ProgressTask()
    tasks_module._report_import_progress(task, _USER_ID, {"phase": "matching"})
    assert task.states == ["PROGRESS"]
    assert isolated_user_events.published == [(_USER_ID, _IMPORT)]


def test_a_finished_import_batch_is_announced_after_celery_stores_its_state(
    isolated_user_events: RecordingUserEvents,
) -> None:
    task_postrun.send(
        sender=run_import_task, task_id="t1", task=run_import_task,
        args=(_USER_ID, [], None, {}), kwargs={}, retval={}, state="SUCCESS",
    )
    assert isolated_user_events.published == [(_USER_ID, _IMPORT)]


def test_other_tasks_finishing_announce_nothing(
    isolated_user_events: RecordingUserEvents,
) -> None:
    task_postrun.send(
        sender=run_jd_health_check_task, task_id="t2", task=run_jd_health_check_task,
        args=(_USER_ID,), kwargs={}, retval=None, state="SUCCESS",
    )
    assert isolated_user_events.published == []
