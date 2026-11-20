# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-20
"""The "jobs" push signal: every jobs write announces itself once its
transaction commits (backend/db/connection.py's after_commit +
backend/user_events.py's announce_jobs_changed_after_commit), so an open Job
Queue re-reads a job that was scored, repaired or captured behind it --
job 8411 sat "not scored" with "Review Import" until a manual reload."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from backend.db import (
    PostgresCompanyAliasRepository,
    PostgresJobRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user
from backend.user_events import UserEventTopic
from tests.conftest import RecordingUserEvents

_FUTURE = "2099-01-01"


@pytest.fixture
def db(pg_dsn: str) -> Iterator[tuple[Any, PostgresJobRepository, int]]:
    conn = get_connection(pg_dsn)
    try:
        account = PostgresUserAccountRepository(conn).create_pending(
            "jobs-events@example.com", "seed-jobs-events@example.com", _FUTURE
        )
        set_current_user(conn.cursor(), account["id"])
        conn.commit()
        yield conn, PostgresJobRepository(conn), account["id"]
    finally:
        conn.close()


def _insert(repo: PostgresJobRepository, user_id: int, **fields: Any) -> int:
    fields.setdefault("company", "Acme")
    fields.setdefault("job_title", "Engineer")
    fields.setdefault("date_found", "2026-11-20")
    fields.setdefault("status", "pending_review")
    return repo.insert(user_id, fields)


def _jobs_signals(events: RecordingUserEvents, user_id: int) -> int:
    return events.published.count((user_id, UserEventTopic.JOBS))


def test_after_commit_runs_each_key_once_on_commit_and_never_after_a_rollback(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        calls: list[str] = []
        conn.after_commit("a", lambda: calls.append("a"))
        conn.after_commit("a", lambda: calls.append("a again"))
        conn.after_commit("b", lambda: calls.append("b"))
        assert calls == []
        conn.commit()
        assert calls == ["a", "b"]

        conn.commit()  # nothing pending any more
        assert calls == ["a", "b"]

        conn.after_commit("c", lambda: calls.append("c"))
        conn.rollback()
        conn.commit()
        assert calls == ["a", "b"]
    finally:
        conn.close()


def test_closing_drops_what_was_never_committed(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    calls: list[str] = []
    conn.after_commit("a", lambda: calls.append("a"))
    conn.close()
    assert calls == []


def test_a_jobs_write_signals_once_its_transaction_commits(
    db: tuple[Any, PostgresJobRepository, int], isolated_user_events: RecordingUserEvents
) -> None:
    conn, repo, user_id = db
    job_id = _insert(repo, user_id)
    repo.update(user_id, job_id, {"fit_score": 80})
    assert _jobs_signals(isolated_user_events, user_id) == 0  # not visible to the page yet

    conn.commit()
    assert _jobs_signals(isolated_user_events, user_id) == 1  # one per transaction, not per write


def test_a_rolled_back_jobs_write_never_signals(
    db: tuple[Any, PostgresJobRepository, int], isolated_user_events: RecordingUserEvents
) -> None:
    conn, repo, user_id = db
    _insert(repo, user_id)
    conn.rollback()
    conn.commit()
    assert _jobs_signals(isolated_user_events, user_id) == 0


@pytest.mark.parametrize(
    "write",
    [
        lambda repo, user_id, job_id: repo.update(user_id, job_id, {"fit_score": 55}),
        lambda repo, user_id, job_id: repo.update_if_status_not_in(user_id, job_id, {"fit_score": 55}, ["applied"]),
        lambda repo, user_id, job_id: repo.update_if_status_in(user_id, job_id, {"fit_score": 55}, ["pending_review"]),
        lambda repo, user_id, job_id: repo.update_if_company_missing(user_id, job_id, {"company": "Found Co"}),
        lambda repo, user_id, job_id: repo.update_certainty_if_identity_unchanged(
            user_id,
            job_id,
            {"company": "", "job_title": "Engineer", "url": None},
            {"certainty_level": "high", "certainty_score": 90, "certainty_reason": "ok"},
        ),
        lambda repo, user_id, job_id: repo.delete(user_id, job_id),
    ],
    ids=["update", "status_not_in", "status_in", "company_missing", "certainty", "delete"],
)
def test_every_kind_of_jobs_write_signals(
    db: tuple[Any, PostgresJobRepository, int],
    isolated_user_events: RecordingUserEvents,
    write: Any,
) -> None:
    conn, repo, user_id = db
    job_id = _insert(repo, user_id, company="")
    conn.commit()
    before = _jobs_signals(isolated_user_events, user_id)

    write(repo, user_id, job_id)
    conn.commit()
    assert _jobs_signals(isolated_user_events, user_id) == before + 1


def test_a_guarded_write_that_did_not_apply_does_not_signal(
    db: tuple[Any, PostgresJobRepository, int], isolated_user_events: RecordingUserEvents
) -> None:
    conn, repo, user_id = db
    job_id = _insert(repo, user_id, status="applied")
    conn.commit()
    before = _jobs_signals(isolated_user_events, user_id)

    assert repo.update_if_status_not_in(user_id, job_id, {"fit_score": 10}, ["applied"]) is None
    conn.commit()
    assert _jobs_signals(isolated_user_events, user_id) == before


def test_a_company_alias_that_regroups_jobs_signals(
    db: tuple[Any, PostgresJobRepository, int], isolated_user_events: RecordingUserEvents
) -> None:
    conn, repo, user_id = db
    _insert(repo, user_id, company="Block")
    conn.commit()
    before = _jobs_signals(isolated_user_events, user_id)

    alias = PostgresCompanyAliasRepository(conn).upsert(user_id, alias_company="Block", canonical_company="Cash App")
    conn.commit()
    assert _jobs_signals(isolated_user_events, user_id) == before + 1

    PostgresCompanyAliasRepository(conn).delete(user_id, alias["id"])
    conn.commit()
    assert _jobs_signals(isolated_user_events, user_id) == before + 2
