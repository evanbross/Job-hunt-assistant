# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-17
"""A job whose JD is only a tracking link gets a browser-extension capture queued
by every repair path, not just the email scan, against the real schema with
Gmail, the AI parser and the scorer faked.

Found 2026-11-17 on Wellfound digest jobs (8190): the per-email rescan and the
broken-import rescan recovered the posting URL but never queued the JD capture;
the import repair queued one but rolled it back unless the job had another fix
to commit. The per-email rescan also re-added the email's own skipped rows as new
skipped "duplicates" on every run (8184 <- 8198 <- 8204 <- 8210).
"""

from __future__ import annotations

from typing import Any

import pytest
from flask import Flask

import config
from backend.db import open_repositories
from backend.db.row_level_security import set_current_user
from routes import emails
from routes import jobs as jobs_route
from tests.db_roles import runtime_database_url
from tests.login_session import sign_in

_POSTING_URL = "https://wellfound.com/jobs/5040873-software-engineer-5-tv-ads"
_TRACKING_BODY = "https://links.wellfound.com/s/c/rzCJEFMgWGB3lJKPO5zEyg7vNJlCeQ4Dz"
_TRACKING_WARNINGS = '["jd_body_is_tracking_link"]'
_SENDER = "Wellfound <team@hi.wellfound.com>"
_SUBJECT = "New jobs for you"


class _Gmail:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def get_message(self, message_id: str) -> dict[str, str]:
        return {"id": message_id, "internalDate": "1778342400000"}

    def extract_text(self, full_msg: dict[str, str]) -> tuple[str, str, str]:
        return _SUBJECT, _SENDER, _TRACKING_BODY

    def extract_raw_html(self, full_msg: dict[str, str]) -> str:
        return ""


class _Parser:
    jobs: list[dict[str, object]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def parse(self, subject: str, sender: str, body: str, raw_html: str = "") -> list[dict[str, object]]:
        return [dict(job) for job in self.jobs]


class _NoScore:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def score(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("low-certainty jobs are not scored")


class _Score:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def score(self, **kwargs: Any) -> dict[str, Any]:
        return {"score": 80, "reasoning": "fits", "salary_min": None, "salary_max": None}


class _FetchesJd:
    text = "A real job description. " * 30  # over the rescan's 400-char floor

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def scan_single_job_url(self, company: str, url: str) -> dict[str, str]:
        return {"job_text": self.text}


def _parsed(company: str = "Netflix", title: str = "Software Engineer 5 - TV (Ads)") -> dict[str, object]:
    return {
        "company": company,
        "job_title": title,
        "url": _POSTING_URL,
        "source": "Wellfound",
        "certainty_level": "low",
        "certainty_reason": "digest card",
        "certainty_score": 50,
    }


@pytest.fixture
def user_id(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> int:
    repos = open_repositories(pg_dsn)
    try:
        account_id = int(
            repos["user_accounts"].create_pending("jd-capture@example.com", "seed", "2099-01-01")["id"]
        )
        repos["conn"].commit()
    finally:
        repos["conn"].close()
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    for module in (emails, jobs_route):
        monkeypatch.setattr(module, "GmailClient", _Gmail)
        monkeypatch.setattr(module, "EmailParser", _Parser)
        monkeypatch.setattr(module, "FitScorer", _NoScore)
    return account_id


def _insert_job(pg_dsn: str, user_id: int, **fields: object) -> int:
    row: dict[str, object] = {
        "company": "Netflix",
        "job_title": "Software Engineer 5 - TV (Ads)",
        "source": "Wellfound",
        "status": "triage",
        "date_found": "2026-11-17",
        "raw_jd_snippet": _TRACKING_BODY,
        "certainty_level": "low",
    }
    row.update(fields)
    repos = open_repositories(pg_dsn)
    try:
        set_current_user(repos["conn"].cursor(), user_id)
        job_id = int(repos["jobs"].insert(user_id, row))
        repos["conn"].commit()
    finally:
        repos["conn"].close()
    return job_id


def _jobs_and_queue(pg_dsn: str, user_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repos = open_repositories(pg_dsn)
    try:
        cur = repos["conn"].cursor()
        set_current_user(cur, user_id)
        cur.execute(
            "SELECT url, site_name, status, origin, origin_detail FROM browser_capture_queue"
            " WHERE user_id = %s ORDER BY id",
            (user_id,),
        )
        queue = [dict(row) for row in cur.fetchall()]
        return repos["jobs"].get_all_ordered(user_id), queue
    finally:
        repos["conn"].close()


def _rescan_email(user_id: int, email_id: str) -> dict[str, Any]:
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(emails.emails_bp, url_prefix="/api")
    client = app.test_client()
    with client.session_transaction() as sess:
        sign_in(sess, user_id)

    response = client.post(f"/api/emails/{email_id}/rescan")
    assert response.status_code == 200, response.get_json()
    return response.get_json()


# ─── per-email rescan (routes/emails.py) ─────────────────────────────────────


def test_email_rescan_queues_a_capture_for_a_recovered_url_with_a_tracking_link_jd(
    user_id: int, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _insert_job(
        pg_dsn, user_id, dedup_key="msg-1", url="",
        import_warnings='["non_posting_url", "jd_body_is_tracking_link"]',
    )
    monkeypatch.setattr(_Parser, "jobs", [_parsed()])

    result = _rescan_email(user_id, "msg-1")

    jobs, queue = _jobs_and_queue(pg_dsn, user_id)
    [job] = jobs
    assert (job["id"], job["url"], job["import_warnings"]) == (job_id, _POSTING_URL, _TRACKING_WARNINGS)
    assert queue == [
        {
            "url": _POSTING_URL, "site_name": "Wellfound", "status": "pending",
            "origin": "email_scan", "origin_detail": f'{_SENDER} — "{_SUBJECT}"',
        }
    ]
    assert "1 queued for description capture" in result["message"]


def test_email_rescan_leaves_the_emails_own_skipped_row_alone_instead_of_re_adding_it(
    user_id: int, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    skipped_id = _insert_job(
        pg_dsn, user_id, dedup_key="msg-1", url="", status="skipped", skip_reason="duplicate",
        company="Deepgram", job_title="Staff Software Engineer, Console",
        import_warnings='["non_posting_url", "jd_body_is_tracking_link"]',
    )
    monkeypatch.setattr(
        _Parser, "jobs", [_parsed(company="Deepgram", title="Staff Software Engineer, Console")]
    )

    for _ in range(2):
        result = _rescan_email(user_id, "msg-1")
        assert "1 already tracked" in result["message"]

    jobs, queue = _jobs_and_queue(pg_dsn, user_id)
    assert [(job["id"], job["status"], job["url"]) for job in jobs] == [(skipped_id, "skipped", "")]
    assert queue == []


# ─── broken-import rescan (routes/jobs.py) ───────────────────────────────────


def test_broken_import_rescan_queues_a_capture_for_a_recovered_url(
    user_id: int, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _insert_job(
        pg_dsn, user_id, dedup_key="msg-1", url="",
        import_warnings='["non_posting_url", "jd_body_is_tracking_link"]',
    )
    monkeypatch.setattr(_Parser, "jobs", [_parsed()])

    state = jobs_route.run_rescan_broken_imports_task(user_id)

    assert state["error"] is None
    assert [j["id"] for j in state["jd_queued"]] == [job_id]
    jobs, queue = _jobs_and_queue(pg_dsn, user_id)
    assert [job["url"] for job in jobs] == [_POSTING_URL]
    assert [(q["url"], q["origin"], q["origin_detail"]) for q in queue] == [
        (_POSTING_URL, "import_repair", f"Job #{job_id}")
    ]


def test_broken_import_rescan_saves_a_fetched_jd_as_the_description(
    user_id: int, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fetched JD used to land only in raw_jd_snippet, so the job kept the
    tracking-link warning and still showed "No description loaded"."""
    _insert_job(
        pg_dsn, user_id, dedup_key="msg-1", url="", certainty_level="high",
        import_warnings='["non_posting_url", "jd_body_is_tracking_link"]',
    )
    monkeypatch.setattr(_Parser, "jobs", [_parsed()])
    monkeypatch.setattr(jobs_route, "CareerSiteScanner", _FetchesJd)
    monkeypatch.setattr(jobs_route, "FitScorer", _Score)

    state = jobs_route.run_rescan_broken_imports_task(user_id)

    assert state["error"] is None
    assert state["jd_queued"] == []
    jobs, queue = _jobs_and_queue(pg_dsn, user_id)
    [job] = jobs
    assert job["job_description"] == _FetchesJd.text
    assert "jd_body_is_tracking_link" not in str(job["import_warnings"] or "")
    assert job["fit_score"] == 80
    assert queue == []


# ─── import repair (routes/jobs.py) ──────────────────────────────────────────


def test_import_repair_commits_the_capture_when_the_jd_is_the_only_defect(
    user_id: int, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_repos() rolls back on exit; the repair committed only when it had
    other fixes to write, so this job's queued capture silently vanished."""
    job_id = _insert_job(
        pg_dsn, user_id, dedup_key="msg-1", url=_POSTING_URL, import_warnings=_TRACKING_WARNINGS
    )
    monkeypatch.setattr(jobs_route, "_write_repair_findings_doc", lambda *a, **k: None)

    state = jobs_route.run_import_repair_task(user_id)

    assert state["error"] is None
    assert [j["id"] for j in state["jd_queued"]] == [job_id]
    assert state["unresolved"] == []
    _, queue = _jobs_and_queue(pg_dsn, user_id)
    assert [(q["url"], q["origin"], q["origin_detail"]) for q in queue] == [
        (_POSTING_URL, "import_repair", f"Job #{job_id}")
    ]


def test_import_repair_reports_a_tracking_link_jd_with_no_url_as_unresolved(
    user_id: int, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _insert_job(pg_dsn, user_id, dedup_key="msg-1", url="", import_warnings=_TRACKING_WARNINGS)
    monkeypatch.setattr(jobs_route, "_write_repair_findings_doc", lambda *a, **k: None)

    state = jobs_route.run_import_repair_task(user_id)

    assert state["jd_queued"] == []
    assert [(j["id"], "jd_body_is_tracking_link" in j["warnings"]) for j in state["unresolved"]] == [
        (job_id, True)
    ]
    _, queue = _jobs_and_queue(pg_dsn, user_id)
    assert queue == []


def test_import_repair_does_not_queue_a_capture_for_a_skipped_job(
    user_id: int, pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _insert_job(
        pg_dsn, user_id, dedup_key="msg-1", url=_POSTING_URL, status="skipped",
        skip_reason="duplicate", import_warnings=_TRACKING_WARNINGS,
    )
    monkeypatch.setattr(jobs_route, "_write_repair_findings_doc", lambda *a, **k: None)

    state = jobs_route.run_import_repair_task(user_id)

    assert state["jd_queued"] == []
    assert state["unresolved"] == []
    _, queue = _jobs_and_queue(pg_dsn, user_id)
    assert queue == []
