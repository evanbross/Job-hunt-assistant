# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-09
"""Who a per-account notification email (scan report, JD health check failures)
goes to.

Hosted, these go out through the app's own SMTP relay, under the app's sending
domain. If a user could type any address in, they could point that relay at strangers
and the bounces and spam reports would land on the domain every account's
verification emails depend on. So hosted always sends to the account's own email,
which registration and email change both verify by link before it is stored. The
notify-email settings are desktop_only (backend/settings_registry.py): there, the
email goes from the user's own Gmail or their own relay, so any address is fine.
"""

from __future__ import annotations

from typing import Any

from backend.app_mode import is_hosted
from backend.settings_service import get_setting


def notification_recipient(
    user_id: int, setting_key: str, repos: dict[str, Any], *, fall_back_to_account: bool
) -> str:
    """The address to notify, or "" when there is none. On the desktop app,
    *setting_key*'s value wins; *fall_back_to_account* says whether an empty
    setting means "the account's email" or "don't send"."""
    account = repos["user_accounts"].get(user_id) or {}
    account_email = str(account.get("email") or "").strip()
    if is_hosted():
        return account_email
    configured = get_setting(setting_key, user_id, repos).strip()
    if configured:
        return configured
    return account_email if fall_back_to_account else ""
