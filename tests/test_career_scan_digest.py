# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

import config
from backend.email import notification_recipient
from backend.scanning import career_scan_digest


# ai: gpt-5 | 2026-10-04
def test_digest_reports_per_board_counts_reasons_and_parse_failures() -> None:
    body = career_scan_digest.format_career_scan_digest(
        {
            "last_run_at": "2026-10-04T03:15:00+00:00",
            "jobs_found": 12,
            "listings_filtered": 3,
            "errors": [],
            "site_reports": [
                {
                    "site_name": "Acme",
                    "site_url": "https://acme.example/jobs",
                    "success": True,
                    "postings": 10,
                    "new": 4,
                    "duplicates": 3,
                    "ignored": 3,
                    "ignored_reasons": {"Blocked title: intern": 2, "Previously skipped": 1},
                    "parse_failures": ["[detail] ValueError: missing title"],
                    "error": None,
                },
                {
                    "site_name": "Broken Board",
                    "site_url": "https://broken.example/jobs",
                    "success": False,
                    "postings": 2,
                    "new": 0,
                    "duplicates": 0,
                    "ignored": 0,
                    "ignored_reasons": {},
                    "parse_failures": [],
                    "error": "TimeoutError: timed out",
                },
            ],
        }
    )

    assert "Boards: 2 total, 1 succeeded, 1 failed" in body
    assert "New postings: 4" in body
    assert "Duplicates: 3" in body
    assert "Ignored: 3" in body
    assert "Blocked title: intern: 2" in body
    assert "[detail] ValueError: missing title" in body
    assert "Broken Board — FAILED" in body


# ai: gpt-5 | 2026-10-04
def test_digest_uses_account_email_when_no_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, str] = {}

    @contextmanager
    def fake_get_repos(user_id: int):
        assert user_id == 7
        yield {
            "settings": object(),
            "user_accounts": FakeAccountsRepository(),
            "career_scan_digest_log": FakeDigestLogRepository(),
            "conn": FakeConnection(),
        }

    def fake_get_setting(
        key: str, user_id: int | None, repos: dict[str, Any]
    ) -> str:
        assert key == "career_scan_digest_notify_email"
        return ""

    monkeypatch.setattr(career_scan_digest, "get_repos", fake_get_repos)
    monkeypatch.setattr(notification_recipient, "get_setting", fake_get_setting)
    monkeypatch.setattr(career_scan_digest, "get_setting_bool", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        career_scan_digest,
        "send_email",
        lambda **kwargs: sent.update(kwargs),
    )

    career_scan_digest.send_career_scan_digest(
        7,
        {
            "last_run_at": "2026-10-04T03:15:00+00:00",
            "jobs_found": 0,
            "listings_filtered": 0,
            "site_reports": [],
        },
    )

    assert sent["to"] == "owner@example.com"
    assert sent["subject"] == "Job-board scan report — 2026-10-04"


# ai: claude-opus-5-5 | 2026-11-09
@pytest.mark.parametrize(
    ("app_mode", "expected_to"),
    [("desktop", "someone-else@example.com"), ("hosted", "owner@example.com")],
)
def test_hosted_sends_the_digest_only_to_the_accounts_own_email(
    monkeypatch: pytest.MonkeyPatch, app_mode: str, expected_to: str
) -> None:
    """Hosted, the app's SMTP relay sends the digest, so a stored override address
    (e.g. one saved on desktop before moving) must not be able to redirect it."""
    sent: dict[str, str] = {}

    @contextmanager
    def fake_get_repos(user_id: int):
        yield {
            "user_accounts": FakeAccountsRepository(),
            "career_scan_digest_log": FakeDigestLogRepository(),
            "conn": FakeConnection(),
        }

    monkeypatch.setattr(config, "APP_MODE", app_mode)
    monkeypatch.setattr(career_scan_digest, "get_repos", fake_get_repos)
    monkeypatch.setattr(career_scan_digest, "get_setting_bool", lambda *a, **k: True)
    monkeypatch.setattr(notification_recipient, "get_setting", lambda *a, **k: "someone-else@example.com")
    monkeypatch.setattr(career_scan_digest, "send_email", lambda **kwargs: sent.update(kwargs))

    career_scan_digest.send_career_scan_digest(
        7, {"last_run_at": "2026-11-09T03:15:00+00:00", "site_reports": []}
    )

    assert sent["to"] == expected_to


# ai: gpt-5 | 2026-10-04
class FakeAccountsRepository:
    def get(self, user_id: int) -> dict[str, str]:
        assert user_id == 7
        return {"email": "owner@example.com"}


# ai: gpt-5 | 2026-10-04
class FakeConnection:
    def commit(self) -> None:
        pass


# ai: claude-sonnet-5 | 2026-10-05
# ai: claude-sonnet-5 | 2026-11-26 -- added mark_delivered() to match the real
# repository's claim()/mark_delivered() split (2026-11-26 full review, Medium
# finding: claim() alone used to mark a date "sent" before send_email() ran).
class FakeDigestLogRepository:
    """Models career_scan_digest_log's real claim()/mark_delivered() split:
    the first claim for a given (user_id, report_date) wins, every later one
    loses -- these tests don't exercise the real repository's bounded retry
    after a failed send, only that a date already claimed or delivered is
    never claimed again."""

    def __init__(self) -> None:
        self.claimed: set[tuple[int, str]] = set()
        self.delivered: set[tuple[int, str]] = set()

    def claim(self, user_id: int, report_date: str) -> bool:
        key = (user_id, report_date)
        if key in self.claimed:
            return False
        self.claimed.add(key)
        return True

    def mark_delivered(self, user_id: int, report_date: str) -> None:
        self.delivered.add((user_id, report_date))


# ai: claude-sonnet-5 | 2026-10-05
# Regression coverage for the 2026-10-05 review finding "digest
# deduplication is a non-atomic check-send-mark sequence" -- the old
# check-then-send-then-mark let two concurrent scheduled runs both pass the
# check and send duplicate emails for the same day.
def test_digest_is_not_sent_twice_for_the_same_report_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, str]] = []
    digest_log = FakeDigestLogRepository()

    @contextmanager
    def fake_get_repos(user_id: int):
        yield {
            "user_accounts": FakeAccountsRepository(),
            "career_scan_digest_log": digest_log,
            "conn": FakeConnection(),
        }

    monkeypatch.setattr(career_scan_digest, "get_repos", fake_get_repos)
    monkeypatch.setattr(notification_recipient, "get_setting", lambda *a, **k: "")
    monkeypatch.setattr(career_scan_digest, "get_setting_bool", lambda *a, **k: True)
    monkeypatch.setattr(career_scan_digest, "send_email", lambda **kwargs: sent.append(kwargs))

    state = {
        "last_run_at": "2026-10-05T03:15:00+00:00",
        "jobs_found": 0,
        "listings_filtered": 0,
        "site_reports": [],
    }

    # Two scheduled runs for the same day (e.g. an orphaned app process plus
    # a fresh one) both call this -- only the first must actually send.
    career_scan_digest.send_career_scan_digest(7, state)
    career_scan_digest.send_career_scan_digest(7, state)

    assert len(sent) == 1
