# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================
# ai: claude-opus-5-5 | 2026-10-14
"""browser_capture_queue origin tracking, against a real Postgres schema.

Covers the producer (the repository's enqueue methods writing origin /
origin_detail) and the consumer (routes/import_.py's _describe_capture_origin,
which the import dialog's Origin block is built from) across the real
boundary, so a column/alias mismatch between them fails here rather than as a
silently-empty Origin block.
"""

from __future__ import annotations

import pytest

import config
from backend.db import CaptureQueueOrigin, get_connection
from backend.db.repositories.user_accounts import PostgresUserAccountRepository
from backend.db.row_level_security import set_current_user
from backend.utils.utils import get_repos
from routes.import_ import _describe_capture_origin
from tests.db_roles import runtime_database_url

_EMAIL_LINK = "https://app.welcometothejungle.com/jobs/abc?token=xyz"


@pytest.fixture(autouse=True)
def _test_db(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))


def _new_user(pg_dsn: str, email: str) -> int:
    conn = get_connection(pg_dsn)
    try:
        account = PostgresUserAccountRepository(conn).create_pending(
            email, f"seed-{email}", "2099-01-01"
        )
        conn.commit()
        return account["id"]
    finally:
        conn.close()


def _enqueue_email_link(user_id: int) -> None:
    with get_repos(user_id) as repos:
        set_current_user(repos["conn"].cursor(), user_id)
        repos["browser_capture_queue"].enqueue(
            user_id,
            _EMAIL_LINK,
            "Welcome to the Jungle",
            origin=CaptureQueueOrigin.EMAIL_SCAN,
            origin_detail='WTTJ <alerts@wttj.example> — "5 new jobs"',
        )
        repos["conn"].commit()


def test_enqueue_records_origin_and_find_latest_reads_it_back(pg_dsn: str) -> None:
    user_id = _new_user(pg_dsn, "origin-email@example.com")
    _enqueue_email_link(user_id)

    with get_repos(user_id) as repos:
        set_current_user(repos["conn"].cursor(), user_id)
        exact = repos["browser_capture_queue"].find_latest_for_url(user_id, _EMAIL_LINK)
        # The extension can report the URL with the token stripped.
        stripped = repos["browser_capture_queue"].find_latest_for_url(
            user_id, "https://app.welcometothejungle.com/jobs/abc"
        )

    assert exact is not None
    assert exact["origin"] == "email_scan"
    assert exact["origin_detail"] == 'WTTJ <alerts@wttj.example> — "5 new jobs"'
    assert exact["site_name"] == "Welcome to the Jungle"
    assert exact["created_at"]
    assert stripped is not None and stripped["id"] == exact["id"]


def test_specialized_enqueues_record_fixed_origins(pg_dsn: str) -> None:
    user_id = _new_user(pg_dsn, "origin-fixed@example.com")
    source_url = "https://careers.example.com/jobs"
    check_url = "https://careers.example.com/jobs/7"
    with get_repos(user_id) as repos:
        set_current_user(repos["conn"].cursor(), user_id)
        queue_repo = repos["browser_capture_queue"]
        queue_repo.enqueue_for_source(user_id, source_url, "Example Careers", 11)
        queue_repo.enqueue_once_for_purpose(user_id, check_url, "Example", "closed_req", 7)
        repos["conn"].commit()
        source_row = queue_repo.find_latest_for_url(user_id, source_url)
        check_row = queue_repo.find_latest_for_url(user_id, check_url)

    assert source_row["origin"] == "career_site"
    assert source_row["origin_detail"] == "Example Careers"
    assert check_row["origin"] == "status_check"
    assert check_row["origin_detail"] == "Job #7 (closed_req)"


# ai: claude-sonnet-5 | 2026-10-15
def test_mark_done_by_urls_still_matches_after_a_stale_processing_reset(
    pg_dsn: str,
) -> None:
    """A capture that takes close to (or past) claim_pending's 10-minute
    stale-processing cutoff can have its queue row reset back to 'pending'
    -- by that same mechanism, on a later /queue poll -- in the window
    between the capture starting and it finally posting its result. If
    mark_done_by_urls only matched 'processing', that race permanently lost
    the row: it was never marked done even though the capture genuinely
    succeeded, so the extension kept retrying (and re-opening a browser tab
    for) a job that was already correctly captured. Confirmed live
    2026-10-15 with a slow Workable page reached via a Matcha.fm redirect."""
    user_id = _new_user(pg_dsn, "origin-stale-race@example.com")
    url = "https://matcha.fm/job/race-condition-test"
    with get_repos(user_id) as repos:
        set_current_user(repos["conn"].cursor(), user_id)
        queue_repo = repos["browser_capture_queue"]
        queue_repo.enqueue(
            user_id,
            url,
            "Test Co",
            origin=CaptureQueueOrigin.REFRESH_FROM_URL,
            origin_detail="Job #1",
        )
        repos["conn"].commit()
        queue_repo.claim_pending(user_id, limit=10)  # pending -> processing
        repos["conn"].commit()

        # Simulate claim_pending's own stale-processing reset firing on a
        # later poll while this capture is still in flight (status back to
        # 'pending', the same transition that function performs).
        repos["conn"].cursor().execute(
            "UPDATE browser_capture_queue SET status = 'pending'"
            " WHERE user_id = %s AND url = %s",
            (user_id, url),
        )
        repos["conn"].commit()

        queue_repo.mark_done_by_urls(user_id, [url])
        repos["conn"].commit()

        rows = [r for r in queue_repo.get_all(user_id) if r["url"] == url]

    assert rows[0]["status"] == "done"


def test_find_latest_for_url_never_returns_another_users_row(pg_dsn: str) -> None:
    owner = _new_user(pg_dsn, "origin-owner@example.com")
    other = _new_user(pg_dsn, "origin-other@example.com")
    _enqueue_email_link(owner)

    with get_repos(other) as repos:
        set_current_user(repos["conn"].cursor(), other)
        found = repos["browser_capture_queue"].find_latest_for_url(other, _EMAIL_LINK)

    assert found is None


def test_describe_capture_origin_for_a_queued_email_link(pg_dsn: str) -> None:
    user_id = _new_user(pg_dsn, "origin-describe@example.com")
    _enqueue_email_link(user_id)

    origin = _describe_capture_origin(
        user_id,
        {"url": "https://www.welcometothejungle.com/en/jobs/abc", "queue_url": _EMAIL_LINK},
        "listener",
    )

    assert origin["channel"] == "Browser extension — capture queue"
    assert origin["page_url"] == "https://www.welcometothejungle.com/en/jobs/abc"
    assert origin["queue_reason"] == "Email scan"
    assert origin["queue_detail"] == 'WTTJ <alerts@wttj.example> — "5 new jobs"'
    assert origin["queued_at"]


def test_describe_capture_origin_for_a_manual_capture(pg_dsn: str) -> None:
    user_id = _new_user(pg_dsn, "origin-manual@example.com")

    origin = _describe_capture_origin(
        user_id, {"url": "https://jobs.example.com/search"}, "listener"
    )

    assert origin == {
        "channel": "Browser extension — manual capture",
        "page_url": "https://jobs.example.com/search",
        "queue_reason": "",
        "queue_detail": "",
        "queued_at": "",
    }
