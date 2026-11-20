# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-15
"""A browser-extension capture of a job already tracked is a sighting of it
(backend/tasks.py's run_import_task), against the real schema -- except the
extension re-capturing a queued URL for the app's own JD fetch, which says
nothing about where the job turned up."""

from __future__ import annotations

from typing import Any

import pytest

import config
from backend import tasks
from backend.db import open_repositories
from backend.db.row_level_security import set_current_user
from tests.db_roles import runtime_database_url

_URL = "https://job-boards.greenhouse.io/acme/jobs/7777777"
_STORED_JD = "Full stored description. " * 40


class _EmptyFetch:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def scan_single_job_url(self, site_name: str, url: str) -> dict[str, str]:
        return {"job_text": ""}


@pytest.fixture
def tracked(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    repos = open_repositories(pg_dsn)
    try:
        user_id = int(repos["user_accounts"].create_pending("import-sightings@example.com", "seed", "2099-01-01")["id"])
        set_current_user(repos["conn"].cursor(), user_id)
        job_id = repos["jobs"].insert(
            user_id,
            {
                "dedup_key": "tracked", "company": "Acme", "job_title": "Staff Engineer", "url": _URL,
                "source": "Greenhouse", "status": "pending_review", "date_found": "2026-11-01",
                "job_description": _STORED_JD,
            },
        )
        repos["conn"].commit()
    finally:
        repos["conn"].close()
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    monkeypatch.setattr(tasks, "CareerSiteScanner", _EmptyFetch)
    return user_id, job_id


def _capture() -> list[dict[str, Any]]:
    return [
        {
            "url": _URL,
            "platform": "Greenhouse",
            "company": "Acme",
            "job_title": "Staff Engineer",
            "listing_text": "Staff Engineer at Acme",
        }
    ]


def _sightings(pg_dsn: str, user_id: int, job_id: int) -> list[dict[str, Any]]:
    repos = open_repositories(pg_dsn)
    try:
        set_current_user(repos["conn"].cursor(), user_id)
        return repos["job_sightings"].list_for_job(user_id, job_id)
    finally:
        repos["conn"].close()


def test_capture_of_a_tracked_job_records_a_sighting(tracked: tuple[int, int], pg_dsn: str) -> None:
    user_id, job_id = tracked
    result = tasks.run_import_task.apply(
        args=(user_id, _capture(), {"name": "Acme careers", "url": "https://acme.example/careers"}, {"import_trigger": "listener"})
    ).get()

    assert result["skipped"] == 1 and result["queued"] == 0
    [sighting] = _sightings(pg_dsn, user_id, job_id)
    assert (sighting["source"], sighting["channel"], sighting["capture_label"], sighting["url"]) == (
        "Browser Extension - Greenhouse", "extension", "Acme careers", _URL,
    )


def test_queued_recapture_records_no_sighting(tracked: tuple[int, int], pg_dsn: str) -> None:
    user_id, job_id = tracked
    tasks.run_import_task.apply(
        args=(user_id, _capture(), None, {"import_trigger": "listener", "queue_url": _URL})
    ).get()

    assert _sightings(pg_dsn, user_id, job_id) == []


# ai: claude-opus-5-5 | 2026-11-19
def test_capture_that_fills_a_blank_company_queues_a_certainty_rejudge(
    tracked: tuple[int, int],
    pg_dsn: str,
    queued_certainty_rejudges: list[tuple[int, int]],
) -> None:
    """repair_existing_job_company fills the company from the capture; the job's
    certainty_reason, written while it was blank, must be re-judged (job 8335)."""
    user_id, job_id = tracked
    repos = open_repositories(pg_dsn)
    try:
        set_current_user(repos["conn"].cursor(), user_id)
        repos["jobs"].update(user_id, job_id, {"company": ""})
        repos["conn"].commit()
    finally:
        repos["conn"].close()

    result = tasks.run_import_task.apply(
        args=(user_id, _capture(), None, {"import_trigger": "listener"})
    ).get()

    assert result["repaired"] == 1
    assert queued_certainty_rejudges == [(user_id, job_id)]


# ai: claude-opus-5-5 | 2026-11-19
def test_capture_that_repairs_nothing_queues_no_certainty_rejudge(
    tracked: tuple[int, int], queued_certainty_rejudges: list[tuple[int, int]]
) -> None:
    user_id, _job_id = tracked

    tasks.run_import_task.apply(args=(user_id, _capture(), None, {"import_trigger": "listener"})).get()

    assert queued_certainty_rejudges == []
