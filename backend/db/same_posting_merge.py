# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-15
"""One-time cleanup run by migrate_schema: fold second jobs rows for one posting
into a single row, and drop URLs that never named a posting.

Until 2026-11-15 the email scan inserted a second jobs row -- auto-skipped as a
"near-certain duplicate", or flagged "possible duplicate" -- each time a digest
named a posting already tracked. Those captures are sightings now
(backend/db/repositories/job_sightings.py); this turns the old rows into the same
thing, so the per-user jobs.url unique index (JOBS_URL_UNIQUE_INDEX) can finally
be created.

Per (user, posting), where a posting is posting_identity's (site, job ID) or else
the exact URL:

- **The kept row** is the one holding the user's work: on the sheet, applied,
  with stages, contacts or interactions, not skipped, not an app auto-skip; the
  oldest wins ties (the first sighting).
- **Rows for the same company** become sightings of the kept row. Their status
  history, capture-queue rows and lead suggestions move onto it, other jobs'
  duplicate_of_id pointing at them is repointed, and the row is deleted. Their
  application stages (by now only app-written ones such as req_closed, see the
  next point) move only onto a kept row with none of its own, so a merge can
  never change the kept row's current stage; otherwise they're deleted with it.
- **Rows the user worked on are never merged**: on the sheet, applied, or with
  contacts/interactions. Two of those for one posting is the user's call; they're
  left as they are and listed in the report (and the log) for review; a pair the
  user decides is one job is folded together by merge_worked_on_pair
  (scripts/merge_worked_on_pair.py), keeping both application timelines.
- **Rows for a different company** sharing that exact URL got another job's link
  (a digest email pairing a job with the wrong URL). Their URL is dropped and
  flagged URL_OF_OTHER_JOB_WARNING; the rows themselves are left alone.

Before grouping, company_normalized is re-derived wherever it went stale: a
2026-10-12 direct backfill fixed `company` on LinkedIn rows stuck at "United
States (Remote)" without touching it, so 36 rows still compared as company
"unitedstatesremote" (and broke fuzzy duplicate matching, not just this merge).

Separately, a URL that names no posting (posting_identity.is_non_posting_url)
is dropped and flagged NON_POSTING_URL_WARNING -- what normalize_and_validate now
does at capture -- when it's a sign-in wall, or when more than one of the user's
jobs share it (which also blocks the unique index). A careers or search page
on a single job stays: it can't match anything any more, and it may be the only
link the user has for a job they applied to.

Runs as the owner (exempt from row-level security) across every account.
Idempotent: once merged, a database has nothing left for it to do."""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from psycopg2 import sql

from backend.scoring.company_matching import normalize_company_name
from backend.scoring.duplicate_matching import is_auto_duplicate_skip
from backend.scoring.import_warning_codes import (
    NON_POSTING_URL_WARNING,
    URL_OF_OTHER_JOB_WARNING,
)
from backend.scoring.posting_identity import (
    is_non_posting_url,
    is_sign_in_url,
    posting_identity,
)

from .repositories.company_aliases import resolve_company_alias
from .repositories.job_sightings import SightingChannel

logger = logging.getLogger(__name__)

# Every (table, column) holding a jobs.id that a plain UPDATE moves onto the kept
# row. Not every one has a foreign key -- application_stages and
# browser_capture_queue don't on older databases -- so this can't be read from
# the catalog's constraints; tests/test_same_posting_merge.py checks this plus
# JOB_ID_REFERENCES_MERGED_SEPARATELY against every job_id column the schema has.
JOB_ID_REFERENCES: tuple[tuple[str, str], ...] = (
    ("application_stages", "job_id"),
    ("job_status_history", "job_id"),
    ("browser_capture_queue", "job_id"),
    ("interactions", "job_id"),
    ("lead_suggestions", "job_id"),
)
# Moved by _merge_into's own statements: unique constraints a plain UPDATE could
# violate (one link per contact and one primary per job; one sighting per job,
# source and day), or a self-reference.
JOB_ID_REFERENCES_MERGED_SEPARATELY: frozenset[tuple[str, str]] = frozenset(
    {("job_contacts", "job_id"), ("job_sightings", "job_id"), ("jobs", "duplicate_of_id")}
)


@dataclass
class MergeReport:
    company_normalized_repaired: int = 0
    merged_rows: int = 0
    other_job_urls_dropped: int = 0
    non_posting_urls_dropped: int = 0
    # (kept job id, other job id): same posting, both worked on -- left for review.
    left_for_review: list[tuple[int, int]] = field(default_factory=list)


def merge_same_posting_job_rows(conn: Any) -> MergeReport:
    """Run the cleanup described in the module docstring and commit it."""
    cur = conn.cursor()
    report = MergeReport()
    report.company_normalized_repaired = _repair_stale_company_normalized(cur)
    report.non_posting_urls_dropped = _drop_non_posting_urls(cur)
    for rows in _same_posting_groups(cur):
        kept = max(rows, key=_keep_rank)
        for row in rows:
            if row["id"] == kept["id"]:
                continue
            if not _same_company(row, kept):
                if (row["url"] or "").strip().lower() == (kept["url"] or "").strip().lower():
                    _drop_url(cur, row, URL_OF_OTHER_JOB_WARNING)
                    report.other_job_urls_dropped += 1
            elif _worked_on(row):
                report.left_for_review.append((kept["id"], row["id"]))
            else:
                _merge_into(cur, loser=row, kept=kept)
                report.merged_rows += 1
    conn.commit()
    if (
        report.company_normalized_repaired
        or report.merged_rows
        or report.other_job_urls_dropped
        or report.non_posting_urls_dropped
    ):
        logger.info(
            "Same-posting cleanup: %d stale company_normalized value(s) repaired, %d "
            "duplicate row(s) folded into sightings, %d URL(s) of another job dropped, "
            "%d sign-in/search-page URL(s) dropped.",
            report.company_normalized_repaired,
            report.merged_rows,
            report.other_job_urls_dropped,
            report.non_posting_urls_dropped,
        )
    if report.left_for_review:
        logger.warning(
            "Same-posting cleanup left %d pair(s) of worked-on jobs for the same posting "
            "unmerged, for review (kept id, other id): %s",
            len(report.left_for_review),
            report.left_for_review,
        )
    return report


# ai: claude-opus-5-5 | 2026-11-17
def merge_worked_on_pair(conn: Any, *, kept_id: int, other_id: int) -> None:
    """Fold *other_id* into *kept_id* when the user decides two worked-on rows for one
    posting (MergeReport.left_for_review) are one job.

    Unlike the automatic merge, both rows hold the user's own application history, so
    *other_id*'s application stages move onto *kept_id* -- one combined timeline,
    whose latest stage becomes the job's current one -- instead of being deleted.
    Everything else is _merge_into's: a sighting for the folded row, its status
    history, contacts, queue rows and sightings move over, and the row is deleted.

    Raises ValueError, changing nothing, unless both rows exist, belong to one
    account, name the same posting and the same company. Does not commit: the
    caller commits, or rolls back for a dry run."""
    cur = conn.cursor()
    cur.execute(_JOB_ROW_SELECT + " WHERE j.id = ANY(%s)", ([kept_id, other_id],))
    rows = {row["id"]: dict(row) for row in cur.fetchall()}
    if kept_id == other_id or set(rows) != {kept_id, other_id}:
        raise ValueError(f"Jobs {kept_id} and {other_id} must be two existing, distinct jobs")
    kept, other = rows[kept_id], rows[other_id]
    if kept["user_id"] != other["user_id"]:
        raise ValueError(f"Jobs {kept_id} and {other_id} belong to different accounts")
    if not kept["url"] or not other["url"] or _posting_key(kept["url"]) != _posting_key(other["url"]):
        raise ValueError(f"Jobs {kept_id} and {other_id} are not the same posting")
    if not _same_company(other, kept):
        raise ValueError(f"Jobs {kept_id} and {other_id} are for different companies")
    cur.execute("UPDATE application_stages SET job_id = %s WHERE job_id = %s", (kept_id, other_id))
    kept["n_stages"] += other["n_stages"]
    other["n_stages"] = 0
    _merge_into(cur, loser=other, kept=kept)
    logger.info("Merged worked-on job %d into job %d (same posting).", other_id, kept_id)


def _posting_key(url: str) -> Any:
    stripped = url.strip()
    return posting_identity(stripped) or stripped.lower()


def _repair_stale_company_normalized(cur: Any) -> int:
    """Re-derive company_normalized the way the jobs repository does on every
    write (normalize_company_name, then the user's company aliases) wherever it
    no longer matches. Only rows that differ from the plain normalization need
    the alias lookup, so this stays one query plus a handful."""
    cur.execute(
        "SELECT id, user_id, company, company_normalized FROM jobs"
        " WHERE company IS NOT NULL AND company <> ''"
    )
    candidates = [
        row
        for row in cur.fetchall()
        if (row["company_normalized"] or "") != normalize_company_name(row["company"])
    ]

    def execute(query: str, params: tuple) -> Any:
        cur.execute(query, params)
        return cur

    repaired = 0
    for row in candidates:
        canonical = resolve_company_alias(
            execute, row["user_id"], normalize_company_name(row["company"])
        )
        if canonical != (row["company_normalized"] or ""):
            cur.execute(
                "UPDATE jobs SET company_normalized = %s WHERE id = %s", (canonical, row["id"])
            )
            repaired += 1
    return repaired


def _drop_non_posting_urls(cur: Any) -> int:
    cur.execute(
        "SELECT id, user_id, url, import_warnings FROM jobs WHERE url IS NOT NULL AND url <> ''"
    )
    rows = [row for row in cur.fetchall() if is_non_posting_url(row["url"])]
    uses: Counter[tuple[int, str]] = Counter(
        (row["user_id"], row["url"].strip().lower()) for row in rows
    )
    dropped = 0
    for row in rows:
        if is_sign_in_url(row["url"]) or uses[(row["user_id"], row["url"].strip().lower())] > 1:
            _drop_url(cur, row, NON_POSTING_URL_WARNING)
            dropped += 1
    return dropped


# Every jobs column _merge_into and the keep/worked-on rules read, plus the counts
# they rank by. One copy, shared by the grouping pass and merge_worked_on_pair.
_JOB_ROW_SELECT = """
    SELECT j.id, j.user_id, j.url, j.company, j.company_normalized, j.status,
           j.skip_reason, j.skip_feedback, j.on_sheet, j.source,
           j.capture_label, j.sender_email, j.date_found, j.import_warnings,
           j.duplicate_of_id,
           (j.job_description IS NOT NULL AND j.job_description <> '') AS has_description,
           (SELECT COUNT(*) FROM application_stages s WHERE s.job_id = j.id) AS n_stages,
           (SELECT COUNT(*) FROM job_contacts c WHERE c.job_id = j.id)
             + (SELECT COUNT(*) FROM interactions i WHERE i.job_id = j.id) AS n_people
    FROM jobs j
"""


def _same_posting_groups(cur: Any) -> list[list[dict[str, Any]]]:
    cur.execute(_JOB_ROW_SELECT + " WHERE j.url IS NOT NULL AND j.url <> ''")
    groups: dict[tuple[int, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in cur.fetchall():
        groups[(row["user_id"], _posting_key(row["url"]))].append(dict(row))
    return [rows for rows in groups.values() if len(rows) > 1]


def _keep_rank(row: dict[str, Any]) -> tuple:
    """Higher sorts first: which row of a group holds the most of the user's work."""
    return (
        bool(row["on_sheet"]),
        row["status"] == "applied",
        row["n_stages"] > 0,
        row["n_people"] > 0,
        row["status"] != "skipped",
        not is_auto_duplicate_skip(row),
        -row["id"],
    )


def _worked_on(row: dict[str, Any]) -> bool:
    return bool(row["on_sheet"]) or row["status"] == "applied" or row["n_people"] > 0


def _same_company(row: dict[str, Any], kept: dict[str, Any]) -> bool:
    a = (row.get("company_normalized") or "").strip()
    b = (kept.get("company_normalized") or "").strip()
    return not a or not b or a == b


def _merge_into(cur: Any, *, loser: dict[str, Any], kept: dict[str, Any]) -> None:
    loser_id, kept_id = loser["id"], kept["id"]
    seen_on = str(loser["date_found"] or "")[:10]
    cur.execute(
        """
        INSERT INTO job_sightings
            (user_id, job_id, seen_at, source, channel, capture_label, url, sender_email)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (
            loser["user_id"],
            kept_id,
            f"{seen_on}T00:00:00+00:00" if seen_on else "1970-01-01T00:00:00+00:00",
            (loser["source"] or "").strip() or "Unknown",
            str(SightingChannel.MERGED_DUPLICATE),
            loser["capture_label"],
            loser["url"],
            loser["sender_email"],
        ),
    )
    # A richer copy may have arrived later; don't lose a description the kept row lacks.
    if not kept["has_description"] and loser["has_description"]:
        cur.execute(
            "UPDATE jobs SET job_description = (SELECT job_description FROM jobs WHERE id = %s)"
            " WHERE id = %s",
            (loser_id, kept_id),
        )
        kept["has_description"] = True
    # Stages decide a job's current status (latest_application_stage), so only a
    # kept row with no history of its own takes on the loser's.
    if kept["n_stages"] == 0 and loser["n_stages"] > 0:
        kept["n_stages"] = loser["n_stages"]
    else:
        cur.execute("DELETE FROM application_stages WHERE job_id = %s", (loser_id,))
    for table, column in JOB_ID_REFERENCES:
        cur.execute(
            sql.SQL("UPDATE {} SET {} = %s WHERE {} = %s").format(
                sql.Identifier(table), sql.Identifier(column), sql.Identifier(column)
            ),
            (kept_id, loser_id),
        )
    # job_contacts: UNIQUE (job_id, contact_id) and at most one primary per job.
    cur.execute(
        "DELETE FROM job_contacts WHERE job_id = %s AND contact_id IN"
        " (SELECT contact_id FROM job_contacts WHERE job_id = %s)",
        (loser_id, kept_id),
    )
    cur.execute(
        "UPDATE job_contacts SET is_primary = FALSE WHERE job_id = %s"
        " AND EXISTS (SELECT 1 FROM job_contacts WHERE job_id = %s AND is_primary)",
        (loser_id, kept_id),
    )
    cur.execute("UPDATE job_contacts SET job_id = %s WHERE job_id = %s", (kept_id, loser_id))
    # Sightings recorded on this row before the merge ran (a capture matched it
    # rather than the kept row); any it can't move are same source, same day.
    cur.execute(
        """
        INSERT INTO job_sightings
            (user_id, job_id, seen_at, source, channel, capture_label, url, sender_email)
        SELECT user_id, %s, seen_at, source, channel, capture_label, url, sender_email
        FROM job_sightings WHERE job_id = %s
        ON CONFLICT DO NOTHING
        """,
        (kept_id, loser_id),
    )
    cur.execute(
        "UPDATE jobs SET duplicate_of_id = %s WHERE duplicate_of_id = %s AND id <> %s",
        (kept_id, loser_id, kept_id),
    )
    cur.execute(
        "UPDATE jobs SET duplicate_of_id = NULL WHERE id = %s AND duplicate_of_id IN (%s, %s)",
        (kept_id, loser_id, kept_id),
    )
    cur.execute("DELETE FROM jobs WHERE id = %s", (loser_id,))


def _drop_url(cur: Any, row: dict[str, Any], warning: str) -> None:
    try:
        warnings = json.loads(row.get("import_warnings") or "[]")
    except (TypeError, ValueError):
        warnings = []
    if not isinstance(warnings, list):
        warnings = []
    if warning not in warnings:
        warnings.append(warning)
    cur.execute(
        "UPDATE jobs SET url = NULL, import_warnings = %s WHERE id = %s",
        (json.dumps(warnings), row["id"]),
    )
