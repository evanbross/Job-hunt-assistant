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

# ai: claude-sonnet-5 | 2026-10-09
# ai: claude-sonnet-5 | 2026-10-18 — generalized from a hardcoded _USER_ID=1
"""One-off maintenance script: backfill one operator's application_profile
row with the country/is_18_or_older values autofill.js used to hardcode.

Root cause (fixed alongside this script, see browser-extension/autofill.js's
profileValueForCategory/boolValueForCategory): the 2026-10-08 review's first
fix added real country/is_18_or_older profile fields but kept autofill.js
falling back to the old hardcoded 'United States'/true whenever a field was
unset -- which kept this operator's behavior unchanged, but silently
reintroduced the same cross-account bug the fields were added to close (a
second account's unset field would also get this operator's answers). The
2026-10-09 follow-up review flagged that: autofill.js now treats an unset
field as unresolved, same as work_authorized/veteran_status/etc. already do.

A target operator's own prior behavior (US phone/residence, adult
eligibility "Yes") is a one-time, explicit backfill of that one profile row
instead of a schema- or code-level default, so it never leaks onto a
different account. Only ever updates a field that's still NULL -- never
overwrites a value the operator has actually set (including via
ApplicationProfilePanel since this ran).

The account is never assumed: the app supports multiple users, so there is
no portable "the" operator across a restored, test, or newly seeded database
(2026-10-18 full review, Medium finding -- this was flagged open since the
2026-10-09 review as still hardcoding user_id=1).

Usage:
    python scripts/backfill_existing_operator_country_and_age.py --user-id 1
    python scripts/backfill_existing_operator_country_and_age.py --email evan@eross.com
    python scripts/backfill_existing_operator_country_and_age.py --user-id 1 --dry-run
    python scripts/backfill_existing_operator_country_and_age.py --user-id 1 --yes
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.utils.utils import get_repos  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
)
logger = logging.getLogger(__name__)


def _resolve_user_id(repos: dict, args: argparse.Namespace) -> int:
    if args.user_id is not None:
        account = repos["user_accounts"].get(args.user_id)
        if account is None:
            raise SystemExit(f"No account with id {args.user_id}.")
        return account["id"]
    account = repos["user_accounts"].get_by_email(args.email)
    if account is None:
        raise SystemExit(f"No account with email {args.email!r}.")
    return account["id"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--user-id", type=int, help="Account id to backfill.")
    target.add_argument("--email", help="Account email to backfill.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report planned changes without writing them."
    )
    parser.add_argument(
        "--yes", action="store_true", help="Skip the confirmation prompt."
    )
    args = parser.parse_args()

    # user_accounts isn't RLS-protected (it's the identity table itself, looked up
    # the same way routes/session_auth.py does with no session yet -- see
    # backend/db/row_level_security.py's _EXCLUDED_FROM_RLS comment for the
    # equivalent extension_tokens case), so this resolves/validates the target
    # account before the RLS-protected application_profile lookup below needs a
    # real identity to see anything at all.
    with get_repos() as repos:
        user_id = _resolve_user_id(repos, args)
        account = repos["user_accounts"].get(user_id)

    logger.info("Selected account: id=%d email=%s", user_id, account["email"])
    if not args.dry_run and not args.yes:
        confirmed = input(f"Backfill application_profile for {account['email']!r}? [y/N] ")
        if confirmed.strip().lower() not in ("y", "yes"):
            logger.info("Aborted -- no changes made.")
            return

    with get_repos(user_id) as repos:
        profile = repos["application_profile"].get(user_id)
        if profile is None:
            logger.info("No application_profile row for user %d -- nothing to backfill.", user_id)
            return

        fields: dict[str, object] = {}
        if profile.get("country") is None:
            fields["country"] = "United States"
        if profile.get("is_18_or_older") is None:
            fields["is_18_or_older"] = True

        if not fields:
            logger.info("country and is_18_or_older are both already set -- nothing to do.")
            return

        logger.info("Backfilling: %s", fields)
        if not args.dry_run:
            repos["application_profile"].upsert(user_id, fields)
            repos["conn"].commit()
        logger.info("Done%s.", " (dry run — no writes made)" if args.dry_run else "")


if __name__ == "__main__":
    main()
