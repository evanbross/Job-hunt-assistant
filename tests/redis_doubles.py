# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-12
"""A Redis client that can never connect, for tests of behaviour during an outage."""

from __future__ import annotations

import redis

from backend.security.redis_tls import fail_fast_retry


# ai: claude-opus-5-5 | 2026-11-12
def unreachable_redis() -> redis.Redis:
    """A client pointed at a closed port, with the app's own retry policy.

    Using fail_fast_retry() (what app_redis_client() applies) keeps these tests
    exercising production's retry behaviour, and keeps each call to ~0.4 s instead
    of redis-py's default ~10 s of retries with backoff."""
    return redis.Redis(
        host="127.0.0.1",
        port=1,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
        retry=fail_fast_retry(),
    )
