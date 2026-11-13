# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-09-29
"""POST /browser-capture-queue -- EnqueueUrlRequest's Pydantic conversion
(2026-09-29 Pydantic validation cleanup)."""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from flask import Flask

from backend.db import CaptureQueueOrigin
from routes.browser_capture import browser_capture_bp

_TEST_USER_ID = 1


def _make_app(monkeypatch: pytest.MonkeyPatch, enqueue_calls: list[tuple]) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(browser_capture_bp, url_prefix="/api")

    class FakeQueueRepo:
        def enqueue(
            self,
            user_id: int,
            url: str,
            site_name: str,
            *,
            origin: CaptureQueueOrigin,
            origin_detail: str = "",
        ) -> int:
            assert origin == CaptureQueueOrigin.MANUAL  # ai: claude-opus-5-5 | 2026-10-14
            enqueue_calls.append((user_id, url, site_name))
            return 42

    class FakeConnection:
        def commit(self) -> None:
            pass

    @contextmanager
    def fake_get_repos():
        yield {"browser_capture_queue": FakeQueueRepo(), "conn": FakeConnection()}

    monkeypatch.setattr("routes.browser_capture.current_user_id", lambda: _TEST_USER_ID)
    monkeypatch.setattr("routes.browser_capture.get_repos", fake_get_repos)
    return app


def test_enqueue_url_uses_explicit_site_name(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    app = _make_app(monkeypatch, calls)
    client = app.test_client()

    response = client.post(
        "/api/browser-capture-queue",
        json={"url": "https://example.com/jobs/1", "site_name": "Example Co"},
    )

    assert response.status_code == 201
    assert calls == [(_TEST_USER_ID, "https://example.com/jobs/1", "Example Co")]


def test_enqueue_url_derives_site_name_from_netloc(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    app = _make_app(monkeypatch, calls)
    client = app.test_client()

    response = client.post(
        "/api/browser-capture-queue", json={"url": "https://example.com/jobs/1"}
    )

    assert response.status_code == 201
    assert calls == [(_TEST_USER_ID, "https://example.com/jobs/1", "example.com")]


def test_enqueue_url_rejects_missing_url(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch, [])
    client = app.test_client()

    response = client.post("/api/browser-capture-queue", json={})

    assert response.status_code == 400
    assert response.get_json()["error"] == "url is required"


def test_enqueue_url_rejects_non_http_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _make_app(monkeypatch, [])
    client = app.test_client()

    response = client.post(
        "/api/browser-capture-queue", json={"url": "ftp://example.com/jobs/1"}
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "url must be a valid http/https URL"


def test_enqueue_url_allows_loopback_and_private_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No SSRF check applies here -- these URLs are opened by the browser
    extension client-side, never fetched by the server (see EnqueueUrlRequest's
    docstring)."""
    calls: list[tuple] = []
    app = _make_app(monkeypatch, calls)
    client = app.test_client()

    response = client.post(
        "/api/browser-capture-queue", json={"url": "http://127.0.0.1:8000/jobs/1"}
    )

    assert response.status_code == 201
    assert calls == [(_TEST_USER_ID, "http://127.0.0.1:8000/jobs/1", "127.0.0.1:8000")]
