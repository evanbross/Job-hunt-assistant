# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# LICENSE: Personal Use Only
# This source code is made available for personal, non-commercial use only.
# You may read, study, and run this code for personal purposes.
# You may NOT:
#   - Use this code, in whole or in part, in any commercial product or service
#   - Sell, sublicense, or otherwise monetize this code or derivatives of it
#   - Redistribute this code without explicit written permission from ZombieTech LLC
#
# No warranty is provided, express or implied. Use at your own risk.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-fable-5 | 2026-07-16
"""Tests for backend.utils.expand_board_url_parsed_jobs.

Regression coverage for the 2026-07-16 scan crash: any greenhouse.io URL in
parsed email jobs raised AttributeError inside is_job_board_url, killing the
whole scan run before this function could apply its fallback behavior.
"""

from unittest.mock import patch

from backend.email.email_parser import ParsedJob
from backend.scanning.board_url_expansion import expand_board_url_parsed_jobs
from backend.scanning.career_site_scanner import CareerSiteJob, ScanOutcome


def _parsed_job(url: str, company: str = "Acme", source: str = "email") -> ParsedJob:
    return ParsedJob(
        company=company,
        job_title="Engineer",
        url=url,
        source=source,
        confidence="high",
        confidence_score=90,
        confidence_reason="test fixture",
    )


def _career_site_job(job_url: str, company: str | None = None) -> CareerSiteJob:
    job = CareerSiteJob(
        site_name="Acme",
        site_url="https://boards.greenhouse.io/acme",
        job_title="Backend Engineer",
        job_url=job_url,
        job_text="Build things.",
        posted_at=None,
        confidence_level="high",
        confidence_score=85,
    )
    if company is not None:
        job["company"] = company
    return job


class _FakeScanner:
    def __init__(
        self, outcome: ScanOutcome | None = None, error: Exception | None = None
    ) -> None:
        self.calls: list[tuple[str, str]] = []
        self._outcome = outcome
        self._error = error

    def scan_site(self, site_name: str, site_url: str) -> ScanOutcome:
        self.calls.append((site_name, site_url))
        if self._error is not None:
            raise self._error
        assert self._outcome is not None
        return self._outcome


def test_non_board_url_passes_through_unchanged() -> None:
    original = _parsed_job("https://www.linkedin.com/jobs/view/999")
    fake = _FakeScanner()
    with patch("backend.scanning.board_url_expansion.CareerSiteScanner", return_value=fake):
        result = expand_board_url_parsed_jobs([original])

    assert result == [original]
    assert fake.calls == []


def test_greenhouse_single_job_url_passes_through_without_crashing() -> None:
    """Regression: this URL previously raised AttributeError at the
    is_job_board_url check, before any fallback could apply."""
    original = _parsed_job("https://job-boards.greenhouse.io/acme/jobs/12345")
    fake = _FakeScanner()
    with patch("backend.scanning.board_url_expansion.CareerSiteScanner", return_value=fake):
        result = expand_board_url_parsed_jobs([original])

    assert result == [original]
    assert fake.calls == []


def test_board_url_expands_into_individual_jobs() -> None:
    board = _parsed_job("https://boards.greenhouse.io/acme")
    fake = _FakeScanner(
        outcome=ScanOutcome(
            jobs=[
                _career_site_job("https://boards.greenhouse.io/acme/jobs/1"),
                _career_site_job(
                    "https://boards.greenhouse.io/acme/jobs/2", company="Acme Labs"
                ),
            ],
            detected_platform="greenhouse",
        )
    )
    with patch("backend.scanning.board_url_expansion.CareerSiteScanner", return_value=fake):
        result = expand_board_url_parsed_jobs([board])

    assert fake.calls == [("Acme", "https://boards.greenhouse.io/acme")]
    assert len(result) == 2
    assert result[0]["url"] == "https://boards.greenhouse.io/acme/jobs/1"
    assert result[0]["company"] == "Acme"  # falls back to the email's company
    assert result[1]["company"] == "Acme Labs"  # scanner-provided company wins
    assert all(job["source"] == "email" for job in result)
    assert all(job["confidence"] == "high" for job in result)


def test_scan_failure_falls_back_to_original_entry() -> None:
    board = _parsed_job("https://boards.greenhouse.io/acme")
    fake = _FakeScanner(error=RuntimeError("network down"))
    with patch("backend.scanning.board_url_expansion.CareerSiteScanner", return_value=fake):
        result = expand_board_url_parsed_jobs([board])

    assert result == [board]


def test_empty_scan_result_falls_back_to_original_entry() -> None:
    board = _parsed_job("https://boards.greenhouse.io/acme")
    fake = _FakeScanner(outcome=ScanOutcome(jobs=[], detected_platform="greenhouse"))
    with patch("backend.scanning.board_url_expansion.CareerSiteScanner", return_value=fake):
        result = expand_board_url_parsed_jobs([board])

    assert result == [board]


def test_blank_and_non_https_urls_pass_through() -> None:
    blank = _parsed_job("")
    plain_http = _parsed_job("http://boards.greenhouse.io/acme")
    fake = _FakeScanner()
    with patch("backend.scanning.board_url_expansion.CareerSiteScanner", return_value=fake):
        result = expand_board_url_parsed_jobs([blank, plain_http])

    assert result == [blank, plain_http]
    assert fake.calls == []
