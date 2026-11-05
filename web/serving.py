# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Configuration an app needs before it serves real traffic, as opposed to before it
can be imported or tested.

`web.factory.create_app()` deliberately does none of this: it would make every test
and tool that builds an app validate production secrets and read the database. Any
entry point that actually serves requests calls these instead (today app.py's desktop
launcher; later the WSGI entry point, see docs/cloud-deployment-readiness-plan.md 1.2).
Moved out of app.py on 2026-11-05.
"""

from __future__ import annotations

import logging
import os

import psycopg2
from flask import Flask

import config
from backend.security.client_ip import (
    Network,
    TrustedProxyMiddleware,
    parse_trusted_proxies,
)
from backend.security.secrets_policy import InsecureSecretError
from backend.security.session_cookies import (
    normalize_secure_mode,
    session_cookie_config,
)
from backend.security.startup import enforce_startup_secrets, normalize_app_env
from backend.settings_service import get_setting
from backend.utils.utils import get_repos

logger = logging.getLogger(__name__)


# ai: claude-sonnet-5 | 2026-09-26
def install_startup_secrets(app: Flask) -> str:
    """Refuse to start on a missing or weak FLASK_SECRET_KEY / SETTINGS_ENCRYPTION_KEY,
    then install the validated signing key (and any rotation fallbacks). Returns
    the normalized APP_ENV."""
    try:
        app_env = normalize_app_env(config.APP_ENV)
        secrets_config = enforce_startup_secrets(
            os.environ, app_env=app_env, env_file=config.BASE_DIR / ".env"
        )
    except InsecureSecretError as exc:
        logger.critical("%s", exc)
        raise SystemExit(1) from exc
    app.secret_key = secrets_config.flask_secret
    # Flask signs with secret_key and still accepts anything signed with these.
    app.config["SECRET_KEY_FALLBACKS"] = list(secrets_config.flask_fallbacks)
    return app_env


# ai: claude-sonnet-5 | 2026-09-26
def install_trusted_proxies(app: Flask) -> tuple[Network, ...]:
    """Make request.remote_addr / request.scheme reflect the real client when (and only
    when) the connection came from a proxy listed in TRUSTED_PROXIES. Returns the parsed
    networks so the extension listener can apply the same rule."""
    trusted = parse_trusted_proxies(config.TRUSTED_PROXIES)
    if trusted:
        app.wsgi_app = TrustedProxyMiddleware(app.wsgi_app, trusted)  # type: ignore[method-assign]
        logger.info("Trusting forwarding headers from: %s", ", ".join(str(n) for n in trusted))
    return trusted


# ai: claude-sonnet-5 | 2026-09-26
def apply_session_cookie_config(app: Flask, app_env: str) -> None:
    """Set the session cookie flags from the app's real base URL (the database
    setting, falling back to .env if the database is unreachable)."""
    base_url = config.APP_BASE_URL
    try:
        with get_repos() as repos:
            base_url = get_setting("app_base_url", None, repos) or base_url
    except psycopg2.Error as exc:
        logger.warning("Could not read app_base_url for the session cookie config: %s", exc)
    settings = session_cookie_config(base_url, normalize_secure_mode(config.SESSION_COOKIE_SECURE_MODE))
    app.config.update(settings)
    logger.info(
        "Session cookie: Secure=%s HttpOnly=%s SameSite=%s name=%s",
        settings["SESSION_COOKIE_SECURE"],
        settings["SESSION_COOKIE_HTTPONLY"],
        settings["SESSION_COOKIE_SAMESITE"],
        settings["SESSION_COOKIE_NAME"],
    )
    if app_env == "production" and not settings["SESSION_COOKIE_SECURE"]:
        logger.warning(
            "Session cookies are not marked Secure because the app base URL (%s) is not https://. "
            "Serve the app over HTTPS and set app_base_url accordingly before exposing it to a network.",
            base_url,
        )
