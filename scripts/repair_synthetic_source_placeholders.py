# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-10-09
"""One-off maintenance script: repair jobs whose "source" column was stuffed
with the synthetic sender placeholder ManualJobIngestService.ingest() hands
the AI parser for non-email captures (f"{email_id_prefix}@local", e.g.
"browser_ext@local").

Root cause (fixed alongside this script, see backend/scanning/manual_job_ingest_service.py
and backend/utils/company_urls.platform_name_from_url): the parser's prompted
fallback for an unidentifiable job board is "job board name or sender domain".
When it couldn't find a real board name, it echoed that placeholder's "local"
domain, or the whole address, back as "source" -- and ingest() let that
overwrite the real "Browser Extension"/"Manual Paste" label it would
otherwise have used. 921 rows were affected (sources "local", "browser_ext@local",
"tampermonkey@local", "manual@local" -- "tampermonkey" is the old pre-rename
label for the browser extension, kept here for backward compat).

Recomputes each affected row's source the same way ingest() now does: a
Browser Extension/Manual Paste prefix (from the dedup_key's capture-type
prefix) plus a site name derived from the row's own url via
ManualJobIngestService.source_name_for_url() -- now using
platform_name_from_url()'s generic domain fallback, so sites outside the
small hardcoded ATS list (e.g. jobgether.com) get a real name too instead of
falling through to "Manual URL".

Usage:
    python scripts/repair_synthetic_source_placeholders.py          # apply fixes
    python scripts/repair_synthetic_source_placeholders.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db.job_columns import JobColumns  # noqa: E402
from backend.logging_setup import configure_logging  # noqa: E402
from backend.scanning.manual_job_ingest_service import (
    ManualJobIngestService,  # noqa: E402
)
from backend.utils.utils import get_repos  # noqa: E402

configure_logging()
logger = logging.getLogger(__name__)

_USER_ID = 1  # sole account in this single-tenant deployment (evan@eross.com)

_SYNTHETIC_SOURCES = {"local", "browser_ext@local", "tampermonkey@local", "manual@local"}

_BROWSER_EXTENSION_DEDUP_PREFIXES = ("browser_ext:", "tampermonkey:")
_MANUAL_PASTE_DEDUP_PREFIX = "manual:"


def _source_prefix_for_dedup_key(dedup_key: str) -> str | None:
    if dedup_key.startswith(_BROWSER_EXTENSION_DEDUP_PREFIXES):
        return "Browser Extension"
    if dedup_key.startswith(_MANUAL_PASTE_DEDUP_PREFIX):
        return "Manual Paste"
    return None


def _candidates(repos: dict) -> list[dict]:
    jobs = repos["jobs"].get_all_ordered(_USER_ID)
    return [job for job in jobs if str(job.get("source") or "").strip().lower() in _SYNTHETIC_SOURCES]


def _recomputed_source(job: dict) -> str | None:
    dedup_key = str(job.get("dedup_key") or "")
    prefix = _source_prefix_for_dedup_key(dedup_key)
    if prefix is None:
        logger.warning(
            "  job %s: dedup_key %r doesn't match a known capture-type prefix, skipping",
            job["id"],
            dedup_key,
        )
        return None

    url = str(job.get("url") or "").strip()
    if not url:
        return prefix

    platform = ManualJobIngestService.source_name_for_url(url)
    if platform == "Manual URL":
        return prefix
    return f"{prefix} - {platform}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Report planned changes without writing them."
    )
    args = parser.parse_args()

    with get_repos(_USER_ID) as repos:
        candidates = _candidates(repos)
        logger.info("Found %d job(s) with a synthetic placeholder source", len(candidates))

        repaired = 0
        skipped = 0
        for i, job in enumerate(candidates, start=1):
            job_id = int(job["id"])
            old_source = job.get("source")
            new_source = _recomputed_source(job)
            if new_source is None:
                skipped += 1
                continue

            logger.info(
                "[%d/%d] job %d: %r -> %r", i, len(candidates), job_id, old_source, new_source
            )
            if not args.dry_run:
                repos["jobs"].update(_USER_ID, job_id, {JobColumns.SOURCE: new_source})
            repaired += 1

        if not args.dry_run and repaired:
            repos["conn"].commit()

        logger.info(
            "Done. repaired=%d skipped=%d%s",
            repaired,
            skipped,
            " (dry run — no writes made)" if args.dry_run else "",
        )


if __name__ == "__main__":
    main()
