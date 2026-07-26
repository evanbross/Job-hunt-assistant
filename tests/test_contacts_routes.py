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

from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from typing import Any

import pytest
from flask import Flask

from routes.contacts import contacts_bp

# ai: claude-sonnet-5 | 2026-07-25


def make_fake_get_repos(repos: dict) -> Any:
    @contextmanager
    def fake() -> Iterator[dict[str, Any]]:
        try:
            yield repos
        finally:
            repos["conn"].close()

    return fake


class FakeConnection:
    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


class FakeContactsRepo:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def get_all(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]

    def replace_all(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def count(self) -> int:
        return len(self.rows)


class FakeSettingsRepo:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.values.get(key, default)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value


def _make_app(repos: dict, monkeypatch: pytest.MonkeyPatch) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(contacts_bp, url_prefix="/api")
    monkeypatch.setattr("routes.contacts.get_repos", make_fake_get_repos(repos))
    return app


def test_import_contacts_missing_file_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "contacts": FakeContactsRepo(), "settings": FakeSettingsRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.post("/api/contacts/import")
    assert response.status_code == 400


def test_import_contacts_wrong_extension_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "contacts": FakeContactsRepo(), "settings": FakeSettingsRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.post(
        "/api/contacts/import",
        data={"file": (BytesIO(b"not a csv"), "connections.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_import_contacts_success(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "contacts": FakeContactsRepo(), "settings": FakeSettingsRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    csv_bytes = (
        b"First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
        b"Jane,Doe,https://linkedin.com/in/janedoe,,Acme Inc,Engineer,15 Jan 2024\n"
    )
    response = client.post(
        "/api/contacts/import",
        data={"file": (BytesIO(csv_bytes), "Connections.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["imported"] == 1
    assert data["skipped"] == 0
    assert data["imported_at"]
    assert repos["settings"].get("contacts_last_imported_at") == data["imported_at"]


def test_get_contacts_status(monkeypatch: pytest.MonkeyPatch) -> None:
    settings_repo = FakeSettingsRepo()
    settings_repo.set("contacts_last_imported_at", "2026-07-25T12:00:00+00:00")
    repos = {
        "conn": FakeConnection(),
        "contacts": FakeContactsRepo([{"id": 1}, {"id": 2}]),
        "settings": settings_repo,
    }
    client = _make_app(repos, monkeypatch).test_client()

    response = client.get("/api/contacts/status")
    assert response.status_code == 200
    assert response.get_json() == {
        "count": 2,
        "last_imported_at": "2026-07-25T12:00:00+00:00",
    }


def test_get_contacts_company_map_groups_by_normalized_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = {
        "conn": FakeConnection(),
        "contacts": FakeContactsRepo(
            [
                {
                    "id": 1,
                    "full_name": "Jane Doe",
                    "company_normalized": "acme",
                    "position": "Engineer",
                    "profile_url": "https://linkedin.com/in/janedoe",
                    "connected_on": "2024-01-15",
                },
                {
                    "id": 2,
                    "full_name": "No Company",
                    "company_normalized": "",
                    "position": "",
                    "profile_url": None,
                    "connected_on": None,
                },
            ]
        ),
        "settings": FakeSettingsRepo(),
    }
    client = _make_app(repos, monkeypatch).test_client()

    response = client.get("/api/contacts/company-map")
    assert response.status_code == 200
    data = response.get_json()
    assert list(data.keys()) == ["acme"]
    assert data["acme"][0]["full_name"] == "Jane Doe"
