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

# ai: claude-sonnet-5 | 2026-09-29
"""routes/import_.py's reset_import and extract_imported_job_urls -- covers
the 2026-09-29 Pydantic validation cleanup. import_and_queue_all_jobs is
deliberately left out of this pass (see routes/import_.py's comment on that
route): it forwards its body straight to queue_import_capture(), which is
also called from a non-Flask listener thread with a raw dict and has a
large, not-fully-enumerable field surface (browser-extension capture
payloads) -- validating it risks silently breaking a legitimate capture."""

from __future__ import annotations

import pytest
from flask import Flask

from routes.import_ import _active_imports, import_bp


def _make_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(import_bp, url_prefix="/api")
    return app


def test_reset_import_clears_one_tracked_import() -> None:
    _active_imports.clear()
    _active_imports["abc"] = object()
    _active_imports["xyz"] = object()

    client = _make_app().test_client()
    response = client.post("/api/import/reset", json={"import_id": "abc"})

    assert response.status_code == 200
    assert "abc" not in _active_imports
    assert "xyz" in _active_imports
    _active_imports.clear()


def test_reset_import_without_id_clears_everything() -> None:
    _active_imports.clear()
    _active_imports["abc"] = object()

    client = _make_app().test_client()
    response = client.post("/api/import/reset", json={})

    assert response.status_code == 200
    assert _active_imports == {}


def test_reset_import_falls_back_to_query_arg() -> None:
    _active_imports.clear()
    _active_imports["abc"] = object()
    _active_imports["xyz"] = object()

    client = _make_app().test_client()
    response = client.post("/api/import/reset?import_id=abc", json={})

    assert response.status_code == 200
    assert "abc" not in _active_imports
    assert "xyz" in _active_imports
    _active_imports.clear()


def test_reset_import_coerces_non_string_import_id_to_string() -> None:
    """Matches the pre-Pydantic route's str(...) coercion exactly -- a
    numeric import_id is stringified, not rejected."""
    _active_imports.clear()
    _active_imports["123"] = object()

    client = _make_app().test_client()
    response = client.post("/api/import/reset", json={"import_id": 123})

    assert response.status_code == 200
    assert "123" not in _active_imports
    _active_imports.clear()


def test_extract_job_urls_returns_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("routes.import_.dump_import_capture", lambda body: None)

    class FakeService:
        def analyze_capture(self, content, content_format, *, base_url):
            assert content == "some html"
            assert content_format == "html"
            assert base_url == "https://example.com/jobs"
            return {"urls": ["https://example.com/jobs/1"]}

    monkeypatch.setattr("routes.import_.CapturedPageImportService", FakeService)

    client = _make_app().test_client()
    response = client.post(
        "/api/import/job-urls",
        json={
            "content": "some html",
            "format": "html",
            "url": "https://example.com/jobs",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {"urls": ["https://example.com/jobs/1"]}


def test_extract_job_urls_defaults_format_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("routes.import_.dump_import_capture", lambda body: None)

    class FakeService:
        def analyze_capture(self, content, content_format, *, base_url):
            assert content_format == "auto"
            assert base_url is None
            return {"urls": []}

    monkeypatch.setattr("routes.import_.CapturedPageImportService", FakeService)

    client = _make_app().test_client()
    response = client.post("/api/import/job-urls", json={"content": "x"})

    assert response.status_code == 200


def test_extract_job_urls_surfaces_analyze_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("routes.import_.dump_import_capture", lambda body: None)

    class FakeService:
        def analyze_capture(self, content, content_format, *, base_url):
            raise ValueError("nothing to extract")

    monkeypatch.setattr("routes.import_.CapturedPageImportService", FakeService)

    client = _make_app().test_client()
    response = client.post("/api/import/job-urls", json={"content": "x"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "nothing to extract"
