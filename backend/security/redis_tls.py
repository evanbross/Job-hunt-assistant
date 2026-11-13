# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""TLS options for the Celery broker / result backend, and the app's Redis clients.

A `rediss://` URL turns on TLS, but Celery's default for it does not verify the
server certificate, which defeats most of the point. This returns options that
require a valid certificate chain, and refuses to proceed without a CA file to
check it against rather than silently falling back to no verification."""

from __future__ import annotations

import ssl
from typing import Any
from urllib.parse import urlparse

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

import config


# ai: claude-sonnet-5 | 2026-09-26
def redis_ssl_options(redis_url: str, ca_file: str) -> dict[str, Any] | None:
    """Celery `*_use_ssl` options for *redis_url*, or None for a plain redis:// URL."""
    if urlparse(redis_url).scheme.lower() != "rediss":
        return None
    if not ca_file.strip():
        raise ValueError(
            "REDIS_URL uses rediss:// but REDIS_TLS_CA_FILE is not set. Refusing to connect "
            "without verifying the Redis server certificate."
        )
    return {"ssl_cert_reqs": ssl.CERT_REQUIRED, "ssl_ca_certs": ca_file.strip()}


# ai: claude-opus-5-5 | 2026-11-12
def fail_fast_retry() -> Retry:
    """The retry policy for the app's Redis clients: one immediate retry, no backoff.

    redis-py 6's default is 3 retries with exponential backoff, which turns a caller's
    1-2 s timeout into a ~10-15 s stall per call while Redis is down (measured: a 0.2 s
    timeout took 9.8 s). Every caller handles Redis errors itself (degrades, refuses,
    or reports "unreachable"), so failing fast is what they want. The single retry
    still covers the common blip: a pooled connection dropped by a Redis restart.
    A new instance per client, since Retry is per-client configuration."""
    return Retry(NoBackoff(), 1)


# ai: claude-opus-5-5 | 2026-11-06
def app_redis_client(**options: Any) -> redis.Redis:
    """A client for config.REDIS_URL, verifying the server certificate on rediss://.

    *options* are redis-py's (timeouts, health checks): each caller picks its own.
    Building the client doesn't connect, so an unreachable Redis surfaces per call,
    not at import."""
    tls = redis_ssl_options(config.REDIS_URL, config.REDIS_TLS_CA_FILE) or {}
    options.setdefault("retry", fail_fast_retry())  # ai: claude-opus-5-5 | 2026-11-12
    return redis.Redis.from_url(config.REDIS_URL, **tls, **options)
