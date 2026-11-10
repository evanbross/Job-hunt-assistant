# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-10
"""Every string in a JSON request body reaches route code with surrounding
whitespace already removed.

Doing this once, at the request object, means every page, dialog and control in
the app is covered, including routes added later, instead of each route having to
remember. It came out of the hosted rehearsal: an SMTP host pasted as
" smtp.forwardemail.net" was saved as typed, and every email then failed as an
unknown host.

Passwords are the one exception. A password may really begin or end with a space,
and trimming it would make a password typed that way fail to log in. A field is
treated as a password when its JSON key contains "password", which covers every
password field in the API (password, current_password, new_password,
smtp_password).
"""

from __future__ import annotations

from typing import Any

from flask import Request

_UNTRIMMED_KEY_MARKER = "password"


def trim_user_input(value: Any, key: str | None = None) -> Any:
    """Return `value` with every string stripped, recursing into objects and arrays.

    `key` is the JSON key the value sits under; a string under a password key is
    returned unchanged. Array items inherit their array's key. Keys themselves
    are never changed.
    """
    if isinstance(value, str):
        if key is not None and _UNTRIMMED_KEY_MARKER in key.lower():
            return value
        return value.strip()
    if isinstance(value, dict):
        return {item_key: trim_user_input(item, item_key) for item_key, item in value.items()}
    if isinstance(value, list):
        return [trim_user_input(item, key) for item in value]
    return value


class TrimmedInputRequest(Request):
    """Flask's request with get_json() (and so `request.json`) trimmed by
    trim_user_input(). Installed as the app's request_class in web/factory.py."""

    def get_json(self, force: bool = False, silent: bool = False, cache: bool = True) -> Any | None:
        return trim_user_input(super().get_json(force=force, silent=silent, cache=cache))
