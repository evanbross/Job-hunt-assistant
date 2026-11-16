# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-15
"""Job sightings against the real schema: the repository (per-day dedupe), the
jobs repository's savepoint-scoped insert rollback that recording relies on, the
insert-or-sighting fallback, and the routes that read sightings back."""

from __future__ import annotations

from typing import Any

import pytest
from flask import Flask

import config
from backend.db import (
    DuplicateJobUrlError,
    PostgresUserAccountRepository,
    SightingChannel,
    get_connection,
    open_repositories,
)
from backend.db.row_level_security import set_current_user
from backend.scanning.job_sightings import insert_or_record_sighting
from routes.jobs import jobs_bp
from routes.leads import leads_bp
from tests.db_roles import runtime_database_url

_URL = "https://www.linkedin.com/jobs/view/4400000001/"


def _job(dedup_key: str, url: str = _URL, **overrides: Any) -> dict[str, Any]:
    row = {
        "dedup_key": dedup_key,
        "company": "Acme",
        "job_title": "Staff Engineer",
        "url": url,
        "source": "LinkedIn",
        "status": "pending_review",
        "date_found": "2026-11-15",
    }
    row.update(overrides)
    return row


@pytest.fixture
def repos_and_user(pg_dsn: str) -> Any:
    repos = open_repositories(pg_dsn)
    account = repos["user_accounts"].create_pending("sightings@example.com", "seed-sightings", "2099-01-01")
    user_id = int(account["id"])
    set_current_user(repos["conn"].cursor(), user_id)
    repos["conn"].commit()
    try:
        yield repos, user_id
    finally:
        repos["conn"].close()


def test_record_keeps_one_sighting_per_job_source_and_day(repos_and_user: Any) -> None:
    repos, user_id = repos_and_user
    job_id = repos["jobs"].insert(user_id, _job("s-1"))
    sightings = repos["job_sightings"]

    assert sightings.record(
        user_id, job_id, source="LinkedIn", channel=SightingChannel.EMAIL,
        capture_label="Digest A", seen_at="2026-11-14T08:00:00+00:00",
    )
    # Same source, same UTC day: nothing new.
    assert not sightings.record(
        user_id, job_id, source="LinkedIn", channel=SightingChannel.EMAIL,
        capture_label="Digest B", seen_at="2026-11-14T21:00:00+00:00",
    )
    # A different source the same day, and the same source the next day, both count.
    assert sightings.record(
        user_id, job_id, source="Wellfound", channel=SightingChannel.EXTENSION,
        seen_at="2026-11-14T09:00:00+00:00",
    )
    assert sightings.record(
        user_id, job_id, source="LinkedIn", channel=SightingChannel.EMAIL,
        seen_at="2026-11-15T08:00:00+00:00", url=_URL, sender_email="jobs@linkedin.com",
    )
    repos["conn"].commit()

    listed = sightings.list_for_job(user_id, job_id)
    assert [(s["source"], s["seen_at"][:10], s["channel"]) for s in listed] == [
        ("LinkedIn", "2026-11-15", "email"),
        ("Wellfound", "2026-11-14", "extension"),
        ("LinkedIn", "2026-11-14", "email"),
    ]
    assert listed[0]["url"] == _URL and listed[0]["sender_email"] == "jobs@linkedin.com"
    assert listed[2]["capture_label"] == "Digest A"
    assert sightings.counts_by_job(user_id, [job_id, job_id + 999]) == {job_id: 3}
    assert sightings.counts_by_job(user_id, []) == {}


def test_lost_insert_keeps_earlier_uncommitted_writes(repos_and_user: Any) -> None:
    """insert() rolls back only to its own savepoint: a capture path that records
    sightings and then loses an insert race must not lose those sightings."""
    repos, user_id = repos_and_user
    first_id = repos["jobs"].insert(user_id, _job("s-1"))
    repos["conn"].commit()

    repos["job_sightings"].record(user_id, first_id, source="LinkedIn", channel=SightingChannel.EMAIL)
    other_id = repos["jobs"].insert(user_id, _job("s-2", url="https://example.com/jobs/other"))
    with pytest.raises(DuplicateJobUrlError):
        repos["jobs"].insert(user_id, _job("s-3"))
    repos["conn"].commit()

    assert repos["jobs"].get_by_id(user_id, other_id) is not None
    assert len(repos["job_sightings"].list_for_job(user_id, first_id)) == 1


def test_insert_or_record_sighting_records_on_the_job_holding_the_url(repos_and_user: Any) -> None:
    repos, user_id = repos_and_user
    first_id = insert_or_record_sighting(repos, user_id, _job("s-1"), SightingChannel.EMAIL)
    assert first_id is not None

    second = _job("s-2", source="Wellfound", capture_label="Second digest", sender_email="digest@x.test")
    assert insert_or_record_sighting(repos, user_id, second, SightingChannel.EMAIL) is None
    repos["conn"].commit()

    [sighting] = repos["job_sightings"].list_for_job(user_id, first_id)
    assert (sighting["source"], sighting["capture_label"], sighting["sender_email"]) == (
        "Wellfound", "Second digest", "digest@x.test",
    )
    assert len(repos["jobs"].get_all_ordered(user_id)) == 1


@pytest.fixture
def client_and_user(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, int]:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        account = PostgresUserAccountRepository(conn).create_pending(
            "sightings-routes@example.com", "seed-sightings-routes", "2099-01-01"
        )
        conn.commit()
    finally:
        conn.close()
    app = Flask(__name__)
    app.secret_key = "test"
    for blueprint in (jobs_bp, leads_bp):
        app.register_blueprint(blueprint, url_prefix="/api")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = account["id"]
    return client, int(account["id"])


def _seed_job_with_sightings(pg_dsn: str, user_id: int, **job_overrides: Any) -> int:
    repos = open_repositories(pg_dsn)
    try:
        set_current_user(repos["conn"].cursor(), user_id)
        job_id = repos["jobs"].insert(user_id, _job("route-1", **job_overrides))
        for day, source in (("2026-11-13", "LinkedIn"), ("2026-11-14", "Wellfound")):
            repos["job_sightings"].record(
                user_id, job_id, source=source, channel=SightingChannel.EMAIL,
                seen_at=f"{day}T08:00:00+00:00",
            )
        repos["conn"].commit()
        return job_id
    finally:
        repos["conn"].close()


def test_sightings_route_and_queue_count(client_and_user: tuple[Any, int], pg_dsn: str) -> None:
    client, user_id = client_and_user
    job_id = _seed_job_with_sightings(pg_dsn, user_id)

    body = client.get(f"/api/jobs/{job_id}/sightings").get_json()
    assert [s["source"] for s in body["sightings"]] == ["Wellfound", "LinkedIn"]
    assert client.get(f"/api/jobs/{job_id + 999}/sightings").status_code == 404

    page = client.get("/api/jobs/queue?view=queue").get_json()
    counts = {job["id"]: job.get("sighting_count") for job in page["jobs"]}
    assert counts.get(job_id) == 2


def test_lead_detail_includes_sightings(client_and_user: tuple[Any, int], pg_dsn: str) -> None:
    client, user_id = client_and_user
    job_id = _seed_job_with_sightings(pg_dsn, user_id, status="applied", on_sheet=1)

    detail = client.get(f"/api/leads/{job_id}").get_json()
    assert [s["source"] for s in detail["sightings"]] == ["Wellfound", "LinkedIn"]
