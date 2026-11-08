# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-08
"""Alert when Celery work piles up (cloud plan 4.3).

A beat tick on the `maintenance` queue counts the tasks waiting in each queue and logs
an ERROR for any queue over QUEUE_DEPTH_ALERT_THRESHOLD -- which the error tracker
turns into an alert (backend/error_tracking.py). It runs on the maintenance worker so
a stuck or dead default worker can't also stop the check that would notice it; if the
maintenance worker itself stops, the error tracker's cron monitor for this tick
reports the missed check-ins instead.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import config
from backend.celery_app import DEFAULT_QUEUE, MAINTENANCE_QUEUE, celery_app
from backend.security.redis_tls import app_redis_client

logger = logging.getLogger(__name__)

# Celery's Redis transport keeps a queue as a Redis list named after it, plus one list
# per non-zero priority step, named <queue>\x06\x16<priority> (kombu's defaults).
_PRIORITY_SEPARATOR = "\x06\x16"
_PRIORITY_STEPS = (3, 6, 9)


def queue_depth(client: Any, queue: str) -> int:
    """Tasks waiting in `queue` (not counting ones a worker has already taken)."""
    keys = [queue, *(f"{queue}{_PRIORITY_SEPARATOR}{step}" for step in _PRIORITY_STEPS)]
    return sum(int(client.llen(key)) for key in keys)


def check_queue_depths(client: Any, queues: Iterable[str], threshold: int) -> dict[str, int]:
    """Each queue's depth; logs an ERROR for each one above `threshold`."""
    depths = {queue: queue_depth(client, queue) for queue in queues}
    for queue, depth in depths.items():
        if depth > threshold:
            logger.error(
                "Celery queue %r has %d tasks waiting (alert threshold %d): its workers are"
                " down, stuck, or too few for the load.",
                queue, depth, threshold,
            )
    return depths


@celery_app.task
def queue_depth_check() -> dict[str, int]:
    client = app_redis_client(socket_timeout=5, socket_connect_timeout=5)
    return check_queue_depths(
        client, (DEFAULT_QUEUE, MAINTENANCE_QUEUE), config.QUEUE_DEPTH_ALERT_THRESHOLD
    )
