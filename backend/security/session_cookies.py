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

"""Session cookie settings, derived from how the app is actually served.

A Secure cookie is only ever sent over HTTPS, and a browser discards one set
over plain HTTP from a non-loopback address. So the flag has to follow the
deployment: on for an https:// public URL, off only where the app is reached
over plain HTTP (local use, a Tailscale IP). "auto" derives it from the app's
base URL; "true"/"false" force it."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

SAMESITE = "Lax"
SECURE_COOKIE_NAME = "__Host-session"
PLAIN_COOKIE_NAME = "session"
_VALID_MODES = frozenset({"auto", "true", "false"})


# ai: claude-sonnet-5 | 2026-09-26
def normalize_secure_mode(raw: str) -> str:
    mode = raw.strip().lower() or "auto"
    if mode not in _VALID_MODES:
        raise ValueError(f"SESSION_COOKIE_SECURE must be auto, true, or false, not {raw!r}")
    return mode


# ai: claude-sonnet-5 | 2026-09-26
def cookies_should_be_secure(app_base_url: str, mode: str) -> bool:
    if mode == "true":
        return True
    if mode == "false":
        return False
    return urlparse(app_base_url.strip()).scheme.lower() == "https"


# ai: claude-sonnet-5 | 2026-09-26
def session_cookie_config(app_base_url: str, mode: str = "auto") -> dict[str, Any]:
    """Flask config entries for the session cookie.

    HttpOnly always (page scripts can never read it) and SameSite=Lax always
    (blocks cross-site sends). When Secure, the cookie also gets the __Host-
    prefix, which makes the browser refuse to accept it unless it is Secure,
    path=/, and has no Domain attribute, so a sibling subdomain can't plant or
    overwrite it."""
    secure = cookies_should_be_secure(app_base_url, mode)
    return {
        "SESSION_COOKIE_SECURE": secure,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": SAMESITE,
        "SESSION_COOKIE_NAME": SECURE_COOKIE_NAME if secure else PLAIN_COOKIE_NAME,
        "SESSION_COOKIE_PATH": "/",
    }
