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

from typing import Any

import pytest

import backend.scanning.job_board_searcher as job_board_searcher_module
from backend.scanning.job_board_searcher import JobBoardSearcher, strip_html, url_to_dedup_key


class FakeResponse:
    def __init__(self, payload: dict[str, Any], *, should_fail: bool = False) -> None:
        self._payload = payload
        self._should_fail = should_fail

    def raise_for_status(self) -> None:
        if self._should_fail:
            raise RuntimeError("request failed")

    def json(self) -> dict[str, Any]:
        return self._payload


# ai: gpt-5 | 2026-07-26
def test_strip_html_decodes_entities_and_normalizes_whitespace() -> None:
    raw = "<p>Senior&nbsp;<strong>Backend</strong></p>\n<div>R&amp;D role</div>"

    assert strip_html(raw) == "Senior Backend R&D role"


# ai: gpt-5 | 2026-07-26
def test_url_to_dedup_key_is_stable_and_url_specific() -> None:
    url = "https://example.com/jobs/123"

    assert url_to_dedup_key(url) == url_to_dedup_key(url)
    assert url_to_dedup_key(url).startswith("board:")
    assert url_to_dedup_key(url) != url_to_dedup_key("https://example.com/jobs/456")


# ai: gpt-5 | 2026-07-26
def test_search_all_normalizes_sources_and_deduplicates_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_get(
        url: str,
        *,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: int,
    ) -> FakeResponse:
        calls.append((url, params))
        assert headers["Accept"] == "application/json"
        assert timeout == 15
        if "remotive.com" in url:
            return FakeResponse(
                {
                    "jobs": [
                        {
                            "title": " Senior Platform Engineer ",
                            "company_name": " Acme ",
                            "url": " https://jobs.example.com/acme/platform ",
                            "description": "<p>Build&nbsp;APIs</p>",
                            "publication_date": "2026-07-20T12:00:00Z",
                        },
                        {
                            "title": "Missing URL",
                            "company_name": "Acme",
                            "url": "",
                            "description": "ignored",
                        },
                    ]
                }
            )
        page = int(params["page"])
        if page == 0:
            return FakeResponse(
                {
                    "results": [
                        {
                            "name": "Senior Platform Engineer",
                            "company": {"name": "Acme"},
                            "refs": {
                                "landing_page": "https://jobs.example.com/acme/platform"
                            },
                            "contents": "<p>Duplicate from Muse</p>",
                            "publication_date": "2026-07-21T08:00:00Z",
                        },
                        {
                            "name": "Staff Backend Engineer",
                            "company": {"name": "Beta"},
                            "refs": {"landing_page": "https://muse.example.com/beta"},
                            "contents": "Ship &amp; operate",
                            "publication_date": "",
                        },
                    ]
                }
            )
        return FakeResponse({"results": []})

    monkeypatch.setattr(job_board_searcher_module.requests, "get", fake_get)
    monkeypatch.setattr(job_board_searcher_module.time, "sleep", lambda _seconds: None)

    results = JobBoardSearcher().search_all(["python"])

    assert results == [
        {
            "title": "Senior Platform Engineer",
            "company": "Acme",
            "url": "https://jobs.example.com/acme/platform",
            "description": "Build APIs",
            "source": "Remotive",
            "date_posted": "2026-07-20",
        },
        {
            "title": "Staff Backend Engineer",
            "company": "Beta",
            "url": "https://muse.example.com/beta",
            "description": "Ship & operate",
            "source": "The Muse",
            "date_posted": None,
        },
    ]
    assert calls == [
        (
            "https://remotive.com/api/remote-jobs",
            {"category": "software-dev", "search": "python", "limit": 50},
        ),
        (
            "https://www.themuse.com/api/public/jobs",
            {"category": "Software Engineering", "level": "Senior Level", "page": 0},
        ),
        (
            "https://www.themuse.com/api/public/jobs",
            {"category": "Software Engineering", "level": "Senior Level", "page": 1},
        ),
    ]


# ai: gpt-5 | 2026-07-26
def test_search_all_keeps_other_sources_when_one_source_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(
        url: str,
        *,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: int,
    ) -> FakeResponse:
        if "remotive.com" in url:
            return FakeResponse({}, should_fail=True)
        return FakeResponse(
            {
                "results": [
                    {
                        "name": "Senior Infra Engineer",
                        "company": {"name": "Delta"},
                        "refs": {"landing_page": "https://muse.example.com/delta"},
                        "contents": "Remote role",
                        "publication_date": "2026-07-22T10:00:00Z",
                    }
                ]
            }
        )

    monkeypatch.setattr(job_board_searcher_module.requests, "get", fake_get)
    monkeypatch.setattr(job_board_searcher_module.time, "sleep", lambda _seconds: None)

    results = JobBoardSearcher().search_all(["python"])

    assert results == [
        {
            "title": "Senior Infra Engineer",
            "company": "Delta",
            "url": "https://muse.example.com/delta",
            "description": "Remote role",
            "source": "The Muse",
            "date_posted": "2026-07-22",
        }
    ]
