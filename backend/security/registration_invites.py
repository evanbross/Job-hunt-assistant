# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-07
"""Who may create an account (docs/cloud-deployment-readiness-plan.md 4.6).

With `registration_invite_only` on (the default), only an email an admin invited can
register. The invite is checked twice: when registration starts (before any account row
is written or email sent) and again, by consuming it, in the same transaction that sets
the password -- so an invite revoked after someone started registering also stops the
link already in their inbox. Consuming it means a deleted account needs a new invite.

Emails must already be normalized (backend/security/account_email.py)."""

from __future__ import annotations

from typing import Any

from backend.settings_service import get_setting_bool

INVITE_ONLY_SETTING = "registration_invite_only"


def invite_only(repos: dict[str, Any]) -> bool:
    return get_setting_bool(INVITE_ONLY_SETTING, None, repos, True)


def may_start_registration(email: str, repos: dict[str, Any]) -> bool:
    return not invite_only(repos) or repos["registration_invites"].exists(email)


def consume_invite(email: str, repos: dict[str, Any]) -> bool:
    """Use up *email*'s invite as its registration completes. True if the registration
    may complete: an invite was consumed, or the install isn't invite-only. The caller
    rolls back the registration otherwise."""
    consumed = repos["registration_invites"].remove(email)
    return consumed or not invite_only(repos)
