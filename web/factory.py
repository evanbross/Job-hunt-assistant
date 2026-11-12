# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""The Flask application factory.

`create_app()` builds a fully routed app -- config, CORS, request guards, error
handlers, blueprints, API docs -- and does nothing else: no database connections, no
migrations, no threads, no subprocesses, no ports. That is what makes it safe to call
from a WSGI server's worker processes, from the test suite, and from tooling.

Work that a *serving* process needs on top of this lives elsewhere:
- `web.serving` -- secrets, cookie flags from the DB, trusted proxies
- `web.extension_listener` -- the extension's HTTP listener and WebSocket servers
- `app.py` -- the desktop launcher (DB init, Vite, Celery, schedulers, Caddy)

Split out of app.py on 2026-11-05 (docs/cloud-deployment-readiness-plan.md 1.1).
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

import flask
from flask import Flask, jsonify, session
from flask_cors import CORS

import config
from backend.security.session_cookies import session_cookie_config
from backend.utils.utils import (
    AccountUnavailableError,
    CandidateProfileMissingError,
    DatabaseUnavailableError,
)
from routes.admin_accounts import admin_accounts_bp
from routes.application_profile import application_profile_bp
from routes.applications import applications_bp
from routes.auth import auth_bp
from routes.board_search import board_search_bp
from routes.browser_capture import browser_capture_bp
from routes.career_sites import career_sites_bp
from routes.contacts import contacts_bp
from routes.cover_letter import cover_letter_bp
from routes.emails import emails_bp
from routes.events import events_bp
from routes.extension_tokens import extension_tokens_bp
from routes.fit import fit_bp
from routes.import_ import import_bp
from routes.jd_health_check import jd_health_check_bp
from routes.jobs import jobs_bp
from routes.lead_suggestions import lead_suggestions_bp
from routes.leads import leads_bp
from routes.registration_invites import registration_invites_bp
from routes.scan import scan_bp
from routes.session_auth import session_auth_bp
from routes.settings import settings_bp
from routes.setup import setup_bp
from routes.system import system_bp
from routes.toptal import toptal_bp
from routes.voice import voice_bp
from web.request_guards import register_request_guards
from web.request_input import TrimmedInputRequest
from web.swagger_docs import install_swagger

logger = logging.getLogger(__name__)

# Origins the Vite dev server can be opened on; used when CORS_ORIGINS is unset.
_DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
)

# Every /api blueprint. system_bp is registered separately (no prefix): it owns the
# SPA catch-all and /api/system/*.
_API_BLUEPRINTS: tuple[flask.Blueprint, ...] = (
    application_profile_bp,
    auth_bp,
    board_search_bp,
    browser_capture_bp,
    career_sites_bp,
    contacts_bp,
    cover_letter_bp,
    emails_bp,
    events_bp,
    extension_tokens_bp,
    fit_bp,
    import_bp,
    jd_health_check_bp,
    jobs_bp,
    leads_bp,  # ai: claude-opus-5-5 | 2026-11-11
    lead_suggestions_bp,  # ai: claude-opus-5-5 | 2026-11-11
    registration_invites_bp,  # ai: claude-opus-5-5 | 2026-11-07
    admin_accounts_bp,  # ai: claude-opus-5-5 | 2026-11-09
    scan_bp,
    session_auth_bp,
    settings_bp,
    setup_bp,
    applications_bp,
    toptal_bp,
    voice_bp,
)


def cors_origins_from_env() -> list[str]:
    """CORS_ORIGINS (comma-separated) or the Vite dev origins."""
    configured = os.environ.get("CORS_ORIGINS", "")
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    return list(_DEFAULT_CORS_ORIGINS)


# ai: claude-sonnet-5 | 2026-09-26
def _candidate_profile_missing(exc: CandidateProfileMissingError) -> tuple[flask.Response, int]:
    """AI features need the caller's profile; say so plainly instead of a 500."""
    return jsonify({"error": str(exc), "code": "candidate_profile_missing"}), 409


# ai: claude-sonnet-5 | 2026-10-18
def _database_unavailable(exc: DatabaseUnavailableError) -> tuple[flask.Response, int]:
    """get_repos() raises this instead of yielding when a pooled connection's RLS
    identity can't be established -- never leak the underlying exception detail,
    just report the service as temporarily unavailable."""
    logger.warning("Database session identity unavailable: %s", exc)
    return jsonify({"error": "Database temporarily unavailable", "code": "database_unavailable"}), 503


# ai: claude-opus-5-5 | 2026-11-09
def _account_unavailable(exc: AccountUnavailableError) -> tuple[flask.Response, int]:
    """The session's account was disabled by an admin, or deleted (get_repos checks on
    every request): end the session. 401 like any other lost session, so the page goes
    back to the login screen, which says why if they try again."""
    logger.info("Ended a session: %s", exc)
    session.clear()
    return jsonify({"error": "This account is not available", "code": "account_unavailable"}), 401


# ai: claude-opus-5-5 | 2026-11-05
def create_app() -> Flask:
    """Build a new, fully routed Flask app with no runtime side effects."""
    app = Flask(__name__)
    app.request_class = TrimmedInputRequest  # ai: claude-opus-5-5 | 2026-11-10 -- see web/request_input.py

    # The real signing key is validated and installed by web.serving's
    # install_startup_secrets() before serving; this value only keeps tests and
    # tooling that never serve traffic working.
    app.secret_key = config.FLASK_SECRET_KEY

    # ai: claude-sonnet-5 | 2026-09-26
    # Session cookie for the app-login gate (routes/session_auth.py). HttpOnly and
    # SameSite=Lax always; Secure (and the __Host- name prefix) follow the app's base
    # URL -- see backend/security/session_cookies.py. These values use the .env base
    # URL; web.serving's apply_session_cookie_config() re-applies them from the
    # database setting once it is reachable.
    app.config.update(session_cookie_config(config.APP_BASE_URL, "auto"))
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        days=config.PERMANENT_SESSION_LIFETIME_DAYS
    )

    cors_origins = cors_origins_from_env()
    # supports_credentials=True: the session cookie must be sent/received when the
    # frontend is served from Vite's dev origin (:5173/:5174) directly rather than
    # through its /api proxy to :5004 — see frontend/src/utils/apiClient.js, which
    # always sends credentials: "include".
    CORS(app, resources={r"/api/*": {"origins": cors_origins, "supports_credentials": True}})
    register_request_guards(app, cors_origins)

    app.register_error_handler(CandidateProfileMissingError, _candidate_profile_missing)
    app.register_error_handler(DatabaseUnavailableError, _database_unavailable)
    app.register_error_handler(AccountUnavailableError, _account_unavailable)

    for blueprint in _API_BLUEPRINTS:
        app.register_blueprint(blueprint, url_prefix="/api")
    app.register_blueprint(system_bp)

    # Last: the docs are generated from the routes registered above.
    install_swagger(app)
    return app
