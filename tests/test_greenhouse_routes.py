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

"""POST /api/greenhouse/scan -- specifically the 2026-09-29 migration of its
search_url default from config.GREENHOUSE_SEARCH_URL (.env) to the account's
own greenhouse_search_url setting."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from flask import Flask

from routes.greenhouse import greenhouse_bp

# ai: claude-sonnet-5 | 2026-09-29

_TEST_USER_ID = 1


def _make_app(monkeypatch: pytest.MonkeyPatch, *, stored_search_url: str) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(greenhouse_bp, url_prefix="/api")

    @contextmanager
    def fake_get_repos():
        yield {}

    monkeypatch.setattr("routes.greenhouse.current_user_id", lambda: _TEST_USER_ID)
    monkeypatch.setattr("routes.greenhouse.get_repos", fake_get_repos)
    monkeypatch.setattr(
        "routes.greenhouse.get_setting",
        lambda key, user_id, repos: stored_search_url if key == "greenhouse_search_url" else "",
    )
    return app


def test_scan_uses_the_accounts_stored_search_url_when_not_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, str, int]] = []
    monkeypatch.setattr(
        "routes.greenhouse._submit_greenhouse_scan",
        lambda user_id, search_url, max_jobs: calls.append((user_id, search_url, max_jobs))
        or "task-1",
    )
    app = _make_app(monkeypatch, stored_search_url="https://my.greenhouse.io/jobs?query=staff")

    client = app.test_client()
    response = client.post("/api/greenhouse/scan", json={})

    assert response.status_code == 200
    assert calls == [(_TEST_USER_ID, "https://my.greenhouse.io/jobs?query=staff", 50)]


def test_scan_falls_back_to_the_hardcoded_default_when_nothing_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, str, int]] = []
    monkeypatch.setattr(
        "routes.greenhouse._submit_greenhouse_scan",
        lambda user_id, search_url, max_jobs: calls.append((user_id, search_url, max_jobs))
        or "task-1",
    )
    app = _make_app(monkeypatch, stored_search_url="")

    client = app.test_client()
    response = client.post("/api/greenhouse/scan", json={})

    assert response.status_code == 200
    assert calls[0][1] == "https://my.greenhouse.io/jobs"


def test_scan_prefers_an_explicit_search_url_over_the_stored_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, str, int]] = []
    monkeypatch.setattr(
        "routes.greenhouse._submit_greenhouse_scan",
        lambda user_id, search_url, max_jobs: calls.append((user_id, search_url, max_jobs))
        or "task-1",
    )
    app = _make_app(monkeypatch, stored_search_url="https://my.greenhouse.io/jobs?query=staff")

    client = app.test_client()
    response = client.post(
        "/api/greenhouse/scan", json={"search_url": "https://my.greenhouse.io/jobs?query=explicit"}
    )

    assert response.status_code == 200
    assert calls[0][1] == "https://my.greenhouse.io/jobs?query=explicit"
