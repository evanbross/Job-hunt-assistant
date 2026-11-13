# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-fable-5 | 2026-07-13
"""Regression tests for /cover-letter/save when the configured folder is
unavailable (e.g. saved on a drive letter that no longer exists). The route
must flag the failure with folder_error so the frontend can prompt for a
replacement folder, and must never persist a folder that was not written to."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

import config
import routes.cover_letter as cover_letter_module
from routes.cover_letter import cover_letter_bp


class FakeConnection:
    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


_TEST_USER_ID = 1


class FakeSettingsRepo:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, user_id: int, key: str) -> str | None:
        return self.values.get(key)

    def set(self, user_id: int, key: str, value: str) -> None:
        self.values[key] = value


def make_fake_get_repos(repos: dict[str, Any]) -> Any:
    @contextmanager
    def fake() -> Iterator[dict[str, Any]]:
        try:
            yield repos
        finally:
            repos["conn"].close()

    return fake


@pytest.fixture
def settings_repo() -> FakeSettingsRepo:
    return FakeSettingsRepo()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, settings_repo: FakeSettingsRepo) -> Any:
    repos = {"settings": settings_repo, "conn": FakeConnection()}
    monkeypatch.setattr(cover_letter_module, "get_repos", make_fake_get_repos(repos))
    monkeypatch.setattr(cover_letter_module, "current_user_id", lambda: _TEST_USER_ID)

    app = Flask(__name__)
    app.register_blueprint(cover_letter_bp, url_prefix="/api")
    return app.test_client()


def unavailable_folder(tmp_path: Path) -> str:
    """A path whose mkdir always fails: a child of a regular file."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    return str(blocker / "Cover Letters")


def test_unavailable_saved_folder_returns_folder_error_flag(
    client: Any, settings_repo: FakeSettingsRepo, tmp_path: Path
) -> None:
    bad_folder = unavailable_folder(tmp_path)
    settings_repo.values["cover_letter_folder"] = bad_folder

    res = client.post(
        "/api/cover-letter/save",
        json={"text": "Dear hiring manager", "job_title": "Engineer", "format": "text"},
    )

    assert res.status_code == 500
    data = res.get_json()
    assert data["folder_error"] is True
    assert data["folder"] == bad_folder
    assert "error" in data


def test_bad_folder_override_is_not_persisted(
    client: Any, settings_repo: FakeSettingsRepo, tmp_path: Path
) -> None:
    settings_repo.values["cover_letter_folder"] = str(tmp_path / "good")

    res = client.post(
        "/api/cover-letter/save",
        json={
            "text": "Dear hiring manager",
            "job_title": "Engineer",
            "format": "text",
            "folder": unavailable_folder(tmp_path),
        },
    )

    assert res.status_code == 500
    assert res.get_json()["folder_error"] is True
    assert settings_repo.values["cover_letter_folder"] == str(tmp_path / "good")


def test_successful_save_writes_file_and_persists_override(
    client: Any, settings_repo: FakeSettingsRepo, tmp_path: Path
) -> None:
    new_folder = tmp_path / "letters"

    res = client.post(
        "/api/cover-letter/save",
        json={
            "text": "Dear hiring manager",
            "job_title": "Engineer",
            "company": "Acme",
            "format": "text",
            "folder": str(new_folder),
        },
    )

    assert res.status_code == 200
    data = res.get_json()
    saved = Path(data["path"])
    assert saved.parent == new_folder
    assert saved.read_text(encoding="utf-8") == "Dear hiring manager"
    assert settings_repo.values["cover_letter_folder"] == str(new_folder)


# ─── hosted mode (docs/cloud-deployment-readiness-plan.md 2.2) ─────────────────
# ai: claude-opus-5-5 | 2026-11-06 -- a hosted server sends the document to the
# browser; it never writes into a folder, least of all one named in the request.


@pytest.fixture
def hosted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "APP_MODE", "hosted")


_SAVE = {"text": "Dear team,\nHello.", "job_title": "Engineer", "company": "Acme", "label": "Cover Letter"}


@pytest.mark.parametrize(
    ("file_format", "signature", "extension"),
    [("docx", b"PK", "docx"), ("pdf", b"%PDF", "pdf"), ("text", b"Dear team,", "txt"), ("md", b"Dear team,", "md")],
)
def test_hosted_save_is_a_download_of_the_document(
    client: Any, hosted: None, file_format: str, signature: bytes, extension: str
) -> None:
    response = client.post("/api/cover-letter/save", json={**_SAVE, "format": file_format})

    assert response.status_code == 200
    disposition = response.headers["Content-Disposition"]
    assert disposition.startswith("attachment")
    assert f"Acme - Engineer Cover Letter.{extension}" in disposition
    assert response.data.startswith(signature)


def test_hosted_save_ignores_a_folder_in_the_request(
    client: Any, hosted: None, settings_repo: FakeSettingsRepo, tmp_path: Path
) -> None:
    """On a server a client-chosen folder would be a write anywhere the process can."""
    target = tmp_path / "anywhere"

    response = client.post(
        "/api/cover-letter/save", json={**_SAVE, "format": "text", "folder": str(target)}
    )

    assert response.status_code == 200
    assert not target.exists()
    assert "cover_letter_folder" not in settings_repo.values


def test_the_desktop_saves_the_same_document_into_the_folder(client: Any, tmp_path: Path) -> None:
    folder = tmp_path / "Letters"

    response = client.post(
        "/api/cover-letter/save", json={**_SAVE, "format": "pdf", "folder": str(folder)}
    )

    assert response.status_code == 200
    assert (folder / "Acme - Engineer Cover Letter.pdf").read_bytes().startswith(b"%PDF")
