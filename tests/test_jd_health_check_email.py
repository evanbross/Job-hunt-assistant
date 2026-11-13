# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""The JD parser health check's failure email (backend/tasks.py).

The 2026-11-06 report: a failing WTTJ canary read like a job in the queue (it isn't
one, so searching the queue found nothing), and the email gave no way to reach the
place where health-check targets are edited."""

from __future__ import annotations

from typing import Any

import pytest

import backend.tasks as tasks
import config
from backend.db import get_connection
from backend.db.repositories.user_accounts import PostgresUserAccountRepository
from backend.settings_service import set_setting
from backend.tasks import jd_health_check_failure_email_body, run_jd_health_check_task
from backend.utils.utils import get_repos

_POSTING_URL = "https://app.welcometothejungle.com/jobs/UmxDK3hO"


def _failure(kind: str) -> dict[str, str]:
    return {
        "label": "Metabase - Software Engineer, Backend (WTTJ, resolves to Lever)",
        "url": _POSTING_URL,
        "reason": "job_text too short (198 chars, need 250)",
        "kind": kind,
    }


# ─── the body ───────────────────────────────────────────────────────────────────


def test_the_body_says_these_are_test_urls_not_queue_jobs_and_where_they_live() -> None:
    body = jd_health_check_failure_email_body([_failure("posting")], "https://jobs.example.com")

    assert "not jobs in your queue" in body
    assert "Settings > Career Sites > JD Parser Health Check" in body
    assert "Manage the targets: https://jobs.example.com/?settings=jd_health_check" in body


def test_a_single_posting_failure_suggests_the_posting_may_have_closed() -> None:
    body = jd_health_check_failure_email_body([_failure("posting")], "")

    assert f"URL: {_POSTING_URL}" in body
    assert "Problem: job_text too short (198 chars, need 250)" in body
    assert "If the posting has closed" in body
    assert "Manage the targets" not in body  # no base URL configured, so no broken link


def test_a_board_failure_points_at_a_format_change() -> None:
    body = jd_health_check_failure_email_body([_failure("board")], "https://jobs.example.com/")

    assert "site's format changed" in body
    assert "https://jobs.example.com/?settings=jd_health_check" in body  # no double slash


# ─── end to end through the task ────────────────────────────────────────────────


class _ShortPostingScanner:
    """Extracts a posting whose text is too short, the reported failure."""

    def scan_single_job_url(self, label: str, url: str) -> dict[str, Any]:
        return {"job_title": "Software Engineer", "company": "Metabase", "job_text": "x" * 198}

    def scan_site(self, label: str, url: str) -> dict[str, Any]:
        raise AssertionError("a single posting must not be scanned as a board")

    def pop_failures(self) -> list[str]:
        return []


class _RecordingGmail:
    sent: list[dict[str, str]] = []

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id

    def send_email(self, to: str, subject: str, body_text: str) -> None:
        _RecordingGmail.sent.append({"to": to, "subject": subject, "body": body_text})


# ai: claude-opus-5-5 | 2026-11-09
def _user_with_one_short_posting_target(pg_dsn: str) -> int:
    conn = get_connection(pg_dsn)
    try:
        user_id = PostgresUserAccountRepository(conn).create_pending(
            "health@example.com", "seed-health", "2099-01-01"
        )["id"]
        conn.commit()
    finally:
        conn.close()
    with get_repos(user_id) as repos:
        repos["jd_health_targets"].insert(
            user_id, {"platform": "wttj", "label": "Metabase (WTTJ)", "url": _POSTING_URL}
        )
        set_setting("jd_health_check_notify_email", "me@example.com", user_id, repos)
        set_setting("app_base_url", "https://jobs.example.com", None, repos)  # system-wide
        repos["conn"].commit()
    return user_id


def test_the_task_emails_the_kind_of_target_and_a_link_to_its_settings(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = _user_with_one_short_posting_target(pg_dsn)
    _RecordingGmail.sent = []
    monkeypatch.setattr(tasks, "CareerSiteScanner", _ShortPostingScanner)
    monkeypatch.setattr(tasks, "GmailClient", _RecordingGmail)

    run_jd_health_check_task.apply(args=(user_id,))

    assert len(_RecordingGmail.sent) == 1
    body = _RecordingGmail.sent[0]["body"]
    assert "not jobs in your queue" in body
    assert "If the posting has closed" in body
    assert "https://jobs.example.com/?settings=jd_health_check" in body


# ai: claude-opus-5-5 | 2026-11-09
def test_hosted_sends_the_failure_email_through_smtp_not_the_users_gmail(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hosted never requests gmail.send (backend/email/gmail_scopes.py), so the
    notification goes through the app's SMTP relay instead -- and only to the
    account's own email, never the stored notify address."""
    user_id = _user_with_one_short_posting_target(pg_dsn)
    smtp_sent: list[dict[str, str]] = []

    def _record_smtp(*, to: str, subject: str, body_text: str) -> None:
        smtp_sent.append({"to": to, "subject": subject, "body": body_text})

    def _no_gmail(user_id: int) -> None:
        raise AssertionError("hosted must not send through the user's Gmail")

    monkeypatch.setattr(config, "APP_MODE", "hosted")
    monkeypatch.setattr(config, "APP_BASE_URL", "https://jobs.example.com")
    monkeypatch.setattr(tasks, "CareerSiteScanner", _ShortPostingScanner)
    monkeypatch.setattr(tasks, "GmailClient", _no_gmail)
    monkeypatch.setattr(tasks.smtp_sender, "send_email", _record_smtp)

    run_jd_health_check_task.apply(args=(user_id,))

    assert [m["to"] for m in smtp_sent] == ["health@example.com"]
    assert smtp_sent[0]["subject"] == "JD parser health check: 1 failure(s)"
    assert "If the posting has closed" in smtp_sent[0]["body"]
    assert "https://jobs.example.com/?settings=jd_health_check" in smtp_sent[0]["body"]
