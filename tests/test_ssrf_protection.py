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

# ai: claude-fable-5 | 2026-07-15
"""SSRF-protection regression tests for the Jobanni scan route/scanner and the
AI provider client (2026-07-15 code review, findings 1, 2, and 5).

All unsafe destinations use IP literals or localhost, so validation fails
before any DNS lookup or network connection is attempted.
"""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

import config
from backend.ai_provider import AIProviderClient, AIProviderError
from backend.scanning.sources.jobanni_scanner import scan as jobanni_scan
from routes.jobanni import JobanniScanRequest, jobanni_bp
from routes.settings import settings_bp


def make_client() -> FlaskClient:
    app = Flask(__name__)
    app.register_blueprint(jobanni_bp, url_prefix="/api")
    return app.test_client()


# ─── Route: unsafe search_url values are rejected with a 400 ─────────────────


@pytest.mark.parametrize(
    "search_url",
    [
        "http://127.0.0.1/jobs",
        "http://localhost/jobs",
        "http://10.0.0.5/jobs",
        "http://192.168.1.1/jobs",
        "http://169.254.169.254/latest/meta-data/",
        "ftp://example.com/jobs",
        "file:///etc/passwd",
        "http://user:pass@example.com/jobs",
    ],
)
def test_scan_route_rejects_unsafe_search_url(search_url: str) -> None:
    client = make_client()
    response = client.post("/api/jobanni/scan", json={"search_url": search_url})
    assert response.status_code == 400
    assert "rejected" in response.get_json()["error"]


def test_scan_route_returns_400_not_500_for_non_numeric_max_jobs() -> None:
    client = make_client()
    response = client.post(
        "/api/jobanni/scan",
        json={"search_url": "https://example.com/jobs", "max_jobs": "abc"},
    )
    assert response.status_code == 400
    assert "max_jobs" in response.get_json()["error"]


def test_scan_route_starts_thread_for_safe_url(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[tuple] = []

    class FakeThread:
        def __init__(self, *args: object, **kwargs: object) -> None:
            started.append((kwargs.get("target"), kwargs.get("args")))

        def start(self) -> None:
            pass

    monkeypatch.setattr("routes.jobanni.validate_outbound_url", lambda url: None)
    monkeypatch.setattr("routes.jobanni.threading.Thread", FakeThread)

    client = make_client()
    response = client.post(
        "/api/jobanni/scan",
        json={"search_url": "https://jobanni.example/jobs", "max_jobs": 25},
    )

    assert response.status_code == 200
    assert response.get_json() == {"status": "started"}
    assert len(started) == 1
    assert started[0][1] == ("https://jobanni.example/jobs", 25)


def test_scan_request_model_clamps_max_jobs() -> None:
    assert JobanniScanRequest.model_validate({"max_jobs": 9999}).max_jobs == 500
    assert JobanniScanRequest.model_validate({"max_jobs": -3}).max_jobs == 1


# ─── Scanner: fetches refuse unsafe destinations before connecting ──────────


@pytest.mark.parametrize(
    "search_url",
    ["http://127.0.0.1/jobs", "http://10.0.0.5/jobs"],
)
def test_jobanni_scan_refuses_unsafe_fetch_target(search_url: str) -> None:
    with pytest.raises(RuntimeError, match="unsafe URL"):
        jobanni_scan(search_url=search_url, max_jobs=1)


# ─── AI provider: _post_json validates its destination ───────────────────────


def test_post_json_rejects_private_destination_without_retrying() -> None:
    with pytest.raises(AIProviderError, match="URL rejected"):
        AIProviderClient._post_json("http://10.1.2.3:11434/api/chat", {}, headers={})


def test_post_json_rejects_private_destination_even_with_allow_loopback() -> None:
    # allow_loopback only exempts 127.0.0.0/8 — other private ranges stay blocked
    with pytest.raises(AIProviderError, match="URL rejected"):
        AIProviderClient._post_json(
            "http://192.168.0.10:11434/api/chat", {}, headers={}, allow_loopback=True
        )


def test_post_json_rejects_non_http_scheme() -> None:
    with pytest.raises(AIProviderError, match="URL rejected"):
        AIProviderClient._post_json("file:///etc/passwd", {}, headers={})


# ─── Settings: unsafe ollama_base_url is rejected at save time ───────────────


@pytest.mark.parametrize(
    "base_url",
    ["http://192.168.0.10:11434", "ftp://example.com", "http://user:pass@example.com"],
)
def test_update_config_rejects_unsafe_ollama_base_url(
    monkeypatch: pytest.MonkeyPatch, base_url: str
) -> None:
    monkeypatch.setattr(config, "load_runtime_config", lambda: {})
    app = Flask(__name__)
    app.register_blueprint(settings_bp, url_prefix="/api")
    test_client = app.test_client()

    response = test_client.put("/api/config", json={"ollama_base_url": base_url})

    assert response.status_code == 400
    assert "ollama_base_url" in response.get_json()["error"]
