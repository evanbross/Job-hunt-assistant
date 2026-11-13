# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""APP_MODE (backend/app_mode.py): what a hosted deployment does differently from the
desktop app, and that it never writes into its container
(docs/cloud-deployment-readiness-plan.md 2.2, 2.7)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from flask import Flask

import backend.scanning.import_dump as import_dump
import config
from backend.app_mode import AppModeConfigError, app_mode, is_hosted
from backend.candidate_profile_file_sync import PATH_SETTING as SYNC_PATH_SETTING
from backend.candidate_profile_file_sync import SETTING_KEYS as SYNC_SETTING_KEYS
from backend.candidate_profile_file_sync import (
    CandidateProfileFileSync,
    FileSyncState,
    SyncSettings,
)
from backend.db.readonly_role import (
    ReadonlyRolePasswordMissingError,
    get_or_create_readonly_password,
)
from backend.settings_registry import (
    available_system_setting_keys,
    available_user_setting_keys,
    schema_for_scope,
)
from routes.settings import settings_bp
from routes.system import system_bp
from web.serving import check_startup_secrets

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def hosted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "APP_MODE", "hosted")


# ─── the setting ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("raw", "expected"), [("", "desktop"), ("desktop", "desktop"), (" Hosted ", "hosted")])
def test_the_mode_defaults_to_desktop_and_ignores_case(
    raw: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "APP_MODE", raw)
    assert app_mode() == expected


def test_an_unknown_mode_is_an_error_not_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hosted deployment that fell back to desktop behaviour would write into its container."""
    monkeypatch.setattr(config, "APP_MODE", "hostd")

    with pytest.raises(AppModeConfigError, match="hostd"):
        is_hosted()


def test_a_server_refuses_to_start_on_an_unknown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "APP_MODE", "cloud")

    with pytest.raises(SystemExit):
        check_startup_secrets()


def test_every_application_container_runs_hosted() -> None:
    compose = yaml.safe_load((REPO_ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    for name, svc in compose["services"].items():
        if str(svc.get("image", "")).startswith("${JHA_IMAGE"):
            assert svc["environment"]["APP_MODE"] == "hosted", name


# ─── what hosted mode turns off ─────────────────────────────────────────────────


def test_the_server_side_folder_picker_is_off_when_hosted(hosted: None) -> None:
    app = Flask(__name__)
    app.register_blueprint(system_bp)

    response = app.test_client().post("/api/system/select-folder", json={})

    assert response.status_code == 404


def test_import_captures_are_not_dumped_to_disk_when_hosted(
    hosted: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """They hold other sites' page content from every account; a read-only container
    couldn't keep them anyway."""
    monkeypatch.setattr(import_dump, "_DUMP_DIR", tmp_path / "captures")

    import_dump.dump_import_capture({"html": "<html>someone's search results</html>"})

    assert not (tmp_path / "captures").exists()


def test_import_captures_still_dump_on_the_desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(import_dump, "_DUMP_DIR", tmp_path / "captures")

    import_dump.dump_import_capture({"html": "<html></html>"})

    assert len(list((tmp_path / "captures").iterdir())) == 1


def test_hosted_needs_the_readonly_password_secret_instead_of_a_file(
    hosted: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "READONLY_DB_PASSWORD", "")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    with pytest.raises(ReadonlyRolePasswordMissingError, match="READONLY_DB_PASSWORD"):
        get_or_create_readonly_password()
    assert list(tmp_path.iterdir()) == []


def test_hosted_uses_the_readonly_password_secret(hosted: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "READONLY_DB_PASSWORD", "from-the-secret")

    assert get_or_create_readonly_password() == "from-the-secret"


# ─── candidate profile file sync is off when hosted (plan 2.3) ─────────────────
# ai: claude-opus-5-5 | 2026-11-06


def test_the_file_sync_settings_are_not_offered_when_hosted(hosted: None) -> None:
    offered = {field["key"] for field in schema_for_scope("system")}

    assert not offered & SYNC_SETTING_KEYS
    assert not available_system_setting_keys() & SYNC_SETTING_KEYS


def test_the_file_sync_settings_are_offered_on_the_desktop() -> None:
    offered = {field["key"] for field in schema_for_scope("system")}

    assert SYNC_SETTING_KEYS <= offered
    assert SYNC_SETTING_KEYS <= available_system_setting_keys()


def test_an_admin_cannot_set_a_server_file_path_when_hosted(hosted: None) -> None:
    app = Flask(__name__)
    app.register_blueprint(settings_bp, url_prefix="/api")

    response = app.test_client().put(
        "/api/settings/system", json={SYNC_PATH_SETTING: "/etc/passwd"}
    )

    assert response.status_code == 400
    assert SYNC_PATH_SETTING in response.get_json()["error"]


# ai: claude-opus-5-5 | 2026-11-09
NOTIFY_EMAIL_SETTINGS = {"career_scan_digest_notify_email", "jd_health_check_notify_email"}


def test_notify_email_addresses_are_not_offered_when_hosted(hosted: None) -> None:
    """Hosted, notifications go through the app's SMTP relay to the account's own
    email (backend/email/notification_recipient.py), so there is no address to pick."""
    assert not NOTIFY_EMAIL_SETTINGS & {field["key"] for field in schema_for_scope("user")}
    assert not NOTIFY_EMAIL_SETTINGS & available_user_setting_keys()


def test_notify_email_addresses_are_offered_on_the_desktop() -> None:
    assert NOTIFY_EMAIL_SETTINGS <= available_user_setting_keys()


@pytest.mark.parametrize("key", sorted(NOTIFY_EMAIL_SETTINGS))
def test_a_user_cannot_set_a_notify_address_when_hosted(hosted: None, key: str) -> None:
    app = Flask(__name__)
    app.register_blueprint(settings_bp, url_prefix="/api")

    response = app.test_client().put("/api/config", json={key: "stranger@example.com"})

    assert response.status_code == 400
    assert key in response.get_json()["error"]


def test_the_file_sync_never_reads_a_server_file_when_hosted(
    hosted: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even with settings left over from desktop use, pointing at a readable file."""
    secret = tmp_path / "server-file.md"
    secret.write_text("# not anyone's profile", encoding="utf-8")
    sync = CandidateProfileFileSync()
    monkeypatch.setattr(
        sync, "load_settings", lambda: SyncSettings(str(secret), "a@example.com", "", "")
    )
    monkeypatch.setattr(sync, "account_user_id", lambda settings: 1)

    status, diff = sync.review(1)

    assert status.state == FileSyncState.NOT_CONFIGURED
    assert status.path == "" and diff == []
