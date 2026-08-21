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

# ai: claude-sonnet-5 | 2026-08-21
"""Recovery script: run this if you're locked out of an account (forgot its
password) and can't reach a "change password" option because you can't log
in at all.

Deletes the app_account row for the given email. There is no password
prompt here — recovery doesn't need one: whoever can run this script already
has full DB/filesystem access to this machine (same trust level as the
Google OAuth token already sitting in credentials/). After running this,
re-register with the same email via the "Create account" form.

Registration is open (multi-tenancy) — this targets one account by email,
not "the only account" (there may be several).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.utils.utils import get_repos  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: uv run python scripts/reset_login_account.py <email>")
        return
    email = sys.argv[1].strip().casefold()

    with get_repos() as repos:
        existing = repos["app_account"].get_by_email(email)
        if existing is None:
            print(f"No account found for {email!r} — nothing to reset.")
            return
        print(f"Deleting account for {existing['email']}...")
        repos["app_account"].delete(existing["id"])
        repos["conn"].commit()
    print("Done. Re-register with this email via the Create Account form.")


if __name__ == "__main__":
    main()
