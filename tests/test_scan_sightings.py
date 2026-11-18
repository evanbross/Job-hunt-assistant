# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-15
"""Email capture of a posting that's already tracked records a sighting instead of
a second jobs row: the full scan task (routes/scan.py's run_scan_task) and the
single-email rescan route (routes/emails.py), against the real schema, with Gmail
and the AI parser faked."""

from __future__ import annotations

from typing import Any

import pytest
from flask import Flask

import config
from backend.db import open_repositories
from backend.db.row_level_security import set_current_user
from backend.settings_service import set_setting
from routes import emails, scan
from tests.db_roles import runtime_database_url
from tests.login_session import sign_in

_TRACKED_URL = "https://www.linkedin.com/jobs/view/4433333333/"
# The same posting as a digest links it: another tracking URL, same LinkedIn job ID.
_DIGEST_URL = "https://www.linkedin.com/comm/jobs/view/4433333333/?trackingId=zzz&refId=1"
_NEW_URL = "https://jobs.lever.co/newco/1111-2222"
_SENDER = "LinkedIn <jobs-noreply@linkedin.com>"


class _Gmail:
    """Two digest emails; each names whatever _Parser.jobs_by_subject says."""

    messages = [("msg-1", "Digest one"), ("msg-2", "Digest two")]

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def list_unread_messages(self, lookback_days: int, enabled_sources: list[dict], *, unread_only: bool = True) -> list[dict[str, str]]:
        return [{"id": message_id} for message_id, _ in self.messages]

    def get_message(self, message_id: str) -> dict[str, str]:
        return {"id": message_id, "internalDate": "1778342400000"}

    def extract_text(self, full_msg: dict[str, str]) -> tuple[str, str, str]:
        subject = dict(self.messages)[full_msg["id"]]
        return subject, _SENDER, f"{subject} body"

    def extract_raw_html(self, full_msg: dict[str, str]) -> str:
        return ""


class _Parser:
    jobs_by_subject: dict[str, list[dict[str, object]]] = {}

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def parse(self, subject: str, sender: str, body: str, raw_html: str = "") -> list[dict[str, object]]:
        return [dict(job) for job in self.jobs_by_subject.get(subject, [])]


class _NoFetch:
    """An already-tracked posting must not be fetched again."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def scan_single_job_url(self, company: str, url: str) -> dict[str, str]:
        if "4433333333" in url:
            raise AssertionError(f"fetched an already-tracked posting: {url}")
        return {"job_text": ""}


class _NoScore:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def score(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("scored a job during a sightings test")


def _parsed(url: str, company: str = "Acme", title: str = "Staff Engineer") -> dict[str, object]:
    return {
        "company": company,
        "job_title": title,
        "url": url,
        "source": "LinkedIn",
        "certainty_level": "low",  # low: no scoring for new jobs either
        "certainty_reason": "digest card",
        "certainty_score": 50,
    }


@pytest.fixture
def account(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> tuple[int, int]:
    """An account with a LinkedIn email source and one applied job (the tracked posting)."""
    repos = open_repositories(pg_dsn)
    try:
        user_id = int(repos["user_accounts"].create_pending("scan-sightings@example.com", "seed", "2099-01-01")["id"])
        set_current_user(repos["conn"].cursor(), user_id)
        repos["sources"].insert(
            user_id,
            {"name": "LinkedIn", "match_type": "sender_email", "match_value": "jobs-noreply@linkedin.com", "enabled": 1},
        )
        set_setting("email_lookback_days", "7", user_id, repos)
        set_setting("move_successfully_processed_emails", "false", user_id, repos)
        set_setting("scanned_email_label", "Scanned", user_id, repos)
        tracked_id = repos["jobs"].insert(
            user_id,
            {
                "dedup_key": "tracked-1", "company": "Acme", "job_title": "Staff Engineer", "url": _TRACKED_URL,
                "source": "LinkedIn", "status": "applied", "on_sheet": 1, "date_found": "2026-11-01",
            },
        )
        repos["conn"].commit()
    finally:
        repos["conn"].close()
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    for module in (scan, emails):
        monkeypatch.setattr(module, "GmailClient", _Gmail)
        monkeypatch.setattr(module, "EmailParser", _Parser)
        monkeypatch.setattr(module, "FitScorer", _NoScore)
    monkeypatch.setattr(scan, "CareerSiteScanner", _NoFetch)
    return user_id, tracked_id


def _state(pg_dsn: str, user_id: int, job_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repos = open_repositories(pg_dsn)
    try:
        set_current_user(repos["conn"].cursor(), user_id)
        return repos["jobs"].get_all_ordered(user_id), repos["job_sightings"].list_for_job(user_id, job_id)
    finally:
        repos["conn"].close()


def test_scan_records_a_sighting_for_a_tracked_posting_instead_of_a_row(
    account: tuple[int, int], pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id, tracked_id = account
    monkeypatch.setattr(_Gmail, "messages", [("msg-1", "Digest one")])
    monkeypatch.setattr(_Parser, "jobs_by_subject", {"Digest one": [_parsed(_DIGEST_URL)]})

    result = scan.run_scan_task.apply(args=(user_id,)).get()

    jobs, sightings = _state(pg_dsn, user_id, tracked_id)
    assert [job["id"] for job in jobs] == [tracked_id]
    [sighting] = sightings
    assert (sighting["source"], sighting["channel"], sighting["url"], sighting["capture_label"]) == (
        "LinkedIn", "email", _DIGEST_URL, "Digest one",
    )
    assert sighting["sender_email"] == _SENDER
    assert result["summary"]["already_tracked_jobs"] == 1
    assert result["summary"]["auto_skipped_duplicate_jobs"] == 0


def test_scan_keeps_one_row_when_a_new_job_arrives_in_two_emails(
    account: tuple[int, int], pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """known_jobs is loaded once per scan; the second email must still see the job
    the first one inserted (before 2026-11-15 both were inserted, e.g. the Pepper
    and Splash Sports pairs found on 2026-10-14)."""
    user_id, _ = account
    monkeypatch.setattr(
        _Parser,
        "jobs_by_subject",
        {
            "Digest one": [_parsed(_NEW_URL, company="NewCo")],
            "Digest two": [_parsed(_NEW_URL, company="NewCo")],
        },
    )

    scan.run_scan_task.apply(args=(user_id,))

    jobs, _ = _state(pg_dsn, user_id, 0)
    [new_job] = [job for job in jobs if job["url"] == _NEW_URL]
    _, sightings = _state(pg_dsn, user_id, new_job["id"])
    assert [(s["capture_label"], s["channel"]) for s in sightings] == [("Digest two", "email")]


def test_rescan_turns_an_old_duplicate_row_into_a_sighting(
    account: tuple[int, int], pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row an email made before 2026-11-15 for a posting tracked elsewhere is
    deleted by a rescan of that email, which records a sighting instead."""
    user_id, tracked_id = account
    repos = open_repositories(pg_dsn)
    try:
        set_current_user(repos["conn"].cursor(), user_id)
        old_row = repos["jobs"].insert(
            user_id,
            {
                "dedup_key": "msg-1", "company": "Acme", "job_title": "Staff Engineer", "url": _DIGEST_URL,
                "source": "LinkedIn", "status": "possible_duplicate", "duplicate_of_id": tracked_id,
                "date_found": "2026-11-02",
            },
        )
        repos["conn"].commit()
    finally:
        repos["conn"].close()
    monkeypatch.setattr(_Parser, "jobs_by_subject", {"Digest one": [_parsed(_DIGEST_URL)]})

    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(emails.emails_bp, url_prefix="/api")
    client = app.test_client()
    with client.session_transaction() as sess:
        sign_in(sess, user_id)

    response = client.post("/api/emails/msg-1/rescan")

    assert response.status_code == 200, response.get_json()
    assert "1 already tracked" in response.get_json()["message"]
    jobs, sightings = _state(pg_dsn, user_id, tracked_id)
    assert [job["id"] for job in jobs] == [tracked_id]
    assert old_row not in {job["id"] for job in jobs}
    assert [s["capture_label"] for s in sightings] == ["Digest one"]
