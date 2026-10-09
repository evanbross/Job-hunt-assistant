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
"""One-off maintenance script: backfill this operator's application_profile
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

This operator's own prior behavior (US phone/residence, adult eligibility
"Yes") is a one-time, explicit backfill of this one profile row instead of a
schema- or code-level default, so it never leaks onto a different account.
Only ever updates a field that's still NULL -- never overwrites a value the
operator has actually set (including via ApplicationProfilePanel since this
ran).

Usage:
    python scripts/backfill_existing_operator_country_and_age.py          # apply
    python scripts/backfill_existing_operator_country_and_age.py --dry-run
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

_USER_ID = 1  # sole account in this single-tenant deployment (evan@eross.com)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Report planned changes without writing them."
    )
    args = parser.parse_args()

    with get_repos(_USER_ID) as repos:
        profile = repos["application_profile"].get(_USER_ID)
        if profile is None:
            logger.info("No application_profile row for user %d -- nothing to backfill.", _USER_ID)
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
            repos["application_profile"].upsert(_USER_ID, fields)
            repos["conn"].commit()
        logger.info("Done%s.", " (dry run — no writes made)" if args.dry_run else "")


if __name__ == "__main__":
    main()
