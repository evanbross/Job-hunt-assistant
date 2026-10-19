# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-07-24
"""One-off maintenance script: backfill job.url for existing rows still holding
an unresolved email click-tracking redirect (e.g. Jobgether's AWS SES
awstrack.me links) instead of the canonical job-board URL.

email_parser.py already resolves these at import time via resolve_tracking_url,
but "awstrack.me" was only added to _TRACKING_DOMAIN_FRAGMENTS on 2026-07-24 —
any Jobgether job imported before that date, plus any row where the resolve
call failed transiently at parse time, is still stuck on the raw tracking URL.
Those rows made the browser extension open the tracking-redirect hop instead
of the real page: the tab lands on a different registered domain than it was
told to open, background.js's login-wall heuristic treats that as suspicious,
and the tab is left open forever instead of being captured and closed.

Safe to re-run: only rows whose current URL still matches a known tracking
domain are touched, and a URL resolve_tracking_url can't resolve is left
unchanged (logged, not overwritten).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.email.email_parser import (  # noqa: E402
    is_email_tracking_url,
    resolve_tracking_url,
)
from backend.utils.utils import get_repos  # noqa: E402


def main() -> None:
    with get_repos() as repos:
        jobs = repos["jobs"].get_all_ordered()
        candidates = [
            job for job in jobs if is_email_tracking_url(str(job.get("url") or ""))
        ]
        if not candidates:
            print("No jobs with an unresolved tracking-redirect URL found.")
            return

        print(f"Found {len(candidates)} job(s) with a tracking-redirect URL:\n")
        resolved_count = 0
        for job in candidates:
            original_url = str(job["url"])
            resolved_url = resolve_tracking_url(original_url)
            label = f"{job['id']}: {job.get('company') or '?'} — {job.get('job_title') or '?'}"
            if resolved_url == original_url:
                print(f"{label}\n   could not resolve, left unchanged: {original_url}\n")
                continue
            repos["jobs"].update(job["id"], {"url": resolved_url})
            resolved_count += 1
            print(f"{label}\n   {original_url}\n-> {resolved_url}\n")

        repos["conn"].commit()
        print(f"Resolved and updated {resolved_count} of {len(candidates)} job(s).")


if __name__ == "__main__":
    main()
