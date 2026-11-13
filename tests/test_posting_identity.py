# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-10-29
"""Job IDs a site assigns decide duplicates before fuzzy title matching does.

From the 2026-10-29 CVS Health import: several separate "Staff Software
Development Engineer" requisitions (R0993767, R1079811, R1064241) were flagged
possible duplicates of one another on a 100% title match, while the same
requisition under a differently-cased slug was only recognised by luck."""

from __future__ import annotations

from typing import Any

import pytest

from backend.db import (
    PostgresJobRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user
from backend.scanning.import_repair import post_repair_duplicate_check
from backend.scoring.duplicate_checker import DuplicateChecker
from backend.scoring.duplicate_matching import db_match_for_job
from backend.scoring.posting_identity import (
    PostingIdentity,
    are_distinct_postings,
    is_same_posting,
    posting_identity,
)

_CVS = "https://jobs.cvshealth.com/us/en/job"
_TITLE = "Staff Software Development Engineer"


def _cvs_url(req: str, slug: str = "Staff-Software-Development-Engineer") -> str:
    return f"{_CVS}/{req}/{slug}"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (_cvs_url("R0993767"), PostingIdentity("jobs.cvshealth.com", "R0993767", True)),
        (_cvs_url("r0993767", "x"), PostingIdentity("jobs.cvshealth.com", "R0993767", True)),
        (
            "https://careers.cisco.com/global/en/job/1441880/Software-Engineer",
            PostingIdentity("careers.cisco.com", "1441880", True),
        ),
        (
            "https://www.linkedin.com/jobs/view/4385602723/?trk=x",
            PostingIdentity("linkedin.com", "4385602723", False),
        ),
        (
            "https://jobs.ashbyhq.com/acme?ashby_jid=abc-123",
            PostingIdentity("ashbyhq.com", "abc-123", False),
        ),
        # A /job/ segment that is a title slug, not an ID.
        ("https://example.com/job/senior-engineer/apply", None),
        ("https://boards.greenhouse.io/acme/jobs/123", None),
        ("", None),
    ],
)
def test_posting_identity(url: str, expected: PostingIdentity | None) -> None:
    assert posting_identity(url) == expected


def test_same_requisition_is_the_same_posting_whatever_the_slug() -> None:
    assert is_same_posting(_cvs_url("R0993767"), _cvs_url("R0993767", "staff-software-development-engineer"))
    assert not is_same_posting(_cvs_url("R0993767"), _cvs_url("R1079811"))


def test_distinct_only_when_both_name_an_id_on_the_same_site() -> None:
    assert are_distinct_postings(_cvs_url("R0993767"), _cvs_url("R1079811"))
    assert not are_distinct_postings(_cvs_url("R0993767"), _cvs_url("R0993767", "other"))
    assert not are_distinct_postings(_cvs_url("R0993767"), "https://example.com/job/R1079811/x")
    assert not are_distinct_postings(_cvs_url("R0993767"), "")


def test_listing_ids_never_make_postings_distinct() -> None:
    """A repost gets a new LinkedIn/Ashby listing ID; fuzzy matching must still
    be allowed to catch it (tests/test_dismiss_duplicate.py relies on this)."""
    assert not are_distinct_postings(
        "https://www.linkedin.com/jobs/view/1111111111/", "https://www.linkedin.com/jobs/view/2222222222/"
    )
    assert is_same_posting(
        "https://www.linkedin.com/jobs/view/1111111111/?trk=a", "https://www.linkedin.com/jobs/view/1111111111/"
    )


def test_checker_never_matches_a_different_requisition_of_the_same_title() -> None:
    other_req = {"id": 1, "company": "CVS Health", "job_title": _TITLE, "url": _cvs_url("R0993767")}
    assert (
        DuplicateChecker().check(
            job_title=_TITLE, company="CVS Health", job_url=_cvs_url("R1079811"), sheet_jobs=[other_req]
        )
        is None
    )


def test_checker_returns_the_row_it_matched_not_a_same_titled_neighbour() -> None:
    """A different requisition with the same title comes first, but the match is
    the ID-less row -- and the caller is handed that row itself."""
    other_req = {"id": 1, "company": "CVS Health", "job_title": _TITLE, "url": _cvs_url("R0993767")}
    no_id = {"id": 2, "company": "CVS Health", "job_title": _TITLE, "url": "https://example.com/listing"}
    match = DuplicateChecker().check(
        job_title=_TITLE, company="CVS Health", job_url=_cvs_url("R1079811"), sheet_jobs=[other_req, no_id]
    )
    assert match is not None
    assert match["row"] is no_id


def test_same_requisition_under_another_slug_is_an_exact_match() -> None:
    known = [{"id": 7, "company": "CVS Health", "job_title": _TITLE, "url": _cvs_url("R0993767"), "status": "triage"}]
    _status, source, _hint, matched_id, _scores = db_match_for_job(
        job_title=_TITLE,
        company="CVS Health",
        job_url=_cvs_url("R0993767", "staff-software-development-engineer"),
        known_jobs=known,
        dedup_checker=DuplicateChecker(),
    )
    assert (source, matched_id) == ("db_url", 7)


def _make_user(conn: Any) -> int:
    account = PostgresUserAccountRepository(conn).create_pending(
        "owner@example.com", "seed-owner@example.com", "2099-01-01"
    )
    set_current_user(conn.cursor(), account["id"])
    conn.commit()
    return account["id"]


def test_recheck_clears_a_flag_between_two_requisitions(pg_dsn: str) -> None:
    """The re-check that cleared the existing CVS flags, against real rows."""
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user(conn)
        repo = PostgresJobRepository(conn)
        original_id = repo.insert(
            user_id,
            {"company": "CVS Health", "job_title": _TITLE, "url": _cvs_url("R0993767"), "status": "triage"},
        )
        flagged_id = repo.insert(
            user_id,
            {
                "company": "CVS Health",
                "job_title": _TITLE,
                "url": _cvs_url("R1079811"),
                "status": "possible_duplicate",
                "duplicate_of_id": original_id,
                "duplicate_hint": "Possible duplicate of ...",
                "fit_score": 80,
            },
        )
        conn.commit()

        changed = post_repair_duplicate_check(user_id, repo, repo.get_by_id(user_id, flagged_id))
        conn.commit()

        assert changed is True
        cleared = repo.get_by_id(user_id, flagged_id)
        assert cleared["status"] == "pending_review"
        assert cleared["duplicate_of_id"] is None
        assert cleared["duplicate_hint"] is None
    finally:
        conn.close()
