# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-11-27
"""Regression coverage for routes/scan.py's email-scan JD-capture fallback.

Found live (job 8755, 2026-11-27): a Getro digest email provided only a
click-tracking link and newsletter navigation text, not a real job
description. The direct fetch (CareerSiteScanner.scan_single_job_url)
already ran and got too little content, but the old fallback only queued a
browser-extension capture when the *stored snippet itself* was a bare
tracking link (job_validator.py's TRACKING_LINK_JD_WARNING via
is_email_tracking_url) -- the Getro snippet was prose, not a URL, so that
check never matched and no capture was ever queued. The job was left with
no real JD, silently, forever (short of a manual "Refresh from URL" click).

The fix broadens the fallback to whatever the direct fetch itself already
knows: did it get a usable JD or not, regardless of why not.
"""

from __future__ import annotations

import pytest

import config
from backend.db import open_repositories
from backend.db.row_level_security import set_current_user
from backend.settings_service import set_setting
from routes import scan
from tests.db_roles import runtime_database_url

_TRACKING_URL = "https://url3473.getro.com/ls/click?upn=u001.abc123"
_DIGEST_BODY = (
    "Insight Partners job board\n\n3 new jobs matching your preferences\n\n"
    f"Senior Backend Engineer at Seeq\n{_TRACKING_URL}"
)
_SENDER = "Insight Partners via Getro <hello@getro.com>"
_SUBJECT = "New jobs for you in Insight Partners' job board"


class FakeGmailClient:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def list_unread_messages(
        self, lookback_days: int, enabled_sources: list[dict], *, unread_only: bool = True
    ) -> list[dict[str, str]]:
        return [{"id": "getro-msg-1"}]

    def get_message(self, message_id: str) -> dict[str, str]:
        return {"id": message_id, "internalDate": "1778342400000"}

    def extract_text(self, full_msg: dict[str, str]) -> tuple[str, str, str]:
        return _SUBJECT, _SENDER, _DIGEST_BODY

    def extract_raw_html(self, full_msg: dict[str, str]) -> str:
        return ""


class FakeEmailParser:
    """What EmailParser.parse() returns for this digest: a title/company/url,
    no inline JD -- the tracking link is unresolved (getro.com is a known
    tracking domain per email_parser.py, but the fake destination here still
    requires the browser-extension fallback either way)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def parse(self, subject: str, sender: str, body: str, raw_html: str = "") -> list[dict[str, object]]:
        return [
            {
                "company": "Seeq",
                "job_title": "Senior Backend Engineer",
                "url": _TRACKING_URL,
                "source": "Getro",
                "certainty_level": "high",
                "certainty_reason": "clear sender, title, and url",
                "certainty_score": 90,
            }
        ]


class FetchesTooLittle:
    """Simulates a JS-rendered destination page: the direct fetch gets
    something, but under the 400-char floor routes/scan.py requires."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def scan_single_job_url(self, company: str, url: str) -> dict[str, str]:
        return {"job_text": "Loading…"}


class FetchesARealJd:
    text = "A real job description. " * 30  # over the 400-char floor

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def scan_single_job_url(self, company: str, url: str) -> dict[str, str]:
        return {"job_text": self.text}


def _seed_account(dsn: str) -> int:
    repos = open_repositories(dsn)
    try:
        account = repos["user_accounts"].create_pending(
            "owner@example.com", "seed-owner@example.com", "2099-01-01"
        )
        user_id = account["id"]
        set_current_user(repos["conn"].cursor(), user_id)
        repos["sources"].insert(
            user_id,
            {
                "name": "Getro",
                "match_type": "sender_email",
                "match_value": "hello@getro.com",
                "enabled": 1,
            },
        )
        set_setting("email_lookback_days", "7", user_id, repos)
        set_setting("move_successfully_processed_emails", "false", user_id, repos)
        set_setting("scanned_email_label", "Scanned", user_id, repos)
        repos["conn"].commit()
    finally:
        repos["conn"].close()
    return user_id


def _run_scan(monkeypatch: pytest.MonkeyPatch, dsn: str, user_id: int, scanner: type) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(dsn))
    monkeypatch.setattr(scan, "GmailClient", FakeGmailClient)
    monkeypatch.setattr(scan, "EmailParser", FakeEmailParser)
    monkeypatch.setattr(scan, "CareerSiteScanner", scanner)
    scan.run_scan_task.apply(args=(user_id,))


def _jobs_and_queue(dsn: str, user_id: int) -> tuple[list[dict], list[dict]]:
    repos = open_repositories(dsn)
    try:
        set_current_user(repos["conn"].cursor(), user_id)
        jobs = repos["jobs"].get_all_ordered(user_id)
        queue = repos["browser_capture_queue"].get_all(user_id)
        return jobs, queue
    finally:
        repos["conn"].close()


def test_a_digest_with_no_real_jd_queues_a_browser_capture(
    monkeypatch: pytest.MonkeyPatch, pg_dsn: str
) -> None:
    user_id = _seed_account(pg_dsn)
    _run_scan(monkeypatch, pg_dsn, user_id, FetchesTooLittle)

    jobs, queue = _jobs_and_queue(pg_dsn, user_id)
    assert len(jobs) == 1
    job = jobs[0]
    assert not job["job_description"]
    [queued] = queue
    assert queued["url"] == _TRACKING_URL
    assert queued["origin"] == "email_scan"


def test_a_digest_whose_direct_fetch_succeeds_queues_nothing(
    monkeypatch: pytest.MonkeyPatch, pg_dsn: str
) -> None:
    user_id = _seed_account(pg_dsn)
    _run_scan(monkeypatch, pg_dsn, user_id, FetchesARealJd)

    jobs, queue = _jobs_and_queue(pg_dsn, user_id)
    assert len(jobs) == 1
    assert jobs[0]["job_description"] == FetchesARealJd.text
    assert queue == []


# ai: claude-sonnet-5 | 2026-11-27
# A Getro digest only ever teases a few jobs inline (the user's report: "the
# Getro email also has a button to see more jobs than just the 3"). Registering
# the board itself as a recurring automatic source lets the existing generic
# scanner -- already built to handle VC-portfolio boards like this one, see
# career_site_scanner.py's _scan_generic -- pick up the rest on its own schedule.
_BOARD_URL = "https://jobs.insightpartners.com/jobs"


def test_a_getro_digest_registers_its_board_as_an_automatic_source(
    monkeypatch: pytest.MonkeyPatch, pg_dsn: str
) -> None:
    user_id = _seed_account(pg_dsn)
    monkeypatch.setattr(scan, "discover_getro_board_url", lambda raw_html, body: _BOARD_URL)

    _run_scan(monkeypatch, pg_dsn, user_id, FetchesTooLittle)

    repos = open_repositories(pg_dsn)
    try:
        set_current_user(repos["conn"].cursor(), user_id)
        source = repos["career_sites"].get_by_url(user_id, _BOARD_URL)
    finally:
        repos["conn"].close()
    assert source is not None
    assert source["collection_mode"] == "automatic"
    assert source["enabled"] == 1


def test_a_getro_digest_does_not_duplicate_an_already_registered_board(
    monkeypatch: pytest.MonkeyPatch, pg_dsn: str
) -> None:
    user_id = _seed_account(pg_dsn)
    repos = open_repositories(pg_dsn)
    try:
        set_current_user(repos["conn"].cursor(), user_id)
        repos["career_sites"].insert(
            user_id,
            {
                "name": "Insight Partners",
                "url": _BOARD_URL,
                "enabled": 1,
                "collection_mode": "automatic",
                "recapture_interval_days": 7,
            },
        )
        repos["conn"].commit()
    finally:
        repos["conn"].close()
    monkeypatch.setattr(scan, "discover_getro_board_url", lambda raw_html, body: _BOARD_URL)

    _run_scan(monkeypatch, pg_dsn, user_id, FetchesTooLittle)

    repos = open_repositories(pg_dsn)
    try:
        set_current_user(repos["conn"].cursor(), user_id)
        cur = repos["conn"].cursor()
        cur.execute(
            "SELECT COUNT(*) AS n FROM career_site_sources WHERE user_id = %s AND url = %s",
            (user_id, _BOARD_URL),
        )
        count = cur.fetchone()["n"]
    finally:
        repos["conn"].close()
    assert count == 1


def test_a_non_getro_digest_does_not_attempt_board_discovery(
    monkeypatch: pytest.MonkeyPatch, pg_dsn: str
) -> None:
    user_id = _seed_account(pg_dsn)

    class NonGetroEmailParser(FakeEmailParser):
        def parse(self, subject: str, sender: str, body: str, raw_html: str = "") -> list[dict[str, object]]:
            jobs = super().parse(subject, sender, body, raw_html)
            for job in jobs:
                job["source"] = "WellKnownBoard"
            return jobs

    class NonGetroGmailClient(FakeGmailClient):
        def extract_text(self, full_msg: dict[str, str]) -> tuple[str, str, str]:
            return _SUBJECT, "Well Known Board <alerts@wellknownboard.com>", _DIGEST_BODY

    called = False

    def fail_if_called(raw_html: str, body: str) -> str | None:
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    monkeypatch.setattr(scan, "GmailClient", NonGetroGmailClient)
    monkeypatch.setattr(scan, "EmailParser", NonGetroEmailParser)
    monkeypatch.setattr(scan, "CareerSiteScanner", FetchesTooLittle)
    monkeypatch.setattr(scan, "discover_getro_board_url", fail_if_called)
    scan.run_scan_task.apply(args=(user_id,))

    assert called is False
