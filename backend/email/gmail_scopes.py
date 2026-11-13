# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-09
"""The Gmail OAuth scopes this deployment asks for
(docs/cloud-deployment-readiness-plan.md 3.1).

Every Gmail scope is "restricted": a hosted app needs Google verification plus a
yearly security assessment, sized by the scopes it requests. So hosted asks only for
gmail.modify (read, mark as read, move to a label). The one thing gmail.send did --
the JD health check's failure email -- goes through the app's own SMTP relay there
(backend/tasks.py). The desktop app keeps gmail.send: it sends from the user's own
Gmail, with no relay to configure.

A function, not a config constant: hosted vs desktop is decided by
backend/app_mode.py, which config.py can't import.
"""

from __future__ import annotations

import config
from backend.app_mode import is_hosted

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def gmail_scopes() -> list[str]:
    """The scopes to request in the consent flow and to load a stored token with."""
    if config.READ_ONLY_MODE:
        return [GMAIL_READONLY_SCOPE]
    if is_hosted():
        return [GMAIL_MODIFY_SCOPE]
    return [GMAIL_MODIFY_SCOPE, GMAIL_SEND_SCOPE]


def missing_gmail_scopes(granted: list[str]) -> list[str]:
    """Required scopes a stored token wasn't granted -- e.g. a desktop token from
    before gmail.send was added. Non-empty means the user should reconnect Gmail."""
    granted_set = set(granted)
    return [scope for scope in gmail_scopes() if scope not in granted_set]
