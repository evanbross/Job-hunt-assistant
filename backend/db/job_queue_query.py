# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-10-23
"""What the Job Queue page can ask for: its views, sort orders, search fields
and filters, and the SQL each one means.

The page used to download every queue and triage row (~2,400 rows / 2.3MB
on a real account) and filter, sort and render all of them in the browser,
a ~5s main-thread freeze plus a ~700ms response that held up every other
request behind it. It now asks PostgresJobRepository for one page at a time
(list_queue_page / count_queue / list_queue_companies), and this module is
the single definition of what those requests mean. The frontend reads the
sort and search-field choices from GET /api/jobs/queue/options instead of
keeping its own copy of the keys.

A leaf module (no imports beyond other leaf modules) so backend/utils/utils.py
can derive ACTIVE_JOB_STATUSES from it without an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.db.keyset_pagination import SortKey
from backend.db.keyset_pagination import order_by_sql as keyset_order_by_sql
from backend.scoring.company_matching import normalize_company_name_sql

QUEUE_STATUSES: tuple[str, ...] = ("pending_review",)
TRIAGE_STATUSES: tuple[str, ...] = ("triage", "possible_duplicate")


class JobQueueView(StrEnum):
    QUEUE = "queue"
    TRIAGE = "triage"
    BOTH = "both"
    COMPANY = "company"
    UNREACHABLE = "unreachable"


# None: the Unreachable view isn't a status slice -- it's every job the
# closed-req scan last failed to reach, whatever its status (see
# routes/applications.py's _persist_unreachable_flag).
VIEW_STATUSES: dict[JobQueueView, tuple[str, ...] | None] = {
    JobQueueView.QUEUE: QUEUE_STATUSES,
    JobQueueView.TRIAGE: TRIAGE_STATUSES,
    JobQueueView.BOTH: QUEUE_STATUSES + TRIAGE_STATUSES,
    JobQueueView.COMPANY: QUEUE_STATUSES + TRIAGE_STATUSES,
    JobQueueView.UNREACHABLE: None,
}

# The queue and triage lists both hide a job until its snooze date arrives.
NOT_SNOOZED_SQL = (
    "(snoozed_until IS NULL OR snoozed_until::date <= (NOW() AT TIME ZONE 'UTC')::date)"
)


def view_where_sql(view: JobQueueView) -> str:
    """The rows *view* shows before any search or filter. No parameters:
    every value comes from this module's own constants."""
    statuses = VIEW_STATUSES[view]
    if statuses is None:
        return "unreachable_since IS NOT NULL"
    quoted = ", ".join(f"'{status}'" for status in statuses)
    return f"status IN ({quoted}) AND {NOT_SNOOZED_SQL}"


# ai: claude-opus-5-5 | 2026-11-03 — sorts are SortKey tuples, so ORDER BY and the
# keyset-pagination cursor predicate (backend/db/keyset_pagination.py) come from
# one definition instead of an ORDER BY string the cursor would have to re-parse.
@dataclass(frozen=True)
class SortOption:
    key: str
    label: str
    keys: tuple[SortKey, ...]


_DATE_DESC = (SortKey("date_found", descending=True, nulls_last=True), SortKey("id", descending=True))
_DATE_ASC = (SortKey("date_found", nulls_last=False), SortKey("id"))
# A job with no certainty score sorts as 50, except a possible duplicate
# with none, which sorts below every scored job (-1).
_CONFIDENCE = (
    "COALESCE(certainty_score, CASE WHEN status = 'possible_duplicate' THEN -1 ELSE 50 END)"
)
_FIT_DESC = SortKey("fit_score", descending=True, nulls_last=True)

# Every order ends in id, so a page boundary never falls between two rows
# the database could return in either order -- without that, rows sharing a
# date_found or fit_score could repeat or go missing between pages.
JOB_QUEUE_SORTS: tuple[SortOption, ...] = (
    SortOption("date_desc", "Newest first", _DATE_DESC),
    SortOption("date_asc", "Oldest first", _DATE_ASC),
    SortOption("fit_desc_date_desc", "Highest fit, newest first", (_FIT_DESC, *_DATE_DESC)),
    SortOption("fit_desc_date_asc", "Highest fit, oldest first", (_FIT_DESC, *_DATE_ASC)),
    SortOption("fit_asc", "Lowest fit", (SortKey("fit_score", nulls_last=True), *_DATE_DESC)),
    SortOption("confidence_desc", "Highest confidence", (SortKey(_CONFIDENCE, descending=True), *_DATE_DESC)),
    SortOption("confidence_asc", "Lowest confidence", (SortKey(_CONFIDENCE), *_DATE_DESC)),
    SortOption("company_asc", "Company A–Z", (SortKey("lower(COALESCE(company, ''))"), *_DATE_DESC)),
)
SORTS_BY_KEY: dict[str, SortOption] = {sort.key: sort for sort in JOB_QUEUE_SORTS}
DEFAULT_SORT = "date_desc"
# The Unreachable view keeps its own fixed order (longest-unreachable first),
# as it always has; the sort dropdown doesn't apply to it.
UNREACHABLE_SORT_KEYS: tuple[SortKey, ...] = (SortKey("unreachable_since"), SortKey("id", descending=True))
# The Company view's groups, A-Z by label; columns of list_queue_companies' grouped subquery.
COMPANY_GROUP_SORT_KEYS: tuple[SortKey, ...] = (
    SortKey("lower(label)"),
    SortKey("label"),
    SortKey("company_key"),
)
COMPANY_GROUP_CURSOR_LISTING = "job-queue-companies"


@dataclass(frozen=True)
class SearchField:
    key: str
    label: str
    haystack_sql: str


# Each search term must appear somewhere in the field's text. "all" joins
# the same columns the old client-side search did, with concat_ws skipping
# NULLs the way the client skipped null/undefined.
JOB_SEARCH_FIELDS: tuple[SearchField, ...] = (
    SearchField(
        "all",
        "All Fields",
        "concat_ws(' ', '#' || id, company, job_title, source, url, status, capture_label,"
        " sender_email, duplicate_hint, date_found, fit_score::text)",
    ),
    SearchField("job_title", "Job Title", "COALESCE(job_title, '')"),
    SearchField("company", "Company Name", "COALESCE(company, '')"),
    SearchField("job_id", "Job Id", "id::text"),
)
SEARCH_FIELDS_BY_KEY: dict[str, SearchField] = {field.key: field for field in JOB_SEARCH_FIELDS}
DEFAULT_SEARCH_FIELD = "company"
# Below this, a substring match (a bare "r" or "th") hits so much of the list
# that it isn't a useful filter, so shorter text means "no filter yet". Job
# Id is exempt: a short number is still a precise, deliberate search.
MIN_SEARCH_LENGTH = 3

# Matches a job against contacts exactly the way the card's contact badge
# does: by normalize_company_name() of the job's raw company, which is how
# contacts.company_normalized was keyed at import (no alias resolution).
_JOB_COMPANY_MATCH_KEY_SQL = normalize_company_name_sql("jobs.company")
HAS_CONTACT_SQL = (
    "EXISTS (SELECT 1 FROM contacts c WHERE c.user_id = jobs.user_id"
    f" AND c.company_normalized <> '' AND c.company_normalized = {_JOB_COMPANY_MATCH_KEY_SQL})"
)
# Companies with at least one applied job, by the alias-resolved
# company_normalized identity that Company History and the per-company job
# counts also group on.
APPLIED_COMPANIES_SQL = (
    "SELECT company_normalized FROM jobs"
    " WHERE user_id = %s AND status = 'applied' AND company_normalized <> ''"
)
# A job at one of those companies (one %s: the user id). A job with no
# company ('' -- the column is NOT NULL) is never one, so "hide companies
# I've applied to" never hides it.
AT_APPLIED_COMPANY_SQL = f"company_normalized IN ({APPLIED_COMPANIES_SQL})"

# The Company view's grouping: the same normalizer, with a blank company
# grouped as "Unknown Company" and a name that normalizes to nothing (e.g.
# "Inc.") under its own key. The label is each group's most common spelling.
_GROUP_LABEL_SQL = "COALESCE(NULLIF(TRIM(company), ''), 'Unknown Company')"
COMPANY_GROUP_KEY_SQL = (
    f"COALESCE(NULLIF({normalize_company_name_sql(_GROUP_LABEL_SQL)}, ''), 'unknown-company')"
)
COMPANY_GROUP_LABEL_SQL = _GROUP_LABEL_SQL

# The batch "Re-fetch Triage" and "Refresh unrated" actions: jobs with a URL
# to re-fetch. Shared by the counts their buttons show and the job lists
# they then run over, so the two always agree.
REFRESHABLE_TRIAGE_SQL = f"status = 'triage' AND {NOT_SNOOZED_SQL} AND COALESCE(url, '') <> ''"
UNRATED_REFRESHABLE_SQL = (
    f"{view_where_sql(JobQueueView.BOTH)} AND fit_score IS NULL AND COALESCE(url, '') <> ''"
)


class RefreshCandidateKind(StrEnum):
    TRIAGE = "triage"
    UNRATED = "unrated"


REFRESH_CANDIDATE_SQL: dict[RefreshCandidateKind, str] = {
    RefreshCandidateKind.TRIAGE: REFRESHABLE_TRIAGE_SQL,
    RefreshCandidateKind.UNRATED: UNRATED_REFRESHABLE_SQL,
}


def search_pattern(term: str) -> str:
    """One search term as an ILIKE pattern: * and % match any run of
    characters, ? and _ any single character, and everything else (a
    backslash included) matches literally."""
    escaped = term.replace("\\", "\\\\").replace("*", "%").replace("?", "_")
    return f"%{escaped}%"


@dataclass(frozen=True)
class JobQueueQuery:
    """One Job Queue listing request. Route-validated before it gets here:
    sort and search_field are keys of SORTS_BY_KEY / SEARCH_FIELDS_BY_KEY."""

    view: JobQueueView
    sort: str = DEFAULT_SORT
    search: str = ""
    search_field: str = DEFAULT_SEARCH_FIELD
    only_unscored: bool = False
    only_with_contacts: bool = False
    hide_applied_companies: bool = False
    # Restricts the listing to one Company-view group (COMPANY_GROUP_KEY_SQL).
    company_key: str | None = None

    # ai: claude-opus-5-5 | 2026-11-03
    def sort_keys(self) -> tuple[SortKey, ...]:
        if self.view is JobQueueView.UNREACHABLE:
            return UNREACHABLE_SORT_KEYS
        return SORTS_BY_KEY[self.sort].keys

    def order_by_sql(self) -> str:
        return keyset_order_by_sql(self.sort_keys())

    def cursor_listing(self) -> str:
        """Names this listing's order inside its cursors, so a cursor issued
        under one sort is refused under another."""
        return "job-queue:unreachable" if self.view is JobQueueView.UNREACHABLE else f"job-queue:{self.sort}"

    def search_terms(self) -> list[str]:
        trimmed = self.search.strip()
        if self.search_field != "job_id" and 0 < len(trimmed) < MIN_SEARCH_LENGTH:
            return []
        return trimmed.lower().split()

    def filter_sql(self, user_id: int) -> tuple[str, list[object]]:
        """The search box and filter toggles, as SQL ANDed onto the view's
        rows. Empty string when nothing narrows the view."""
        clauses: list[str] = []
        params: list[object] = []
        haystack = SEARCH_FIELDS_BY_KEY[self.search_field].haystack_sql
        for term in self.search_terms():
            clauses.append(f"{haystack} ILIKE %s")
            params.append(search_pattern(term))
        if self.only_unscored:
            clauses.append("fit_score IS NULL")
        if self.only_with_contacts:
            clauses.append(HAS_CONTACT_SQL)
        if self.hide_applied_companies:
            clauses.append(f"NOT {AT_APPLIED_COMPANY_SQL}")
            params.append(user_id)
        if self.company_key is not None:
            clauses.append(f"{COMPANY_GROUP_KEY_SQL} = %s")
            params.append(self.company_key)
        return " AND ".join(clauses), params
