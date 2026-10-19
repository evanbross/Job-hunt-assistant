# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

import json
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

import pytest

import backend.scanning.job_board_searcher as job_board_searcher_module
from backend.scanning.job_board_searcher import (
    JobBoardSearcher,
    strip_html,
    url_to_dedup_key,
)


class _FakeHeaders:
    def get_content_charset(self) -> str:
        return "utf-8"


class FakeResponse:
    """Stand-in for the addinfourl safe_urlopen() returns (a context manager)."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body

    @property
    def headers(self) -> _FakeHeaders:
        return _FakeHeaders()


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
    calls: list[str] = []

    def fake_safe_urlopen(req: Any, *, timeout: int, **_kwargs: object) -> FakeResponse:
        calls.append(req.full_url)
        assert req.get_header("Accept") == "application/json"
        assert timeout == 15
        if "remotive.com" in req.full_url:
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
        query = parse_qs(urlparse(req.full_url).query)
        page = int(query["page"][0])
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

    monkeypatch.setattr(job_board_searcher_module, "safe_urlopen", fake_safe_urlopen)
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
    assert len(calls) == 3
    assert calls[0].startswith("https://remotive.com/api/remote-jobs?")
    assert parse_qs(urlparse(calls[0]).query) == {
        "category": ["software-dev"],
        "search": ["python"],
        "limit": ["50"],
    }
    assert calls[1].startswith("https://www.themuse.com/api/public/jobs?")
    assert parse_qs(urlparse(calls[1]).query) == {
        "category": ["Software Engineering"],
        "level": ["Senior Level"],
        "page": ["0"],
    }
    assert parse_qs(urlparse(calls[2]).query)["page"] == ["1"]


# ai: gpt-5 | 2026-07-26
def test_search_all_keeps_other_sources_when_one_source_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_safe_urlopen(req: Any, *, timeout: int, **_kwargs: object) -> FakeResponse:
        if "remotive.com" in req.full_url:
            raise URLError("request failed")
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

    monkeypatch.setattr(job_board_searcher_module, "safe_urlopen", fake_safe_urlopen)
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
