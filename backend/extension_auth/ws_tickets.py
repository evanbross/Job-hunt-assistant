# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# LICENSE: Personal Use Only
# This source code is made available for personal, non-commercial use only.
# You may read, study, and run this code for personal purposes.
# You may NOT:
#   - Use this code, in whole or in part, in any commercial product or service
#   - Sell, sublicense, or otherwise monetize this code or derivatives of it
#   - Redistribute this code without explicit written permission from ZombieTech LLC
#
# No warranty is provided, express or implied. Use at your own risk.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Short-lived, single-use tickets for authenticating the queue WebSocket.

Browsers cannot attach an Authorization header to a WebSocket handshake, and
putting the long-lived extension token in the URL would leak it into proxy and
access logs. Instead the extension trades its token (over normal HTTP, with
the header) for a ticket that is valid for seconds and can be redeemed once,
then presents the ticket in the WebSocket URL.

State is in-process memory: the listener and the WebSocket server live in one
process, so a shared instance is sufficient. A horizontally scaled deployment
would back this with Redis (SET with EX, GETDEL) behind the same interface."""

from __future__ import annotations

import secrets
import threading
import time

TICKET_TTL_SECONDS = 30
# A single authenticated client re-tickets on each reconnect; this bound only
# exists so a runaway client cannot grow the table without limit.
_MAX_OUTSTANDING_TICKETS = 10_000


# ai: claude-sonnet-5 | 2026-09-26
class WebSocketTicketStore:
    def __init__(self, *, ttl_seconds: int = TICKET_TTL_SECONDS) -> None:
        self._ttl_seconds = ttl_seconds
        self._tickets: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def issue(self, user_id: int) -> str:
        ticket = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            if len(self._tickets) >= _MAX_OUTSTANDING_TICKETS:
                raise RuntimeError("Too many outstanding WebSocket tickets")
            self._tickets[ticket] = (user_id, now + self._ttl_seconds)
        return ticket

    def redeem(self, ticket: str) -> int | None:
        """Return the ticket's user and invalidate it, or None if unknown,
        already used, or expired."""
        now = time.monotonic()
        with self._lock:
            entry = self._tickets.pop(ticket, None)
        if entry is None:
            return None
        user_id, expires_at = entry
        return user_id if now < expires_at else None

    def _purge_expired(self, now: float) -> None:
        expired = [t for t, (_, expires_at) in self._tickets.items() if expires_at <= now]
        for ticket in expired:
            del self._tickets[ticket]
