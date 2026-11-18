# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-18
"""POST /api/setup/test-ai for each provider the setup wizard offers.

The OpenAI case stubs only the network socket (safe_urlopen), so it exercises
the same request-building and transport the real provider uses -- the bug it
guards against was the route depending on an SDK the app never installs."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

import pytest
from flask import Flask

from routes.setup import setup_bp


@pytest.fixture
def client() -> Any:
    app = Flask(__name__)
    app.register_blueprint(setup_bp, url_prefix="/api")
    return app.test_client()


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _record_openai_call(
    monkeypatch: pytest.MonkeyPatch, *, reply: dict[str, Any] | None = None, status: int | None = None
) -> list[urllib.request.Request]:
    sent: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, **_kwargs: object) -> _FakeResponse:
        sent.append(request)
        if status is not None:
            body = io.BytesIO(json.dumps({"error": {"message": "Incorrect API key provided"}}).encode())
            raise urllib.error.HTTPError(request.full_url, status, "Unauthorized", Message(), body)
        return _FakeResponse(json.dumps(reply).encode())

    monkeypatch.setattr("backend.ai_provider.safe_urlopen", fake_urlopen)
    return sent


def test_openai_key_is_checked_with_the_providers_own_responses_request(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _record_openai_call(monkeypatch, reply={"output_text": "hello"})

    response = client.post(
        "/api/setup/test-ai", json={"provider": "openai", "api_key": "sk-test", "model": "gpt-test"}
    )

    assert response.get_json() == {"ok": True, "message": "OpenAI key is valid (model: gpt-test)."}
    assert sent[0].full_url == "https://api.openai.com/v1/responses"
    assert sent[0].get_header("Authorization") == "Bearer sk-test"
    payload = json.loads(sent[0].data)
    assert payload["model"] == "gpt-test"
    assert payload["max_output_tokens"] >= 16


def test_a_rejected_openai_key_is_reported_as_invalid(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_openai_call(monkeypatch, status=401)

    response = client.post("/api/setup/test-ai", json={"provider": "openai", "api_key": "sk-bad"})

    assert response.get_json() == {"ok": False, "message": "Invalid API key — authentication failed."}


def test_openai_requires_a_key(client: Any) -> None:
    response = client.post("/api/setup/test-ai", json={"provider": "openai"})

    assert response.get_json() == {"ok": False, "message": "API key is required"}


def test_anthropic_key_is_checked_with_the_sdk(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, Any]] = []

    class FakeMessages:
        def create(self, **kwargs: Any) -> None:
            created.append(kwargs)

    class FakeAnthropic:
        def __init__(self, api_key: str) -> None:
            self.messages = FakeMessages()

    monkeypatch.setattr("routes.setup.anthropic.Anthropic", FakeAnthropic)

    response = client.post("/api/setup/test-ai", json={"provider": "anthropic", "api_key": "sk-ant"})

    assert response.get_json() == {"ok": True, "message": "Anthropic API key is valid."}
    assert created and created[0]["max_tokens"] == 1


def test_ollama_reports_the_connection_check_result(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str]] = []

    def fake_check(base_url: str, model: str) -> tuple[bool, str]:
        seen.append((base_url, model))
        return True, "Ollama is reachable."

    monkeypatch.setattr("routes.setup.check_ollama_connection", fake_check)

    response = client.post(
        "/api/setup/test-ai",
        json={"provider": "ollama", "base_url": "http://ollama.example:11434/", "model": "llama3"},
    )

    assert response.get_json() == {"ok": True, "message": "Ollama is reachable."}
    assert seen == [("http://ollama.example:11434", "llama3")]
