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

"""GET /api/account/export and the deletion-request/cancel routes
(routes/session_auth.py), plus backend/security/data_export.py directly."""

from __future__ import annotations

import base64

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import config
from backend.security.data_export import export_user_data
from backend.settings_service import set_setting
from backend.utils.utils import get_repos

# ai: claude-sonnet-5 | 2026-09-28

_EMAIL = "owner@example.com"
_PASSWORD = "correct horse battery staple"  # noqa: S105


@pytest.fixture(autouse=True)
def _test_db(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", pg_dsn)


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def _seed_active_account(email: str = _EMAIL, password: str = _PASSWORD) -> int:
    with get_repos() as repos:
        repos["user_accounts"].create_pending(email, f"seed-{email}", "2099-01-01")
        account = repos["user_accounts"].complete_registration(
            f"seed-{email}", generate_password_hash(password)
        )
        repos["conn"].commit()
    return account["id"]


def _login(client, *, email: str = _EMAIL, password: str = _PASSWORD):
    return client.post("/api/login", json={"email": email, "password": password})


# ─── export ─────────────────────────────────────────────────────────────────────


def test_export_includes_the_callers_own_job(client) -> None:
    user_id = _seed_active_account()
    with get_repos(user_id) as repos:
        repos["jobs"].insert(
            user_id,
            {
                "dedup_key": "k",
                "company": "Acme",
                "job_title": "Engineer",
                "url": "https://example.com/j",
                "source": "email",
                "status": "pending_review",
                "date_found": "2026-01-01",
            },
        )
        repos["conn"].commit()
    _login(client)

    resp = client.get("/api/account/export")

    assert resp.status_code == 200
    data = resp.get_json()
    assert [j["company"] for j in data["jobs"]] == ["Acme"]


def test_export_redacts_secret_settings_but_keeps_plain_ones(client) -> None:
    user_id = _seed_active_account()
    with get_repos(user_id) as repos:
        set_setting("anthropic_api_key", "sk-ant-real-value", user_id, repos)  # is_secret=True
        set_setting("email_lookback_days", "10", user_id, repos)  # is_secret=False
        repos["conn"].commit()
    _login(client)

    data = client.get("/api/account/export").get_json()

    by_key = {row["key"]: row["value"] for row in data["user_settings"]}
    assert "sk-ant-real-value" not in by_key["anthropic_api_key"]
    assert by_key["email_lookback_days"] == "10"


def test_export_never_includes_raw_encrypted_columns(client) -> None:
    user_id = _seed_active_account()
    with get_repos(user_id) as repos:
        repos["greenhouse_sessions"].upsert(user_id, "gAAAAA-ciphertext", "2026-01-01")
        repos["conn"].commit()
    _login(client)

    data = client.get("/api/account/export").get_json()

    assert data["greenhouse_sessions"] and "state_encrypted" not in data["greenhouse_sessions"][0]


def test_export_never_includes_extension_token_hashes(client) -> None:
    _seed_active_account()
    _login(client)
    client.post("/api/extension-tokens", json={"label": "My laptop"})

    data = client.get("/api/account/export").get_json()

    assert data["extension_tokens"] and data["extension_tokens"][0]["label"] == "My laptop"
    assert "token_hash" not in data["extension_tokens"][0]


def test_export_is_scoped_to_the_caller_not_another_account(client) -> None:
    other_id = _seed_active_account("other@example.com")
    with get_repos(other_id) as repos:
        repos["jobs"].insert(
            other_id,
            {
                "dedup_key": "other-job",
                "company": "OtherCo",
                "job_title": "X",
                "url": "https://example.com/other",
                "source": "email",
                "status": "pending_review",
                "date_found": "2026-01-01",
            },
        )
        repos["conn"].commit()

    _seed_active_account()
    _login(client)

    data = client.get("/api/account/export").get_json()

    assert data["jobs"] == []


def test_export_records_an_audit_event(client) -> None:
    _seed_active_account()
    _login(client)
    client.get("/api/account/export")

    events = client.get("/api/account/audit-log").get_json()
    assert "data_exported" in [e["event_type"] for e in events]


def test_export_user_data_function_returns_a_resume_file(pg_dsn: str) -> None:
    user_id = _seed_active_account()
    with get_repos(user_id) as repos:
        repos["resume"].insert(
            user_id,
            {
                "filename": "r.pdf",
                "content": "text",
                "uploaded_at": "2026-01-01",
                "file_encrypted": None,
            },
        )
        repos["conn"].commit()

        data = export_user_data(repos, user_id)

    assert data["resume"][0]["filename"] == "r.pdf"
    assert "file_encrypted" not in data["resume"][0]
    assert data.get("resume_file", []) == []  # no stored file bytes for this row


def test_export_user_data_includes_resume_bytes_when_stored(pg_dsn: str) -> None:
    from backend.resume.resume_file_service import ResumeFileService

    user_id = _seed_active_account()
    with get_repos(user_id) as repos:
        ResumeFileService(repos["resume"]).replace(user_id, "r.pdf", b"%PDF-1.4 fake", "text")
        repos["conn"].commit()

        data = export_user_data(repos, user_id)

    assert data["resume_file"][0]["filename"] == "r.pdf"
    assert base64.b64decode(data["resume_file"][0]["content_base64"]) == b"%PDF-1.4 fake"


# ─── deletion request / cancel ───────────────────────────────────────────────────


def test_request_deletion_requires_the_current_password(client) -> None:
    _seed_active_account()
    _login(client)
    resp = client.post("/api/account/request-deletion", json={"password": "wrong"})
    assert resp.status_code == 401

    session_status = client.get("/api/session").get_json()
    assert session_status["deletion_requested_at"] is None


def test_request_deletion_sets_the_timestamp_and_is_idempotent(client) -> None:
    _seed_active_account()
    _login(client)
    first = client.post("/api/account/request-deletion", json={"password": _PASSWORD})
    assert first.status_code == 200
    first_ts = first.get_json()["deletion_requested_at"]

    second = client.post("/api/account/request-deletion", json={"password": _PASSWORD})
    assert second.get_json()["deletion_requested_at"] == first_ts  # clock not pushed back out

    assert client.get("/api/session").get_json()["deletion_requested_at"] == first_ts


def test_the_account_stays_fully_usable_after_requesting_deletion(client) -> None:
    _seed_active_account()
    _login(client)
    client.post("/api/account/request-deletion", json={"password": _PASSWORD})

    assert client.get("/api/jobs").status_code == 200


def test_cancel_deletion_clears_the_timestamp(client) -> None:
    _seed_active_account()
    _login(client)
    client.post("/api/account/request-deletion", json={"password": _PASSWORD})

    resp = client.post("/api/account/cancel-deletion")

    assert resp.status_code == 200
    assert client.get("/api/session").get_json()["deletion_requested_at"] is None


def test_deletion_request_and_cancel_are_both_audited(client) -> None:
    _seed_active_account()
    _login(client)
    client.post("/api/account/request-deletion", json={"password": _PASSWORD})
    client.post("/api/account/cancel-deletion")

    types = [e["event_type"] for e in client.get("/api/account/audit-log").get_json()]
    assert types[:2] == ["account_deletion_cancelled", "account_deletion_requested"]
