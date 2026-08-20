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

# ai: claude-sonnet-5 | 2026-08-20
"""One-time setup helper: prints the .env lines for the app login gate.

There is no registration flow — this app has exactly one owner. Run this
script once, paste the two printed lines into .env, and restart the app.
Re-run any time to change the password.
"""

from __future__ import annotations

import getpass

from werkzeug.security import generate_password_hash


# ai: claude-sonnet-5 | 2026-08-20
def _read_password(prompt: str) -> str:
    """getpass hides input completely — no dots, no cursor movement, nothing
    printed as you type. That's expected, not a hang. Some terminals (seen
    on Git Bash / certain integrated terminals on Windows) can't suppress
    echo at all, in which case getpass itself already falls back to visible
    input; if it still can't read the terminal, fall back here too rather
    than raising an unhandled traceback."""
    print(f"{prompt}(input is hidden — nothing will appear as you type)")
    try:
        return getpass.getpass("")
    except (OSError, EOFError):
        print("(hidden input isn't supported in this terminal — typing will be visible)")
        return input()


def main() -> None:
    email = input("Login email: ").strip()
    if not email:
        print("Email cannot be empty.")
        return

    password = _read_password("Login password: ")
    confirm = _read_password("Confirm password: ")
    if not password:
        print("Password cannot be empty.")
        return
    if password != confirm:
        print("Passwords did not match.")
        return

    password_hash = generate_password_hash(password)
    print("\nAdd these lines to .env:\n")
    print(f"APP_LOGIN_EMAIL={email}")
    print(f"APP_LOGIN_PASSWORD_HASH={password_hash}")


if __name__ == "__main__":
    main()
