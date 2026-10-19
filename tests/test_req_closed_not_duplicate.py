# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""A req-closed job must not be the destination of a fuzzy duplicate match.

A closed requisition is a dead listing, so a similar new posting at the same
company is a fresh opening rather than a duplicate of it. Regression case: job
2666 ("Sr. Full Stack Engineer" at Tapcart) was flagged possible_duplicate of
job 2665 ("Sr. React Native Engineer" at the same company) after 2665 had been
marked req closed.

An *exact URL* match is deliberately still flagged even when req closed — it is
literally the same posting, and jobs.url carries a per-user unique index.
"""

from __future__ import annotations

from backend.db import (
    PostgresApplicationStageRepository,
    PostgresJobRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user
from backend.scoring.duplicate_checker import DuplicateChecker
from backend.scoring.duplicate_matching import (
    db_match_for_job,
    dismissed_duplicate_ids,
)

_TAPCART_URL = "https://jobs.ashbyhq.com/tapcart/react-native"


def _match(
    known: list[dict],
    *,
    job_url: str = "https://example.com/new-posting",
    dismissed: list[int] | None = None,
):
    return db_match_for_job(
        job_title="Sr. Full Stack Engineer",
        company="Tapcart",
        job_url=job_url,
        known_jobs=known,
        dedup_checker=DuplicateChecker(),
        dismissed_ids=dismissed,
    )


def _tapcart_row(**overrides: object) -> dict:
    row: dict = {
        "id": 2665,
        "job_title": "Sr. React Native Engineer",
        "company": "Tapcart",
        "url": _TAPCART_URL,
        "status": "pending_review",
        "skip_reason": None,
    }
    row.update(overrides)
    return row


def test_open_job_still_fuzzy_matches() -> None:
    """Guards the test itself: without req_closed these two titles do match."""
    _tracked, source, hint, matched_id, _scores = _match([_tapcart_row()])
    assert source == "db_fuzzy"
    assert matched_id == 2665
    assert hint is not None


def test_req_closed_job_is_not_a_fuzzy_duplicate_destination() -> None:
    row = _tapcart_row(status="skipped", skip_reason="req_closed")
    assert _match([row]) == (None, None, None, None, None)


def test_other_skip_reasons_still_match() -> None:
    """Only req_closed is excluded — a job skipped for any other reason is
    still a live thing the user chose to pass on, and remains match-worthy."""
    row = _tapcart_row(status="skipped", skip_reason="not_interested")
    _tracked, source, _hint, matched_id, _scores = _match([row])
    assert source == "db_fuzzy"
    assert matched_id == 2665


def test_req_closed_job_still_matches_on_exact_url() -> None:
    """jobs.url is uniquely indexed, so the same posting must stay recognisable
    regardless of status or a re-import would hit DuplicateJobUrlError."""
    row = _tapcart_row(status="skipped", skip_reason="req_closed")
    _tracked, source, _hint, matched_id, _scores = _match([row], job_url=_TAPCART_URL)
    assert source == "db_url"
    assert matched_id == 2665


# ── Dismissed matches must not come back on a later re-score ──────────────────


def test_dismissed_match_is_not_reflagged() -> None:
    """A re-score must not resurrect a match the user dismissed — that silently
    undid their /dismiss-duplicate call (how job 2666 got re-flagged against
    2665 despite already listing it in dismissed_duplicate_ids)."""
    assert _match([_tapcart_row()], dismissed=[2665]) == (None, None, None, None, None)


def test_dismissed_match_is_not_reflagged_on_exact_url() -> None:
    row = _tapcart_row()
    assert _match([row], job_url=_TAPCART_URL, dismissed=[2665]) == (
        None,
        None,
        None,
        None,
        None,
    )


def test_dismissed_duplicate_ids_parses_stored_json() -> None:
    assert dismissed_duplicate_ids({"dismissed_duplicate_ids": "[2664, 2665]"}) == {
        2664,
        2665,
    }


def test_dismissed_duplicate_ids_tolerates_unset_and_corrupt_values() -> None:
    """Returns an empty set rather than raising, so callers can filter
    unconditionally on rows written before the column existed."""
    for stored in (None, "", "not json", '{"a": 1}', "[1, null, \"x\"]"):
        result = dismissed_duplicate_ids({"dismissed_duplicate_ids": stored})
        assert isinstance(result, set)
    assert dismissed_duplicate_ids({}) == set()
    assert dismissed_duplicate_ids({"dismissed_duplicate_ids": '[1, null, "x"]'}) == {1}


# ── On-sheet shape: a 'req_closed' application_stages row, job keeps its status ─


def test_on_sheet_req_closed_flag_excludes_fuzzy_match() -> None:
    """Sheet jobs record a closed req as a stage row, not on the jobs row, so the
    row alone says 'applied'. get_all_ordered folds the stage into is_req_closed."""
    row = _tapcart_row(status="applied", is_req_closed=True)
    assert _match([row]) == (None, None, None, None, None)


def test_on_sheet_job_without_flag_still_matches() -> None:
    row = _tapcart_row(status="applied", is_req_closed=False)
    _tracked, source, _hint, matched_id, _scores = _match([row])
    assert source == "db_fuzzy"
    assert matched_id == 2665


def test_duplicate_checker_itself_skips_req_closed_rows() -> None:
    """The exclusion lives in DuplicateChecker.check, so callers that bypass
    db_match_for_job (ingest, board search, repair) get it too."""
    checker = DuplicateChecker()
    closed = _tapcart_row(status="skipped", skip_reason="req_closed")
    flagged = _tapcart_row(id=2670, status="applied", is_req_closed=True)
    open_row = _tapcart_row(id=2671)
    kwargs = {"job_title": "Sr. Full Stack Engineer", "company": "Tapcart"}
    assert checker.check(sheet_jobs=[closed, flagged], **kwargs) is None
    assert checker.check(sheet_jobs=[closed, flagged, open_row], **kwargs) is not None


def _new_job(title: str, **overrides: object) -> dict:
    job: dict = {
        "job_title": title,
        "company": "Tapcart",
        "url": f"https://example.com/{title.replace(' ', '-').lower()}",
        "status": "pending_review",
    }
    job.update(overrides)
    return job


def test_get_all_ordered_flags_both_req_closed_shapes(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        account = PostgresUserAccountRepository(conn).create_pending(
            "owner@example.com", "seed-owner@example.com", "2099-01-01"
        )
        user_id = account["id"]
        set_current_user(conn.cursor(), user_id)
        jobs = PostgresJobRepository(conn)
        stages = PostgresApplicationStageRepository(conn)

        queue_closed = jobs.insert(
            user_id, _new_job("queue closed", status="skipped", skip_reason="req_closed")
        )
        sheet_closed = jobs.insert(user_id, _new_job("sheet closed", status="applied"))
        stages.insert(
            user_id,
            {"job_id": sheet_closed, "status": "req_closed", "date": "2026-09-01"},
        )
        sheet_open = jobs.insert(user_id, _new_job("sheet open", status="applied"))
        stages.insert(
            user_id, {"job_id": sheet_open, "status": "applied", "date": "2026-09-01"}
        )
        skipped_other = jobs.insert(
            user_id, _new_job("skipped other", status="skipped", skip_reason="not_a_fit")
        )
        conn.commit()

        flags = {row["id"]: row["is_req_closed"] for row in jobs.get_all_ordered(user_id)}
        assert flags == {
            queue_closed: True,
            sheet_closed: True,
            sheet_open: False,
            skipped_other: False,
        }
    finally:
        conn.close()
