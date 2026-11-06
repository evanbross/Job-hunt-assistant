# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-10-23
"""Contract tests for the Job Queue page's server-side listing
(backend/db/job_queue_query.py + PostgresJobRepository's list_queue_page /
count_queue / list_queue_companies / list_refresh_candidates /
queue_position, and the GET /api/jobs/queue* routes), against real Postgres.
The browser used to filter, sort and count these lists itself; each test
here pins one of those behaviours to its SQL replacement."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from flask import Flask

import config
from backend.db import (
    PostgresApplicationStageRepository,
    PostgresCompanyAliasRepository,
    PostgresContactRepository,
    PostgresJobRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.job_queue_query import (
    JOB_QUEUE_SORTS,
    JOB_SEARCH_FIELDS,
    JobQueueQuery,
    JobQueueView,
    RefreshCandidateKind,
)
from backend.db.keyset_pagination import encode_cursor
from backend.db.row_level_security import set_current_user
from routes.jobs import jobs_bp
from tests.db_roles import runtime_database_url

_HEAVY_COLUMNS = {"job_description", "raw_jd_snippet", "fit_reasoning", "certainty_reason"}
_FUTURE = "2099-01-01"
_PAST = "2020-01-01"


def _make_user(conn: Any, email: str = "queue-listing@example.com") -> int:
    account = PostgresUserAccountRepository(conn).create_pending(email, f"seed-{email}", _FUTURE)
    set_current_user(conn.cursor(), account["id"])
    conn.commit()
    return account["id"]


def _act_as(conn: Any, user_id: int) -> None:
    set_current_user(conn.cursor(), user_id)
    conn.commit()


@pytest.fixture
def db(pg_dsn: str) -> Iterator[tuple[Any, PostgresJobRepository, int]]:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        yield conn, PostgresJobRepository(conn), user_id
    finally:
        conn.close()


def _insert(repo: PostgresJobRepository, user_id: int, **fields: Any) -> int:
    fields.setdefault("job_title", "Engineer")
    fields.setdefault("date_found", "2026-10-01")
    return repo.insert(user_id, fields)


def _ids(rows: list[dict[str, Any]]) -> list[int]:
    return [row["id"] for row in rows]


# ai: claude-opus-5-5 | 2026-11-03 — follows keyset cursors instead of offsets
def _all_pages(repo: PostgresJobRepository, user_id: int, query: JobQueueQuery, page_size: int) -> list[int]:
    ids: list[int] = []
    after = None
    while True:
        page = repo.list_queue_page(user_id, query, page_size, after)
        ids.extend(_ids(page.rows))
        if not page.has_more:
            return ids
        after = page.next_after


def test_views_select_their_statuses_hide_snoozed_and_use_display_columns(db) -> None:
    conn, repo, user_id = db
    queue = _insert(repo, user_id, company="A", status="pending_review", job_description="JD text")
    snooze_over = _insert(repo, user_id, company="B", status="pending_review", snoozed_until=_PAST)
    _insert(repo, user_id, company="C", status="pending_review", snoozed_until=_FUTURE)
    triage = _insert(repo, user_id, company="D", status="triage", fit_reasoning="Why")
    duplicate = _insert(repo, user_id, company="E", status="possible_duplicate")
    _insert(repo, user_id, company="F", status="applied")
    _insert(repo, user_id, company="G", status="skipped")
    unreachable_applied = _insert(
        repo, user_id, company="H", status="applied", unreachable_since="2026-10-01T00:00:00+00:00"
    )
    unreachable_snoozed = _insert(
        repo, user_id, company="I", status="triage", snoozed_until=_FUTURE,
        unreachable_since="2026-10-02T00:00:00+00:00",
    )
    conn.commit()

    def view_ids(view: JobQueueView) -> set[int]:
        return set(_ids(repo.list_queue_page(user_id, JobQueueQuery(view=view), 100).rows))

    assert view_ids(JobQueueView.QUEUE) == {queue, snooze_over}
    assert view_ids(JobQueueView.TRIAGE) == {triage, duplicate}
    assert view_ids(JobQueueView.BOTH) == {queue, snooze_over, triage, duplicate}
    assert view_ids(JobQueueView.COMPANY) == {queue, snooze_over, triage, duplicate}
    # The Unreachable view is every flagged job whatever its status, snoozed or not,
    # longest-unreachable first.
    unreachable = repo.list_queue_page(user_id, JobQueueQuery(view=JobQueueView.UNREACHABLE), 100).rows
    assert _ids(unreachable) == [unreachable_applied, unreachable_snoozed]

    rows = {row["id"]: row for row in repo.list_queue_page(user_id, JobQueueQuery(view=JobQueueView.BOTH), 100).rows}
    for row in rows.values():
        assert _HEAVY_COLUMNS.isdisjoint(row.keys())
    assert rows[queue]["has_description"] is True
    assert rows[triage]["has_fit_reasoning"] is True
    assert rows[duplicate]["description_unavailable"] is True


# Independent reference orders for every sort key: each is a stable sort by
# the tie-breakers first, then by the primary key.
def _expected_order(rows: list[dict[str, Any]], sort: str) -> list[int]:
    def confidence(row: dict[str, Any]) -> int:
        if row["certainty_score"] is not None:
            return row["certainty_score"]
        return -1 if row["status"] == "possible_duplicate" else 50

    newest = sorted(rows, key=lambda r: (r["date_found"], r["id"]), reverse=True)
    oldest = sorted(rows, key=lambda r: (r["date_found"], r["id"]))
    by_sort: dict[str, Callable[[], list[dict[str, Any]]]] = {
        "date_desc": lambda: newest,
        "date_asc": lambda: oldest,
        "fit_desc_date_desc": lambda: sorted(newest, key=lambda r: (r["fit_score"] is None, -(r["fit_score"] or 0))),
        "fit_desc_date_asc": lambda: sorted(oldest, key=lambda r: (r["fit_score"] is None, -(r["fit_score"] or 0))),
        "fit_asc": lambda: sorted(newest, key=lambda r: (r["fit_score"] is None, r["fit_score"] or 0)),
        "confidence_desc": lambda: sorted(newest, key=lambda r: -confidence(r)),
        "confidence_asc": lambda: sorted(newest, key=confidence),
        "company_asc": lambda: sorted(newest, key=lambda r: r["company"].lower()),
    }
    return [row["id"] for row in by_sort[sort]()]


def test_every_sort_matches_its_definition_and_pages_cover_each_row_exactly_once(db) -> None:
    conn, repo, user_id = db
    specs = [
        ("Delta", 80, "2026-10-01", None, "pending_review"),
        ("alpha", None, "2026-10-03", 70, "triage"),
        ("Charlie", 80, "2026-10-03", None, "possible_duplicate"),
        ("bravo", 55, "2026-10-02", 90, "triage"),
        ("Echo", None, "2026-10-01", None, "pending_review"),
        ("Alpha", 95, "2026-10-02", 50, "pending_review"),
        ("foxtrot", 55, "2026-10-03", 10, "triage"),
        ("Golf", 80, "2026-10-02", None, "triage"),
        ("hotel", 80, "2026-10-02", None, "possible_duplicate"),
    ]
    rows = []
    for company, fit, date_found, certainty, status in specs:
        job_id = _insert(
            repo, user_id, company=company, fit_score=fit, date_found=date_found,
            certainty_score=certainty, status=status,
        )
        rows.append({
            "id": job_id, "company": company, "fit_score": fit, "date_found": date_found,
            "certainty_score": certainty, "status": status,
        })
    conn.commit()

    assert {sort.key for sort in JOB_QUEUE_SORTS} == {
        "date_desc", "date_asc", "fit_desc_date_desc", "fit_desc_date_asc",
        "fit_asc", "confidence_desc", "confidence_asc", "company_asc",
    }
    # ai: claude-opus-5-5 | 2026-11-03 — every page size, so every row (NULL-valued ones
    # included) is some page's cursor and each SortKey's after-NULL branch is exercised.
    for sort in JOB_QUEUE_SORTS:
        query = JobQueueQuery(view=JobQueueView.BOTH, sort=sort.key)
        for page_size in range(1, 5):
            assert _all_pages(repo, user_id, query, page_size) == _expected_order(rows, sort.key), (
                sort.key, page_size,
            )


def test_search_terms_wildcards_fields_and_minimum_length(db) -> None:
    conn, repo, user_id = db
    acme = _insert(
        repo, user_id, company="Acme Robotics", job_title="Senior Backend Engineer",
        status="pending_review", url="https://acme.example/jobs/42",
    )
    beta = _insert(repo, user_id, company="Beta Labs", job_title="Frontend Developer", status="triage",
                   url="https://beta.example/1")
    gamma = _insert(repo, user_id, company="Gamma_Co", job_title="Data Engineer", status="triage",
                    url="https://gamma.example/1")
    slash = _insert(repo, user_id, company="Back\\slash", job_title="Writer", status="triage",
                    url="https://slash.example/1")
    conn.commit()

    def search(text: str, field: str = "company") -> set[int]:
        query = JobQueueQuery(view=JobQueueView.BOTH, search=text, search_field=field)
        return set(_ids(repo.list_queue_page(user_id, query, 100).rows))

    everyone = {acme, beta, gamma, slash}
    assert search("acm") == {acme}
    assert search("ACM") == {acme}
    assert search("ac") == everyone  # under 3 characters: no filter yet
    assert search("a*e") == {acme}  # * is any run of characters
    assert search("a%e") == {acme}  # so is %
    assert search("?eta") == {beta}  # ? is one character
    assert search("mma_co") == {gamma}  # so is _
    assert search("k\\s") == {slash}  # a backslash matches literally
    assert search("engineer senior", "job_title") == {acme}  # every term, any order
    assert search("engineer", "job_title") == {acme, gamma}
    assert search("acme.example", "all") == {acme}  # "all" covers the url
    assert search("possible_dup", "all") == set()  # "all" covers the status too
    assert search("pending_rev", "all") == {acme}
    assert search(str(gamma), "job_id") == {gamma}  # Job Id ignores the 3-character minimum
    assert search("robotics", "job_title") == set()
    assert {field.key for field in JOB_SEARCH_FIELDS} == {"all", "job_title", "company", "job_id"}


@pytest.fixture
def filter_scenario(db) -> dict[str, Any]:
    """One account's jobs for the filter-toggle and count tests, plus another
    account's rows that must never leak into them."""
    conn, repo, user_id = db
    aliases = PostgresCompanyAliasRepository(conn)
    _insert(repo, user_id, company="Cash App", status="applied", url="https://jobs.example/cash")
    _insert(repo, user_id, company="Seeq Corporation", status="applied", url="https://jobs.example/seeq-applied")
    conn.commit()
    aliases.upsert(user_id, alias_company="Block", canonical_company="Cash App")
    conn.commit()
    ids = {
        "acme_inc": _insert(repo, user_id, company="ACME, Inc.", status="pending_review", fit_score=None,
                            url="https://jobs.example/acme"),
        "acme_robotics": _insert(repo, user_id, company="Acme Robotics", status="triage", fit_score=70,
                                 url="https://jobs.example/acme-robotics"),
        "block": _insert(repo, user_id, company="Block", status="triage", fit_score=None),
        "seeq": _insert(repo, user_id, company="Seeq", status="possible_duplicate", fit_score=60,
                        url="https://jobs.example/seeq"),
        "no_company": _insert(repo, user_id, company="", status="pending_review", fit_score=50,
                              url="https://jobs.example/blank"),
        "beta": _insert(repo, user_id, company="Beta", status="pending_review", fit_score=40,
                        url="https://jobs.example/beta"),
    }
    _insert(repo, user_id, company="Zed", status="triage", snoozed_until=_FUTURE, url="https://jobs.example/zed")
    _insert(repo, user_id, company="Gone", status="applied", unreachable_since="2026-10-01T00:00:00+00:00",
            url="https://jobs.example/gone")
    PostgresContactRepository(conn).sync_import(user_id, [{
        "dedup_key": "contact-acme", "full_name": "Ada Lovelace", "company_raw": "Acme Inc",
        "company_normalized": "acme", "position": "Engineer", "normalized_title": "engineer",
        "profile_url": None, "email_address": None, "connected_on": None,
        "imported_at": "2026-10-01T00:00:00+00:00",
    }])
    conn.commit()

    other_user = _make_user(conn, "queue-other@example.com")
    _insert(repo, other_user, company="Beta LLC", status="applied", url="https://jobs.example/other-beta")
    _insert(repo, other_user, company="Acme Inc", status="pending_review", url="https://jobs.example/other-acme")
    conn.commit()
    _act_as(conn, user_id)
    return {"repo": repo, "user_id": user_id, "ids": ids}


def test_filter_toggles_match_the_old_client_side_rules(filter_scenario) -> None:
    repo, user_id, ids = filter_scenario["repo"], filter_scenario["user_id"], filter_scenario["ids"]

    def listing(**filters: Any) -> set[int]:
        query = JobQueueQuery(view=JobQueueView.BOTH, **filters)
        return set(_ids(repo.list_queue_page(user_id, query, 100).rows))

    assert listing() == set(ids.values())
    assert listing(only_unscored=True) == {ids["acme_inc"], ids["block"]}
    # Contacts match by the job's raw company, normalized ("ACME, Inc." ~ "Acme Inc"),
    # and never another account's contacts or jobs.
    assert listing(only_with_contacts=True) == {ids["acme_inc"]}
    # Hidden: "Block" (aliased to the applied "Cash App") and "Seeq" (applied as
    # "Seeq Corporation"). Kept: a blank company, and "Beta", which only another
    # account applied to.
    assert listing(hide_applied_companies=True) == {
        ids["acme_inc"], ids["acme_robotics"], ids["no_company"], ids["beta"],
    }
    assert listing(only_unscored=True, hide_applied_companies=True) == {ids["acme_inc"]}


def test_counts_cover_every_number_the_page_shows(filter_scenario) -> None:
    repo, user_id = filter_scenario["repo"], filter_scenario["user_id"]

    counts = repo.count_queue(user_id, JobQueueQuery(view=JobQueueView.BOTH, hide_applied_companies=True))

    assert counts == {
        "total_all": 6,
        "total": 4,
        "unscored": 2,
        "with_contacts": 1,
        "at_applied_company": 2,
        "triage": 3,
        "unreachable": 1,
        "refreshable_triage": 1,  # Acme Robotics: Block has no URL, Seeq is a duplicate
        "unrated_refreshable": 1,  # ACME, Inc.: Block has no URL
    }
    queue_counts = repo.count_queue(user_id, JobQueueQuery(view=JobQueueView.QUEUE))
    assert (queue_counts["total_all"], queue_counts["unscored"], queue_counts["at_applied_company"]) == (3, 1, 0)


def test_refresh_candidates_are_exactly_what_their_counts_count(filter_scenario) -> None:
    repo, user_id, ids = filter_scenario["repo"], filter_scenario["user_id"], filter_scenario["ids"]

    triage = repo.list_refresh_candidates(user_id, RefreshCandidateKind.TRIAGE)
    unrated = repo.list_refresh_candidates(user_id, RefreshCandidateKind.UNRATED)

    assert _ids(triage) == [ids["acme_robotics"]]
    assert _ids(unrated) == [ids["acme_inc"]]
    assert set(triage[0]) == {"id", "company", "job_title", "fit_score", "status", "url"}


def test_company_groups_merge_spellings_and_list_their_own_jobs(db) -> None:
    conn, repo, user_id = db
    seeq_queue = _insert(repo, user_id, company="Seeq", status="pending_review", date_found="2026-10-03")
    seeq_triage = _insert(repo, user_id, company="Seeq", status="triage", date_found="2026-10-02")
    seeq_corp = _insert(repo, user_id, company="Seeq Corporation", status="triage", date_found="2026-10-01")
    blank = _insert(repo, user_id, company="  ", status="triage")
    suffix_only = _insert(repo, user_id, company="Inc.", status="pending_review")
    _insert(repo, user_id, company="Seeq", status="applied")
    conn.commit()

    company_view = JobQueueQuery(view=JobQueueView.COMPANY)
    groups = repo.list_queue_companies(user_id, company_view, 100).rows

    assert groups == [
        {"company_key": "unknown-company", "label": "Inc.", "total": 1, "queue_count": 1, "triage_count": 0},
        {"company_key": "seeq", "label": "Seeq", "total": 3, "queue_count": 1, "triage_count": 2},
        {"company_key": "unknown", "label": "Unknown Company", "total": 1, "queue_count": 0, "triage_count": 1},
    ]
    # ai: claude-opus-5-5 | 2026-11-03 — paged one group at a time by keyset cursor
    first = repo.list_queue_companies(user_id, company_view, 1)
    second = repo.list_queue_companies(user_id, company_view, 1, first.next_after)
    assert (first.rows, second.rows, second.has_more) == ([groups[0]], [groups[1]], True)

    def group_ids(key: str) -> list[int]:
        query = JobQueueQuery(view=JobQueueView.COMPANY, company_key=key)
        return _ids(repo.list_queue_page(user_id, query, 100).rows)

    assert group_ids("seeq") == [seeq_queue, seeq_triage, seeq_corp]
    assert group_ids("unknown") == [blank]
    assert group_ids("unknown-company") == [suffix_only]


def test_queue_position_is_the_jobs_index_in_the_listing(db) -> None:
    conn, repo, user_id = db
    ids = [_insert(repo, user_id, company=f"C{n}", status="pending_review", date_found=f"2026-10-{n + 1:02d}")
           for n in range(5)]
    _insert(repo, user_id, company="Applied", status="applied")
    conn.commit()
    query = JobQueueQuery(view=JobQueueView.QUEUE, sort="date_asc")

    assert [repo.queue_position(user_id, query, job_id) for job_id in ids] == [0, 1, 2, 3, 4]
    assert repo.queue_position(user_id, JobQueueQuery(view=JobQueueView.TRIAGE), ids[0]) is None


# --- Routes ------------------------------------------------------------------


@pytest.fixture
def client(db, monkeypatch: pytest.MonkeyPatch, pg_dsn: str):
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(jobs_bp, url_prefix="/api")
    test_client = app.test_client()
    with test_client.session_transaction() as flask_session:
        flask_session["user_id"] = db[2]
    return test_client


@pytest.mark.parametrize(
    "query_string",
    [
        "",  # view is required
        "view=everything",
        "view=queue&sort=random",
        "view=queue&search_field=notes",
        "view=queue&limit=201",
        "view=queue&offset=0",  # ai: claude-opus-5-5 | 2026-11-03 — replaced by cursor
        "view=queue&cursor=not-a-cursor",
        # A cursor issued under another sort, or for the Company view's groups.
        f"view=queue&sort=date_desc&cursor={encode_cursor('job-queue:date_asc', ['2026-10-01', '1'])}",
        f"view=queue&cursor={encode_cursor('job-queue-companies', ['a', 'a', 'a'])}",
        f"view=queue&cursor={encode_cursor('job-queue:date_desc', ['2026-10-01'])}",  # too few values
        "view=queue&surprise=1",
    ],
)
def test_page_route_rejects_invalid_parameters(client, query_string: str) -> None:
    response = client.get(f"/api/jobs/queue?{query_string}")
    assert response.status_code == 400


# ai: claude-opus-5-5 | 2026-11-03
def test_page_route_rejects_a_hand_edited_cursor_postgres_cannot_compare(client, db) -> None:
    conn, repo, user_id = db
    _insert(repo, user_id, company="A", status="triage")
    conn.commit()
    cursor = encode_cursor("job-queue:fit_asc", ["not-a-number", "2026-10-01", "1"])

    response = client.get(f"/api/jobs/queue?view=triage&sort=fit_asc&cursor={cursor}")

    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid cursor"}


# ai: claude-opus-5-5 | 2026-11-03 — pages by next_cursor instead of offset
def test_page_route_pages_by_cursor_and_extends_to_a_focused_job(client, db) -> None:
    conn, repo, user_id = db
    ids = [_insert(repo, user_id, company=f"C{n}", status="triage", date_found=f"2026-10-{n + 1:02d}")
           for n in range(5)]
    conn.commit()

    pages = [client.get("/api/jobs/queue?view=triage&sort=date_asc&limit=2").get_json()]
    while pages[-1]["has_more"]:
        cursor = pages[-1]["next_cursor"]
        pages.append(client.get(f"/api/jobs/queue?view=triage&sort=date_asc&limit=2&cursor={cursor}").get_json())
    focused = client.get(
        f"/api/jobs/queue?view=triage&sort=date_asc&limit=2&focus_job_id={ids[4]}&only_unscored=true"
    ).get_json()

    assert [_ids(page["jobs"]) for page in pages] == [ids[:2], ids[2:4], ids[4:]]
    assert pages[-1]["next_cursor"] is None
    assert (_ids(focused["jobs"]), focused["has_more"], focused["next_cursor"]) == (ids, False, None)


# ── code-review-2026-11-02 #1: rows that move between page requests ─────────
# ai: claude-opus-5-5 | 2026-11-03
# Each case changes a page-1 row between fetching page 1 and page 2. With OFFSET
# pagination every row after it shifted up one, so page 2 started one row too
# late and that row never appeared; each of these assertions failed. With a
# keyset cursor page 2 starts after page 1's last row, wherever the rows above it went.


def _two_pages_around_a_change(
    repo: PostgresJobRepository, conn: Any, user_id: int, query: JobQueueQuery, change: Callable[[], None]
) -> list[int]:
    first = repo.list_queue_page(user_id, query, 3)
    change()
    conn.commit()
    second = repo.list_queue_page(user_id, query, 10, first.next_after)
    assert not second.has_more
    return _ids(first.rows) + _ids(second.rows)


def test_a_row_rescored_below_the_page_boundary_skips_nothing(db) -> None:
    conn, repo, user_id = db
    ids = [_insert(repo, user_id, company=f"C{fit}", status="triage", fit_score=fit) for fit in (90, 80, 70, 60, 50, 40)]
    conn.commit()
    query = JobQueueQuery(view=JobQueueView.TRIAGE, sort="fit_desc_date_desc")

    seen = _two_pages_around_a_change(repo, conn, user_id, query, lambda: repo.update(user_id, ids[1], {"fit_score": 10}))

    # Nothing skipped; the re-scored job shows up again after the cursor (the
    # list keeps the copy it already shows), and nothing else repeats.
    assert set(seen) == set(ids)
    assert seen == [ids[0], ids[1], ids[2], ids[3], ids[4], ids[5], ids[1]]


def test_a_row_that_leaves_the_filter_or_the_view_skips_nothing(db) -> None:
    conn, repo, user_id = db
    ids = [_insert(repo, user_id, company=f"C{n}", status="triage", date_found=f"2026-10-{20 - n:02d}")
           for n in range(6)]
    conn.commit()

    unscored = JobQueueQuery(view=JobQueueView.TRIAGE, only_unscored=True)
    seen = _two_pages_around_a_change(repo, conn, user_id, unscored, lambda: repo.update(user_id, ids[0], {"fit_score": 75}))
    assert seen == ids

    triage = JobQueueQuery(view=JobQueueView.TRIAGE)
    seen = _two_pages_around_a_change(repo, conn, user_id, triage, lambda: repo.update(user_id, ids[1], {"status": "applied"}))
    assert seen == ids


def test_company_groups_page_by_cursor_when_a_group_disappears(db) -> None:
    conn, repo, user_id = db
    ids = [_insert(repo, user_id, company=name, status="triage") for name in ("Acme", "Beta", "Cobalt", "Delta")]
    conn.commit()
    query = JobQueueQuery(view=JobQueueView.COMPANY)

    first = repo.list_queue_companies(user_id, query, 2)
    repo.update(user_id, ids[0], {"status": "applied"})  # Acme's only job leaves: its group goes
    conn.commit()
    second = repo.list_queue_companies(user_id, query, 10, first.next_after)

    assert [g["label"] for g in first.rows + second.rows] == ["Acme", "Beta", "Cobalt", "Delta"]


def test_companies_and_history_routes_page_by_cursor(client, db) -> None:
    conn, repo, user_id = db
    for name in ("Acme", "Beta", "Cobalt"):
        _insert(repo, user_id, company=name, status="triage")
    history_ids = [_insert(repo, user_id, company=f"H{n}", status="applied", date_found=f"2026-09-{n + 1:02d}")
                   for n in range(3)]
    conn.commit()

    companies = client.get("/api/jobs/queue/companies?view=company&limit=2").get_json()
    more = client.get(f"/api/jobs/queue/companies?view=company&limit=2&cursor={companies['next_cursor']}").get_json()
    assert [g["label"] for g in companies["companies"] + more["companies"]] == ["Acme", "Beta", "Cobalt"]
    assert (more["has_more"], more["next_cursor"]) == (False, None)

    history = client.get("/api/jobs/history?limit=2").get_json()
    rest = client.get(f"/api/jobs/history?limit=2&cursor={history['next_cursor']}").get_json()
    assert _ids(history["jobs"]) + _ids(rest["jobs"]) == history_ids[::-1]
    assert all("stages" in job for job in history["jobs"] + rest["jobs"])

    # A cursor only works for the listing that issued it, and offset is gone.
    assert client.get(f"/api/jobs/history?cursor={companies['next_cursor']}").status_code == 400
    assert client.get("/api/jobs/history?offset=2").status_code == 400


def test_options_counts_companies_and_candidates_routes(client, db) -> None:
    conn, repo, user_id = db
    _insert(repo, user_id, company="Acme", status="triage", url="https://jobs.example/a")
    conn.commit()

    options = client.get("/api/jobs/queue/options").get_json()
    counts = client.get("/api/jobs/queue/counts?view=both").get_json()
    companies = client.get("/api/jobs/queue/companies?view=company").get_json()
    candidates = client.get("/api/jobs/queue/refresh-candidates?kind=triage").get_json()

    assert [sort["key"] for sort in options["sorts"]] == [sort.key for sort in JOB_QUEUE_SORTS]
    assert [field["key"] for field in options["search_fields"]] == [f.key for f in JOB_SEARCH_FIELDS]
    assert options["view_statuses"]["triage"] == ["triage", "possible_duplicate"]
    assert options["view_statuses"]["unreachable"] is None
    assert (counts["total_all"], counts["triage"], counts["refreshable_triage"]) == (1, 1, 1)
    assert companies == {
        "companies": [{"company_key": "acme", "label": "Acme", "total": 1, "queue_count": 0, "triage_count": 1}],
        "has_more": False,
        "next_cursor": None,  # ai: claude-opus-5-5 | 2026-11-03 — keyset pagination
    }
    assert [job["company"] for job in candidates] == ["Acme"]
    assert client.get("/api/jobs/queue/refresh-candidates?kind=everything").status_code == 400
    assert client.get("/api/jobs/queue/counts?view=both&limit=5").status_code == 400


# ai: claude-opus-5-5 | 2026-10-24
def test_page_route_attaches_each_jobs_company_summary(client, db) -> None:
    """The badges each card used to work out from three account-wide maps
    (/applications, /jobs/company-counts, /contacts/company-map): history and
    applications match the alias-resolved company_normalized, applications
    report their effective (latest-stage) status, and contacts match the
    plain normalized company name."""
    conn, repo, user_id = db
    stages = PostgresApplicationStageRepository(conn)
    rejected = _insert(repo, user_id, company="Cash App", status="applied", on_sheet=1, url="https://jobs.example/c1")
    stages.insert(user_id, {"job_id": rejected, "status": "applied", "date": "2026-09-01", "notes": None})
    stages.insert(user_id, {"job_id": rejected, "status": "rejected", "date": "2026-09-10", "notes": None})
    _insert(repo, user_id, company="Cash App", status="applied", on_sheet=1, url="https://jobs.example/c2")
    _insert(repo, user_id, company="Cash App", status="skipped", url="https://jobs.example/c3")  # not on the sheet
    conn.commit()
    PostgresCompanyAliasRepository(conn).upsert(user_id, alias_company="Block", canonical_company="Cash App")
    block = _insert(repo, user_id, company="Block", status="triage", date_found="2026-10-02")
    blank = _insert(repo, user_id, company="", status="triage", date_found="2026-10-01")
    PostgresContactRepository(conn).sync_import(user_id, [{
        "dedup_key": "contact-block", "full_name": "Grace Hopper", "company_raw": "Block, Inc.",
        "company_normalized": "block", "position": "Recruiter", "normalized_title": "recruiter",
        "profile_url": "https://www.linkedin.com/in/grace", "email_address": None, "connected_on": None,
        "imported_at": "2026-10-01T00:00:00+00:00",
    }])
    conn.commit()
    other_user = _make_user(conn, "queue-summary-other@example.com")
    _insert(repo, other_user, company="Cash App", status="applied", on_sheet=1, url="https://jobs.example/other")
    conn.commit()
    _act_as(conn, user_id)

    jobs = {job["id"]: job for job in client.get("/api/jobs/queue?view=triage&sort=date_desc").get_json()["jobs"]}

    assert jobs[block]["company_job_count"] == 4
    assert jobs[block]["company_application_statuses"] == {"rejected": 1, "applied": 1}
    assert [contact["full_name"] for contact in jobs[block]["company_contacts"]] == ["Grace Hopper"]
    assert set(jobs[block]["company_contacts"][0]) == {"id", "full_name", "position", "profile_url", "connected_on"}
    assert jobs[blank]["company_job_count"] == 0
    assert jobs[blank]["company_application_statuses"] == {}
    assert jobs[blank]["company_contacts"] == []
