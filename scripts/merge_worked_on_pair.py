# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================
# ai: claude-opus-5-5 | 2026-11-17
"""Fold one worked-on job into another for the same posting, keeping both timelines.

The same-posting merge (backend/db/same_posting_merge.py) never merges two rows
the user applied to; it lists them for review. When the user decides a pair is one
job (e.g. an accidental second application), this folds --fold into --keep with
merge_worked_on_pair: both rows' application stages end up on --keep.

--drop-stage / --drop-history remove specific application_stages /
job_status_history rows first, by id, for entries that would be wrong once the two
timelines are combined -- e.g. an auto "ghosted" stage on the folded row dated
after a rejection recorded on the kept one, which would otherwise become the
job's current stage. Each id must belong to --keep or --fold.

Usage (PYTHONIOENCODING=utf-8: some company names carry emoji):
    PYTHONIOENCODING=utf-8 uv run python scripts/merge_worked_on_pair.py \\
        --keep 1528 --fold 1632 --drop-stage 930 --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from psycopg2 import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.db.same_posting_merge import merge_worked_on_pair  # noqa: E402
from backend.logging_setup import configure_logging  # noqa: E402
from backend.utils.utils import get_repos  # noqa: E402

configure_logging()
logger = logging.getLogger(__name__)


class PairMerge:
    """One --keep/--fold merge, with its by-id clean-up, against one connection."""

    def __init__(self, conn: Any, keep: int, fold: int) -> None:
        self.conn = conn
        self.keep = keep
        self.fold = fold

    def drop_rows(self, table: str, ids: list[int]) -> None:
        if not ids:
            return
        cur = self.conn.cursor()
        cur.execute(
            sql.SQL("DELETE FROM {} WHERE id = ANY(%s) AND job_id = ANY(%s) RETURNING id").format(
                sql.Identifier(table)
            ),
            (ids, [self.keep, self.fold]),
        )
        deleted = {row["id"] for row in cur.fetchall()}
        missing = set(ids) - deleted
        if missing:
            raise ValueError(f"{table} id(s) {sorted(missing)} not found on job {self.keep} or {self.fold}")
        logger.info("Deleted %s id(s) %s", table, sorted(deleted))

    def timeline(self) -> list[str]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id, status, date, notes FROM application_stages WHERE job_id = %s"
            " ORDER BY date, created_at, id",
            (self.keep,),
        )
        return [f"  stage {r['id']}: {r['date']} {r['status']} {r['notes'] or ''}".rstrip() for r in cur.fetchall()]

    def run(self, drop_stages: list[int], drop_history: list[int]) -> None:
        self.drop_rows("application_stages", drop_stages)
        self.drop_rows("job_status_history", drop_history)
        merge_worked_on_pair(self.conn, kept_id=self.keep, other_id=self.fold)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", type=int, required=True, help="job id that stays")
    parser.add_argument("--fold", type=int, required=True, help="job id folded into --keep and deleted")
    parser.add_argument("--drop-stage", type=int, action="append", default=[], help="application_stages id to delete first")
    parser.add_argument("--drop-history", type=int, action="append", default=[], help="job_status_history id to delete first")
    parser.add_argument("--user-id", type=int, default=1, help="account owning both jobs (default 1)")
    parser.add_argument("--dry-run", action="store_true", help="show the result, then roll back")
    args = parser.parse_args()

    with get_repos(user_id=args.user_id) as repos:
        conn = repos["conn"]
        merge = PairMerge(conn, args.keep, args.fold)
        try:
            merge.run(args.drop_stage, args.drop_history)
        except ValueError as exc:
            conn.rollback()
            logger.error("Not merged: %s", exc)
            return 1
        print(f"Job {args.keep} timeline after merging job {args.fold}:")
        print("\n".join(merge.timeline()))
        if args.dry_run:
            conn.rollback()
            print("Dry run: rolled back.")
        else:
            conn.commit()
            print("Committed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
