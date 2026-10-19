# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from flask import Flask

from routes.extension_tokens import extension_tokens_bp

# ai: claude-sonnet-5 | 2026-09-26

_USER_ID = 1


class FakeConnection:
    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


class FakeTokenRepo:
    """In-memory stand-in for the token repository (the real one is covered
    against Postgres in test_extension_tokens.py)."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def create(
        self,
        user_id: int,
        token_hash: str,
        token_prefix: str,
        label: str,
        created_at: str,
        expires_at: str | None,
    ) -> dict[str, Any]:
        row = {
            "id": len(self.rows) + 1,
            "user_id": user_id,
            "token_prefix": token_prefix,
            "label": label,
            "created_at": created_at,
            "last_used_at": None,
            "expires_at": expires_at,
            "revoked_at": None,
        }
        self.rows.append({**row, "_hash": token_hash})
        return row

    def list_for_user(self, user_id: int) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in row.items() if k != "_hash"}
            for row in self.rows
            if row["user_id"] == user_id
        ]

    def revoke(self, user_id: int, token_id: int, revoked_at: str) -> bool:
        for row in self.rows:
            if row["id"] == token_id and row["user_id"] == user_id and row["revoked_at"] is None:
                row["revoked_at"] = revoked_at
                return True
        return False


@pytest.fixture
def client_and_repo(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, FakeTokenRepo]]:
    repo = FakeTokenRepo()
    repos = {"conn": FakeConnection(), "extension_tokens": repo}

    @contextmanager
    def fake_get_repos() -> Iterator[dict[str, Any]]:
        yield repos

    app = Flask(__name__)
    app.register_blueprint(extension_tokens_bp, url_prefix="/api")
    monkeypatch.setattr("routes.extension_tokens.get_repos", fake_get_repos)
    monkeypatch.setattr("routes.extension_tokens.current_user_id", lambda: _USER_ID)
    yield app.test_client(), repo


def test_create_returns_the_plaintext_once_and_never_the_hash(client_and_repo: Any) -> None:
    client, repo = client_and_repo

    response = client.post("/api/extension-tokens", json={"label": "  Brave on desktop  "})

    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    data = response.get_json()
    assert data["token"].startswith("jha_")
    assert data["record"]["label"] == "Brave on desktop"
    assert "token_hash" not in data["record"] and "_hash" not in data["record"]
    assert data["token"] not in repo.rows[0].values()


def test_listing_never_includes_the_plaintext(client_and_repo: Any) -> None:
    client, _ = client_and_repo
    created = client.post("/api/extension-tokens", json={"label": "laptop"}).get_json()

    listed = client.get("/api/extension-tokens")

    assert listed.status_code == 200
    assert created["token"] not in listed.get_data(as_text=True)
    assert [t["label"] for t in listed.get_json()["tokens"]] == ["laptop"]


def test_expiry_days_sets_an_expiry_timestamp(client_and_repo: Any) -> None:
    client, _ = client_and_repo

    data = client.post("/api/extension-tokens", json={"label": "a", "expires_in_days": 30}).get_json()

    assert data["record"]["expires_at"] is not None
    assert data["record"]["expires_at"].endswith("+00:00")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"label": ""},
        {"label": "   "},
        {"label": "x" * 101},
        {"label": 5},
        {"label": "ok", "expires_in_days": 0},
        {"label": "ok", "expires_in_days": -1},
        {"label": "ok", "expires_in_days": 99999},
        {"label": "ok", "expires_in_days": "30"},
        {"label": "ok", "user_id": 2},
    ],
)
def test_create_rejects_invalid_bodies(client_and_repo: Any, body: dict[str, Any]) -> None:
    client, repo = client_and_repo

    response = client.post("/api/extension-tokens", json=body)

    assert response.status_code == 400
    assert repo.rows == []


def test_create_is_capped_per_user(client_and_repo: Any) -> None:
    client, _ = client_and_repo
    for index in range(20):
        assert client.post("/api/extension-tokens", json={"label": f"t{index}"}).status_code == 201

    over_limit = client.post("/api/extension-tokens", json={"label": "one too many"})
    assert over_limit.status_code == 409

    first_id = client.get("/api/extension-tokens").get_json()["tokens"][-1]["id"]
    assert client.delete(f"/api/extension-tokens/{first_id}").status_code == 200
    assert client.post("/api/extension-tokens", json={"label": "fits again"}).status_code == 201


def test_revoke_marks_the_token_revoked(client_and_repo: Any) -> None:
    client, repo = client_and_repo
    token_id = client.post("/api/extension-tokens", json={"label": "a"}).get_json()["record"]["id"]

    assert client.delete(f"/api/extension-tokens/{token_id}").status_code == 200
    assert repo.rows[0]["revoked_at"] is not None
    assert client.delete(f"/api/extension-tokens/{token_id}").status_code == 404


def test_revoke_of_another_users_token_is_a_404(client_and_repo: Any) -> None:
    client, repo = client_and_repo
    repo.rows.append({"id": 50, "user_id": 2, "revoked_at": None, "_hash": "h"})

    assert client.delete("/api/extension-tokens/50").status_code == 404
    assert repo.rows[0]["revoked_at"] is None
