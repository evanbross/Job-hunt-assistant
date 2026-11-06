# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
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

from backend.ai_provider import AIProviderClient, AIProviderError
from backend.scanning.sources.jobanni_scanner import scan as jobanni_scan
from routes.jobanni import JobanniScanRequest, jobanni_bp, run_jobanni_scan_task
from routes.settings import settings_bp
from tests.task_doubles import RecordedDelays


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


# ai: claude-opus-5-5 | 2026-11-06 -- the scan is a Celery task now, not a thread.
def test_scan_route_starts_a_scan_for_a_safe_url(monkeypatch: pytest.MonkeyPatch) -> None:
    delays = RecordedDelays()
    monkeypatch.setattr("routes.jobanni.validate_outbound_url", lambda url: None)
    monkeypatch.setattr("routes.jobanni.run_jobanni_scan_task.delay", delays)
    monkeypatch.setattr("routes.jobanni.current_user_id", lambda: 1)

    client = make_client()
    response = client.post(
        "/api/jobanni/scan",
        json={"search_url": "https://jobanni.example/jobs", "max_jobs": 25},
    )

    assert response.status_code == 200
    assert response.get_json() == {"status": "started"}
    assert [args for args, _kwargs in delays.calls] == [(1, "https://jobanni.example/jobs", 25)]


# ai: claude-sonnet-5 | 2026-09-29
def test_scan_route_falls_back_to_the_accounts_configured_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    delays = RecordedDelays()

    @contextmanager
    def fake_get_repos():
        yield {}

    monkeypatch.setattr("routes.jobanni.validate_outbound_url", lambda url: None)
    monkeypatch.setattr("routes.jobanni.run_jobanni_scan_task.delay", delays)
    monkeypatch.setattr("routes.jobanni.current_user_id", lambda: 1)
    monkeypatch.setattr("routes.jobanni.get_repos", fake_get_repos)
    monkeypatch.setattr(
        "routes.jobanni.get_setting",
        lambda key, user_id, repos: "https://jobanni.example/default" if key == "jobanni_search_url" else "",
    )
    monkeypatch.setattr(
        "routes.jobanni.get_setting_int", lambda key, user_id, repos, default: 77
    )

    client = make_client()
    response = client.post("/api/jobanni/scan", json={})

    assert response.status_code == 200
    assert delays.calls[0][0] == (1, "https://jobanni.example/default", 77)


def test_scan_route_returns_400_when_no_search_url_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    @contextmanager
    def fake_get_repos():
        yield {}

    monkeypatch.setattr("routes.jobanni.current_user_id", lambda: 1)
    monkeypatch.setattr("routes.jobanni.get_repos", fake_get_repos)
    monkeypatch.setattr("routes.jobanni.get_setting", lambda key, user_id, repos: "")
    monkeypatch.setattr(
        "routes.jobanni.get_setting_int", lambda key, user_id, repos, default: default
    )

    client = make_client()
    response = client.post("/api/jobanni/scan", json={})

    assert response.status_code == 400


def test_scan_request_model_clamps_max_jobs() -> None:
    # ai: claude-sonnet-5 | 2026-09-29 -- 0 is the "not provided, use the
    # account's configured default" sentinel (start_scan() fills it in from
    # jobanni_max_jobs), not a value to clamp up to 1 here.
    assert JobanniScanRequest.model_validate({"max_jobs": 9999}).max_jobs == 500
    assert JobanniScanRequest.model_validate({"max_jobs": -3}).max_jobs == 0
    assert JobanniScanRequest.model_validate({"max_jobs": 0}).max_jobs == 0
    assert JobanniScanRequest.model_validate({"max_jobs": 25}).max_jobs == 25


# ai: claude-sonnet-5 | 2026-09-28
def test_run_scan_hands_off_to_import_task_with_the_right_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_scan previously called run_import_task.delay(jobs, None, body) --
    missing user_id and the required body arg entirely (2026-09-28 code
    review). Runs the real task body, so it would have caught that regression."""

    calls: list[tuple] = []
    monkeypatch.setattr("routes.jobanni.jobanni_scan", lambda **kwargs: [{"id": "job-1"}])
    monkeypatch.setattr(
        "routes.jobanni.run_import_task.delay",
        lambda *args: calls.append(args) or type("T", (), {"id": "task-123"})(),
    )

    run_jobanni_scan_task(7, "https://jobanni.example/jobs", 10)  # ai: claude-opus-5-5 | 2026-11-06

    assert calls == [(7, [{"id": "job-1"}], None, {"import_trigger": "jobanni_scan"})]


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
def test_update_config_rejects_unsafe_ollama_base_url(base_url: str) -> None:
    # ai: claude-sonnet-5 | 2026-08-30 — no config.load_runtime_config to
    # monkeypatch anymore: ollama_base_url is a per-user setting now, and
    # this route reads its current value via a session peek that safely
    # returns "no known value" (so the safety check below still runs) when
    # there's no session at all, as here.
    app = Flask(__name__)
    app.register_blueprint(settings_bp, url_prefix="/api")
    test_client = app.test_client()

    response = test_client.put("/api/config", json={"ollama_base_url": base_url})

    assert response.status_code == 400
    assert "ollama_base_url" in response.get_json()["error"]
