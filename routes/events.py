# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-10-24
# ai: claude-opus-5-5 | 2026-10-28 — moved here from routes/import_.py when the
# stream started carrying more than import status.
"""The page's one server-sent-events stream (backend/user_events.py)."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import redis
from flask import Blueprint, Response
from flask.typing import ResponseReturnValue

from backend.user_events import get_user_events
from backend.utils.utils import current_user_id

logger = logging.getLogger(__name__)

events_bp = Blueprint("events", __name__)

_HEARTBEAT_SECONDS = 25
_RETRY_MILLISECONDS = 10_000


# ai: claude-opus-5-5 | 2026-11-04 — docstring: server-thread note points at deploy/README.md
@events_bp.route("/events")
def user_event_stream() -> ResponseReturnValue:
    """An event named after the topic (UserEventTopic) each time one of the
    caller's topics changes, so the page re-reads only that topic's endpoint.
    The event is a signal, never the data itself.

    A comment line every _HEARTBEAT_SECONDS keeps proxies from timing the
    stream out and makes a closed tab's stream fail on its next write, which
    ends the generator and its Redis subscription. If Redis is unreachable the
    stream just ends; the browser reconnects after _RETRY_MILLISECONDS and the
    page's fallback re-checks cover the gap. Each open stream holds one server
    thread for as long as the tab is open. The threaded dev server starts a
    thread per connection, so that is harmless there; a fixed-pool server
    (e.g. Waitress) would run out of threads after a few tabs. See
    deploy/README.md "What is not included" #1 for the server requirements."""
    user_id = current_user_id()

    def stream() -> Iterator[str]:
        events = get_user_events().listen(user_id, _HEARTBEAT_SECONDS)
        try:
            # Nothing is sent until the subscription is in place (listen's first
            # item), so the page's re-read on `open` can't miss a change.
            next(events)
            yield f"retry: {_RETRY_MILLISECONDS}\n\n"
            for topic in events:
                yield f"event: {topic.value}\ndata: changed\n\n" if topic else ": keep-alive\n\n"
        except (redis.RedisError, OSError) as exc:
            logger.warning("Event stream for user %s ended: %s", user_id, exc)
        finally:
            events.close()  # unsubscribe now, not whenever the generator is collected

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
