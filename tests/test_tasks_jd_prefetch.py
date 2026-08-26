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

from __future__ import annotations

import pytest

from backend.tasks import _safe_scan_single_job_url

_TEST_JOB = {
    "site_name": "",
    "site_url": "https://job-boards.greenhouse.io/acme/jobs/1",
    "job_title": "Senior Engineer",
    "job_url": "https://job-boards.greenhouse.io/acme/jobs/1",
    "job_text": "Full job description text.",
    "posted_at": None,
    "confidence_level": "high",
    "confidence_score": 90,
}


class _StubScanner:
    def __init__(self, *, job: dict | None = None, exc: Exception | None = None) -> None:
        self._job = job
        self._exc = exc
        self.calls: list[tuple[str, str]] = []

    def scan_single_job_url(self, site_name: str, job_url: str) -> dict:
        self.calls.append((site_name, job_url))
        if self._exc is not None:
            raise self._exc
        assert self._job is not None
        return dict(self._job)


# ai: claude-sonnet-5 | 2026-08-26
def test_safe_scan_single_job_url_returns_fetched_job_on_success() -> None:
    scanner = _StubScanner(job=_TEST_JOB)

    result = _safe_scan_single_job_url(scanner, "https://job-boards.greenhouse.io/acme/jobs/1")

    assert result == _TEST_JOB
    assert scanner.calls == [("", "https://job-boards.greenhouse.io/acme/jobs/1")]


def test_safe_scan_single_job_url_returns_none_on_value_error() -> None:
    scanner = _StubScanner(exc=ValueError("unsupported host"))

    result = _safe_scan_single_job_url(scanner, "https://example.com/jobs/1")

    assert result is None


def test_safe_scan_single_job_url_returns_none_on_os_error() -> None:
    scanner = _StubScanner(exc=OSError("connection reset"))

    result = _safe_scan_single_job_url(scanner, "https://example.com/jobs/1")

    assert result is None


def test_safe_scan_single_job_url_lets_unexpected_errors_propagate() -> None:
    """Only (ValueError, OSError) are treated as expected fetch failures --
    a programmer error should still surface loudly rather than silently
    becoming a missing prefetch entry."""
    scanner = _StubScanner(exc=KeyError("job_text"))

    with pytest.raises(KeyError):
        _safe_scan_single_job_url(scanner, "https://example.com/jobs/1")
