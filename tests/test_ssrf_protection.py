# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-fable-5 | 2026-07-15
"""SSRF-protection regression tests for the AI provider client and its
configured endpoints (2026-07-15 code review, findings 1, 2, and 5).

All unsafe destinations use IP literals or localhost, so validation fails
before any DNS lookup or network connection is attempted.
"""

from __future__ import annotations

import pytest
from flask import Flask

import config
from backend.ai_provider import AIProviderClient, AIProviderError
from backend.ollama_endpoint import validate_ollama_base_url
from backend.utils.url_safety import UnsafeUrlError
from routes.settings import settings_bp

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


# ─── Ollama on a hosted server (docs/cloud-deployment-readiness-plan.md 2.4) ────
# ai: claude-opus-5-5 | 2026-11-06 -- loopback is the server itself there, so the
# desktop's exemption doesn't apply: public https only, like every other fetch.


@pytest.fixture
def hosted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "APP_MODE", "hosted")


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:11434",      # the classic local Ollama
        "https://127.0.0.1:8765",      # the extension listener, from inside the container
        "https://10.0.0.5",            # the private network
        "http://8.8.8.8",              # public, but plaintext
        "https://8.8.8.8:11434",       # public https, off the standard port
    ],
)
def test_hosted_refuses_ollama_urls_a_desktop_might_use(hosted: None, base_url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_ollama_base_url(base_url)


def test_hosted_accepts_a_public_https_ollama(hosted: None) -> None:
    validate_ollama_base_url("https://8.8.8.8")


def test_the_desktop_still_reaches_a_local_ollama() -> None:
    validate_ollama_base_url("http://localhost:11434")


def test_hosted_connection_test_cannot_probe_the_servers_own_ports(
    hosted: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It answered differently for an open port and a closed one: a port scanner."""
    connected: list[object] = []
    monkeypatch.setattr("backend.ollama_endpoint.safe_urlopen", lambda *a, **k: connected.append(a))
    app = Flask(__name__)
    app.register_blueprint(settings_bp, url_prefix="/api")

    response = app.test_client().post(
        "/api/config/test-ollama", json={"ollama_base_url": "http://127.0.0.1:5004"}
    )

    assert response.get_json()["ok"] is False
    assert response.get_json()["message"].startswith("Refusing to connect")
    assert connected == []


def test_hosted_settings_save_refuses_a_loopback_ollama_url(hosted: None) -> None:
    app = Flask(__name__)
    app.register_blueprint(settings_bp, url_prefix="/api")

    response = app.test_client().put("/api/config", json={"ollama_base_url": "http://localhost:11434"})

    assert response.status_code == 400
    assert "https" in response.get_json()["error"]
