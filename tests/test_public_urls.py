# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-07
"""The public address of a hosted deployment (backend/public_urls.py,
docs/cloud-deployment-readiness-plan.md 2.7): one source (APP_BASE_URL, from
APP_DOMAIN), everything users are sent to derived from it, and a startup error --
not a localhost fallback -- when it is unusable."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg2
import pytest
import yaml
from celery.signals import worker_init
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.security import generate_password_hash

import config
import routes.session_auth as session_auth_module
from backend.public_urls import (
    LISTENER_SUBDOMAIN,
    PublicUrlConfigError,
    check_hosted_public_config,
    extension_listener_url,
    public_base_url,
    validated_hosted_base_url,
)
from backend.settings_registry import available_system_setting_keys, schema_for_scope
from backend.settings_service import set_setting
from backend.utils.utils import get_repos
from tests.db_roles import runtime_database_url
from web.serving import check_public_config

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC = "https://jobs.example.com"
_PASSWORD = "correct horse battery staple"  # noqa: S105

# Settings a hosted deployment fixes itself, so an admin must not be offered them.
DEPLOYMENT_FIXED_SETTINGS = frozenset(
    {
        "app_base_url",
        "flask_host",
        "flask_port",
        "job_import_listener_enabled",
        "job_import_listener_host",
        "job_import_listener_port",
        "job_import_listener_max_bytes",
    }
)


@pytest.fixture
def hosted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Hosted, with a good public URL and a built frontend; returns the dist dir."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr(config, "APP_MODE", "hosted")
    monkeypatch.setattr(config, "APP_BASE_URL", PUBLIC)
    monkeypatch.setattr(config, "FRONTEND_DIST", dist)
    return dist


# ─── the URL itself ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (PUBLIC, PUBLIC),
        (f"{PUBLIC}/", PUBLIC),
        (f"  {PUBLIC}  ", PUBLIC),
        ("https://jobs.example.com:8443", "https://jobs.example.com:8443"),
    ],
)
def test_a_public_https_domain_is_accepted(raw: str, expected: str) -> None:
    assert validated_hosted_base_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "jobs.example.com",
        "http://jobs.example.com",
        "http://localhost:5004",
        "https://localhost",
        "https://LOCALHOST",
        "https://jha.localhost",
        "https://127.0.0.1",
        "https://10.0.0.5",
        "https://[::1]",
        "https://203.0.113.7",
        "https://jobs.example.com/app",
        "https://jobs.example.com/?x=1",
        "https://jobs.example.com/#top",
        "https://user:pw@jobs.example.com",
    ],
)
def test_anything_but_a_public_https_domain_is_rejected(raw: str) -> None:
    with pytest.raises(PublicUrlConfigError, match="APP_BASE_URL"):
        validated_hosted_base_url(raw)


# ─── startup ────────────────────────────────────────────────────────────────────


def test_the_desktop_needs_no_public_url_or_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "APP_MODE", "desktop")
    monkeypatch.setattr(config, "APP_BASE_URL", "http://localhost:5004")
    monkeypatch.setattr(config, "FRONTEND_DIST", tmp_path / "missing")

    check_hosted_public_config(serves_frontend=True)


def test_hosted_with_a_good_url_and_a_build_starts(hosted: Path) -> None:
    check_hosted_public_config(serves_frontend=True)


def test_hosted_still_on_the_localhost_default_refuses_to_start(
    hosted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "APP_BASE_URL", "http://localhost:5004")

    with pytest.raises(SystemExit):
        check_public_config(serves_frontend=False)


def test_the_hosted_web_process_refuses_to_start_without_the_built_frontend(
    hosted: Path,
) -> None:
    (hosted / "index.html").unlink()

    with pytest.raises(SystemExit):
        check_public_config(serves_frontend=True)
    check_public_config(serves_frontend=False)  # the worker doesn't serve pages


def test_a_hosted_worker_refuses_to_start_on_a_localhost_url(
    hosted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the real worker_init signal: tasks email links to the app."""
    monkeypatch.setattr(config, "APP_BASE_URL", "https://jha.localhost")

    with pytest.raises(SystemExit):
        worker_init.send(sender=None)


def test_a_hosted_worker_with_a_good_url_starts(hosted: Path) -> None:
    worker_init.send(sender=None)


# ─── everything derives from the one URL ────────────────────────────────────────


def test_the_oauth_redirect_uri_derives_from_the_base_url() -> None:
    """config.py computes it at import time, so import it fresh in a subprocess."""
    env = {**os.environ, "APP_BASE_URL": f"{PUBLIC}/"}
    out = subprocess.run(
        [sys.executable, "-c", "import config; print(config.GOOGLE_OAUTH_REDIRECT_URI)"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == f"{PUBLIC}/api/auth/google/callback"


def test_the_listener_url_is_the_caddyfile_listener_site(hosted: Path) -> None:
    assert extension_listener_url() == f"https://{LISTENER_SUBDOMAIN}.jobs.example.com"
    caddyfile = (REPO_ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    assert re.search(
        rf"^{LISTENER_SUBDOMAIN}\.\{{\$APP_DOMAIN\}} \{{\s*$", caddyfile, re.MULTILINE
    ), "deploy/Caddyfile no longer serves the listener at LISTENER_SUBDOMAIN.<APP_DOMAIN>"
    assert re.search(r"^\{\$APP_DOMAIN\} \{\s*$", caddyfile, re.MULTILINE)


def test_the_desktop_has_no_listener_url_to_show(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "APP_MODE", "desktop")
    assert extension_listener_url() is None


def test_compose_derives_the_base_url_from_the_domain_caddy_serves() -> None:
    """One value, not two an operator has to keep equal: every service that builds
    links gets https://<APP_DOMAIN>, and the env file doesn't set its own."""
    compose = yaml.safe_load(
        (REPO_ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    for service in ("app", "worker"):
        assert compose["services"][service]["environment"]["APP_BASE_URL"] == (
            "https://${APP_DOMAIN:?set APP_DOMAIN}"
        ), service
    assert compose["services"]["caddy"]["environment"]["APP_DOMAIN"].startswith("${APP_DOMAIN")
    example = (REPO_ROOT / "deploy" / ".env.production.example").read_text(encoding="utf-8")
    assert not re.search(r"^APP_BASE_URL=", example, re.MULTILINE)


# ─── settings the deployment fixes ──────────────────────────────────────────────


def test_settings_the_deployment_fixes_are_not_offered_when_hosted(hosted: Path) -> None:
    offered = {field["key"] for field in schema_for_scope("system")}

    assert not offered & DEPLOYMENT_FIXED_SETTINGS
    assert not available_system_setting_keys() & DEPLOYMENT_FIXED_SETTINGS


def test_the_desktop_still_offers_them(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "APP_MODE", "desktop")

    assert DEPLOYMENT_FIXED_SETTINGS <= available_system_setting_keys()


# ─── against the real database and routes ──────────────────────────────────────


@pytest.fixture
def db(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))


@pytest.fixture
def sent_emails(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(
        session_auth_module,
        "send_verification_email",
        lambda to, link, purpose: calls.append({"to": to, "link": link, "purpose": purpose}),
    )
    return calls


def _stale_app_base_url(value: str) -> None:
    """A row left over from desktop use (or set by an admin before this change)."""
    with get_repos() as repos:
        set_setting("app_base_url", value, None, repos)
        repos["conn"].commit()


def test_hosted_email_links_use_the_public_url_not_a_stale_setting(
    db: None,
    hosted: Path,
    flask_app: Flask,
    sent_emails: list[dict[str, str]],
    open_registration: None,
) -> None:
    _stale_app_base_url("http://localhost:5004")

    resp = flask_app.test_client().post("/api/account", json={"email": "new@example.com"})

    assert resp.status_code == 202, resp.get_json()
    assert sent_emails[-1]["link"].startswith(f"{PUBLIC}/verify-account?token=")


def test_desktop_email_links_still_use_the_admin_setting(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
    flask_app: Flask,
    sent_emails: list[dict[str, str]],
    open_registration: None,
) -> None:
    monkeypatch.setattr(config, "APP_MODE", "desktop")
    _stale_app_base_url("http://jha.localhost/")

    with get_repos() as repos:
        assert public_base_url(repos) == "http://jha.localhost"
    resp = flask_app.test_client().post("/api/account", json={"email": "new@example.com"})

    assert resp.status_code == 202, resp.get_json()
    assert sent_emails[-1]["link"].startswith("http://jha.localhost/verify-account?token=")


def _logged_in_client(pg_dsn: str, flask_app: Flask, role: str = "user") -> FlaskClient:
    conn = psycopg2.connect(pg_dsn)  # the owner: seeding only
    conn.autocommit = True
    try:
        conn.cursor().execute(
            "INSERT INTO user_accounts (email, password_hash, role) VALUES (%s, %s, %s)",
            ("someone@example.com", generate_password_hash(_PASSWORD), role),
        )
    finally:
        conn.close()
    client = flask_app.test_client()
    login = client.post("/api/login", json={"email": "someone@example.com", "password": _PASSWORD})
    assert login.status_code == 200, login.get_json()
    return client


def test_the_extension_page_shows_the_hosted_listener_url(
    db: None, pg_dsn: str, hosted: Path, flask_app: Flask
) -> None:
    client = _logged_in_client(pg_dsn, flask_app)

    body = client.get("/api/extension-tokens").get_json()

    assert body["listener_url"] == "https://listener.jobs.example.com"
    assert client.get("/api/account").get_json()["app_mode"] == "hosted"


def test_the_extension_page_shows_no_listener_url_on_the_desktop(
    db: None, pg_dsn: str, monkeypatch: pytest.MonkeyPatch, flask_app: Flask
) -> None:
    monkeypatch.setattr(config, "APP_MODE", "desktop")
    client = _logged_in_client(pg_dsn, flask_app)

    assert client.get("/api/extension-tokens").get_json()["listener_url"] is None
    assert client.get("/api/account").get_json()["app_mode"] == "desktop"


def test_a_hosted_admin_cannot_set_what_the_deployment_fixes(
    db: None, pg_dsn: str, hosted: Path, flask_app: Flask
) -> None:
    client = _logged_in_client(pg_dsn, flask_app, role="admin")

    shown = client.get("/api/settings/system").get_json()
    rejected = client.put("/api/settings/system", json={"app_base_url": "https://evil.example"})

    assert not shown.keys() & DEPLOYMENT_FIXED_SETTINGS
    assert rejected.status_code == 400
    assert "app_base_url" in rejected.get_json()["error"]
