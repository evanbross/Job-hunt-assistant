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

# ai: claude-fable-5 | 2026-07-17
"""One-off maintenance script, executed 2026-07-17 (kept for the record).

Deleted the three WTTJ rows whose stored URL belonged to another company's
job — the mis-attributed-URL failure mode of the pre-2026-06-22 WTTJ email
parser, surfaced by the repair flow's page-verification pass (see
routes/jobs.py `_run_wttj_repair`):

  735  Corelight 'Principal Software Engineer'           -> page is Docker's job
  2511 Airbnb    'Senior Software Engineer (Assurance)'  -> page is Omada Health's job
  2594 Zipline   'Staff Software Engineer (AI Platform Team)' -> page is Coinbase's job

The original source emails are gone, so the correct URLs were unrecoverable;
all three rows were still in triage with no application history. Child rows
are removed by the schema's ON DELETE CASCADE; duplicate_of_id pointers to
the deleted ids are cleared. Safe to re-run: already-deleted ids are skipped.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.utils import get_repos  # noqa: E402

DOOMED: tuple[int, ...] = (735, 2511, 2594)


def main() -> None:
    with get_repos() as repos:
        for job_id in DOOMED:
            job = repos["jobs"].get_by_id(job_id)
            if not job:
                print(f"{job_id}: already gone")
                continue
            print(
                f"deleting {job_id}: {job.get('company')} — {job.get('job_title')} "
                f"[{job.get('status')}]"
            )
            repos["jobs"].delete(job_id)

        cur = repos["conn"].cursor()
        cur.execute(
            "UPDATE jobs SET duplicate_of_id = NULL "
            "WHERE duplicate_of_id IN (%s, %s, %s)",
            DOOMED,
        )
        repos["conn"].commit()

        for job_id in DOOMED:
            assert repos["jobs"].get_by_id(job_id) is None, f"{job_id} still present!"
        print("verified: all rows deleted")


if __name__ == "__main__":
    main()
