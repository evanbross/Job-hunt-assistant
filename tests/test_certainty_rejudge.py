# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================
"""Certainty re-judging after a job's company/title/URL is filled in.

Job 8335 (2026-11-19) stored company "Lockheed Martin" with a certainty_reason
saying the company was missing, because the parser never saw the company the
extension captured. These run the re-judge's read/LLM/conditional-write cycle
against real Postgres; only the LLM parser is stubbed.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from kombu.exceptions import OperationalError as BrokerOperationalError

import backend.tasks as tasks_module
from backend.db import (
    PostgresJobRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.job_columns import JobColumns
from backend.db.row_level_security import set_current_user
from backend.email.email_parser import KnownJobFields
from backend.scoring.certainty_rejudge import CertaintyRejudger, rejudge_job_certainty

_STALE_REASON = "Job title, location, and salary are present but the company name is missing or ambiguous."
_FRESH = {
    "company": "Lockheed Martin",
    "job_title": "Full Stack Engineer Stf",
    "url": "",
    "source": "",
    "certainty_level": "high",
    "certainty_score": 90,
    "certainty_reason": "Title, company and URL are all present.",
}


# ai: claude-opus-5-5 | 2026-11-19
class RecordingParser:
    """Stands in for the LLM parser; records what it was shown."""

    def __init__(self, parsed_jobs: list[dict[str, Any]], during_parse: Any = None) -> None:
        self._parsed_jobs = parsed_jobs
        self._during_parse = during_parse
        self.calls: list[tuple[str, KnownJobFields | None]] = []

    def parse(
        self,
        subject: str,
        sender: str,
        body_snippet: str,
        raw_html: str = "",
        *,
        known: KnownJobFields | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append((body_snippet, known))
        if self._during_parse is not None:
            self._during_parse()
        return list(self._parsed_jobs)


# ai: claude-opus-5-5 | 2026-11-19
@pytest.fixture
def stale_job(pg_dsn: str) -> Any:
    conn = get_connection(pg_dsn)
    try:
        account = PostgresUserAccountRepository(conn).create_pending(
            "owner@example.com", "seed-owner@example.com", "2099-01-01"
        )
        user_id = account["id"]
        set_current_user(conn.cursor(), user_id)
        jobs = PostgresJobRepository(conn)
        job_id = jobs.insert(
            user_id,
            {
                "dedup_key": "browser_ext:2026-11-19:rejudge",
                "company": "Lockheed Martin",
                "job_title": "Full Stack Engineer Stf",
                "url": "https://lockheedmartin.eightfold.ai/careers/job/996477730662",
                "source": "Browser Extension - Eightfold",
                "status": "triage",
                "raw_jd_snippet": "Remote. Full Time. ## Basic Qualifications",
                "capture_label": "Browser Extension streamed job listing",
                "certainty_level": "low",
                "certainty_score": 40,
                "certainty_reason": _STALE_REASON,
            },
        )
        conn.commit()
        yield conn, jobs, user_id, job_id
    finally:
        conn.close()


def _certainty(jobs: PostgresJobRepository, user_id: int, job_id: int) -> tuple[Any, Any, Any]:
    row = jobs.get_by_id(user_id, job_id)
    assert row is not None
    return (
        row[JobColumns.CERTAINTY_LEVEL],
        row[JobColumns.CERTAINTY_SCORE],
        row[JobColumns.CERTAINTY_REASON],
    )


def test_rejudge_shows_the_parser_the_job_as_stored_and_saves_its_verdict(stale_job: Any) -> None:
    conn, jobs, user_id, job_id = stale_job
    parser = RecordingParser([_FRESH])

    applied = rejudge_job_certainty(user_id, job_id, CertaintyRejudger(user_id, parser=parser))

    conn.commit()  # end this connection's snapshot so it sees the re-judge's write
    assert applied is True
    assert parser.calls == [
        (
            "Remote. Full Time. ## Basic Qualifications",
            KnownJobFields(
                company="Lockheed Martin",
                job_title="Full Stack Engineer Stf",
                url="https://lockheedmartin.eightfold.ai/careers/job/996477730662",
            ),
        )
    ]
    assert _certainty(jobs, user_id, job_id) == ("high", 90, "Title, company and URL are all present.")


def test_rejudge_is_not_stored_when_a_repair_lands_during_the_llm_call(stale_job: Any) -> None:
    conn, jobs, user_id, job_id = stale_job

    def repair_company_meanwhile() -> None:
        jobs.update(user_id, job_id, {"company": "Lockheed Martin Space"})
        conn.commit()

    parser = RecordingParser([_FRESH], during_parse=repair_company_meanwhile)

    applied = rejudge_job_certainty(user_id, job_id, CertaintyRejudger(user_id, parser=parser))

    conn.commit()
    assert applied is False
    assert _certainty(jobs, user_id, job_id) == ("low", 40, _STALE_REASON)


def test_rejudge_leaves_certainty_alone_when_the_parser_finds_no_posting(stale_job: Any) -> None:
    conn, jobs, user_id, job_id = stale_job

    applied = rejudge_job_certainty(
        user_id, job_id, CertaintyRejudger(user_id, parser=RecordingParser([]))
    )

    conn.commit()
    assert applied is False
    assert _certainty(jobs, user_id, job_id) == ("low", 40, _STALE_REASON)


def test_judge_skips_the_llm_when_the_job_has_no_text() -> None:
    parser = RecordingParser([_FRESH])

    judgment = CertaintyRejudger(1, parser=parser).judge(
        {"company": "Acme", "job_title": "Engineer", "raw_jd_snippet": "  ", "job_description": None}
    )

    assert judgment is None
    assert parser.calls == []


def test_queue_certainty_rejudge_survives_a_broker_outage(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The repair that queued it already committed; a down broker must not
    abort the rest of the caller's repair loop."""

    def broker_down(*_args: Any, **_kwargs: Any) -> None:
        raise BrokerOperationalError("Error 10061 connecting to localhost:6379")

    monkeypatch.setattr(tasks_module.rejudge_job_certainty_task, "delay", broker_down)

    with caplog.at_level(logging.WARNING, logger=tasks_module.__name__):
        tasks_module.queue_certainty_rejudge(1, 8335)

    assert "Could not queue certainty re-judge for job 8335" in caplog.text


def _load_backfill_script() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "rejudge_stale_certainty.py"
    spec = importlib.util.spec_from_file_location("rejudge_stale_certainty", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        ({"company": "Lockheed Martin", "certainty_reason": _STALE_REASON}, True),
        ({"company": "", "certainty_reason": _STALE_REASON}, False),
        (
            {"url": "https://acme.example/jobs/1", "certainty_reason": "No apply URL was provided."},
            True,
        ),
        ({"company": "Acme", "certainty_reason": "Title, company and URL are all present."}, False),
    ],
    ids=["company-claimed-missing", "company-really-missing", "url-claimed-missing", "consistent"],
)
def test_backfill_selects_reasons_that_contradict_the_jobs_own_fields(
    job: dict[str, Any], expected: bool
) -> None:
    assert _load_backfill_script().contradicts_own_fields(job) is expected
