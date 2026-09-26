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

"""One user's saved Greenhouse browser session (Playwright storage state).

The state is the login cookies for that user's own Greenhouse account, so it is
stored encrypted in their own row rather than in a file every account shares.
Each operation opens its own short DB connection: callers run inside long
Playwright sessions and must never hold one open across browser work."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from backend.crypto import decrypt_secret, encrypt_secret
from backend.utils.utils import get_repos


# ai: claude-sonnet-5 | 2026-09-26
class GreenhouseSessionStore:
    def __init__(self, user_id: int) -> None:
        self._user_id = user_id

    def exists(self) -> bool:
        with get_repos() as repos:
            return repos["greenhouse_sessions"].get(self._user_id) is not None

    def load(self) -> dict[str, Any] | None:
        with get_repos() as repos:
            row = repos["greenhouse_sessions"].get(self._user_id)
        if row is None:
            return None
        return json.loads(decrypt_secret(row["state_encrypted"]))

    def save(self, state: dict[str, Any]) -> None:
        with get_repos() as repos:
            repos["greenhouse_sessions"].upsert(
                self._user_id,
                encrypt_secret(json.dumps(state)),
                datetime.now(UTC).isoformat(timespec="seconds"),
            )
            repos["conn"].commit()

    def clear(self) -> bool:
        with get_repos() as repos:
            removed = repos["greenhouse_sessions"].delete(self._user_id)
            repos["conn"].commit()
        return removed
