# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-18
"""Put a login identity into a Flask session the way routes/session_auth.py's
_start_session does, for tests that skip the real login route.

get_repos() rejects a session whose session_version doesn't match the account's
(a password change or reset increments it), so a session holding only a user_id
is a revoked session, not a logged-in one."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

# user_accounts.session_version's column default: an account whose password has
# never been changed or reset.
FRESH_ACCOUNT_SESSION_VERSION = 1


def sign_in(
    flask_session: MutableMapping[str, Any],
    user_id: int,
    session_version: int = FRESH_ACCOUNT_SESSION_VERSION,
) -> None:
    flask_session["user_id"] = user_id
    flask_session["session_version"] = session_version
