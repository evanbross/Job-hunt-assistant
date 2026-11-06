# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""Google OAuth flows that have started but not come back yet, shared by every web
process.

GET /api/auth/google records the PKCE verifier under a random state token, and the
callback, which Google sends to whichever web worker the load balancer picks, has to
find it. A dict in the starting process only works when there is one process, so the
record lives in Redis with a short expiry, and the callback takes it in one atomic
step, so a state token is good for exactly one exchange.

Errors reaching Redis are logged and re-raised: the route decides what the user sees.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from typing import Protocol

import redis

from backend.security.redis_tls import app_redis_client

logger = logging.getLogger(__name__)

# Long enough to pick an account and approve on Google's consent screen.
PENDING_AUTH_TTL_SECONDS = 600


@dataclass(frozen=True)
class PendingGoogleAuth:
    verifier: str  # PKCE code verifier: never leaves the server
    user_id: int  # the account that started the flow; the callback must match it
    return_origin: str  # where the browser goes back to afterwards


class PendingAuthStore(Protocol):
    def put(self, state: str, pending: PendingGoogleAuth) -> None: ...

    def take(self, state: str) -> PendingGoogleAuth | None:
        """Remove and return the flow for *state*, or None if unknown or expired."""
        ...


class MemoryPendingAuthStore:
    """One process only: tests, and nothing else. Never expires."""

    def __init__(self) -> None:
        self.flows: dict[str, PendingGoogleAuth] = {}
        self._lock = threading.Lock()

    def put(self, state: str, pending: PendingGoogleAuth) -> None:
        with self._lock:
            self.flows[state] = pending

    def take(self, state: str) -> PendingGoogleAuth | None:
        with self._lock:
            return self.flows.pop(state, None)


class RedisPendingAuthStore:
    def __init__(self, client: redis.Redis, key_prefix: str = "jha:oauth:google") -> None:
        self._client = client
        self._prefix = key_prefix

    def _key(self, state: str) -> str:
        return f"{self._prefix}:{state}"

    def put(self, state: str, pending: PendingGoogleAuth) -> None:
        try:
            self._client.set(
                self._key(state), json.dumps(asdict(pending)), ex=PENDING_AUTH_TTL_SECONDS
            )
        except (redis.RedisError, OSError) as exc:
            logger.warning("Could not record a pending Google OAuth flow: %s", exc)
            raise

    def take(self, state: str) -> PendingGoogleAuth | None:
        try:
            # GET and DELETE in one MULTI: two callbacks racing on one state token
            # can't both get the verifier.
            pipe = self._client.pipeline(transaction=True)
            pipe.get(self._key(state))
            pipe.delete(self._key(state))
            raw, _deleted = pipe.execute()
        except (redis.RedisError, OSError) as exc:
            logger.warning("Could not read a pending Google OAuth flow: %s", exc)
            raise
        if raw is None:
            return None
        data = json.loads(raw)
        return PendingGoogleAuth(
            verifier=str(data["verifier"]),
            user_id=int(data["user_id"]),
            return_origin=str(data["return_origin"]),
        )


_store: PendingAuthStore | None = None
_lock = threading.Lock()


def get_pending_auths() -> PendingAuthStore:
    global _store
    with _lock:
        if _store is None:
            _store = RedisPendingAuthStore(
                app_redis_client(socket_timeout=2, socket_connect_timeout=2)
            )
        return _store


def set_pending_auths(store: PendingAuthStore | None) -> None:
    global _store
    with _lock:
        _store = store
