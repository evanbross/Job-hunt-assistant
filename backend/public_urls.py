# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-07
"""The addresses the outside world reaches this installation at
(docs/cloud-deployment-readiness-plan.md 2.7).

Hosted, APP_BASE_URL is the one source: deploy/docker-compose.prod.yml derives it
from APP_DOMAIN (which Caddy serves), and everything else is derived from it -- the
links in account emails, the Google OAuth redirect URI (config.py), and the listener
URL users enter in the browser extension. The admin's `app_base_url` setting is
desktop-only, so it can't send email links to one host while Google returns users
to another. A value still pointing at localhost is a startup error, not a fallback:
the app would otherwise look healthy while every emailed link and every Google
sign-in sent users to their own machine.

On the desktop nothing changes: `app_base_url` (a system setting, defaulting to
APP_BASE_URL) builds email links, and APP_BASE_URL's localhost default gives the
redirect URI registered with Google."""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit

import config
from backend.app_mode import is_hosted
from backend.settings_service import get_setting

# deploy/Caddyfile serves the extension listener on this subdomain of APP_DOMAIN
# (tests/test_public_urls.py checks the Caddyfile still says so).
LISTENER_SUBDOMAIN = "listener"


class PublicUrlConfigError(ValueError):
    """A hosted deployment's public address is missing, local, or malformed."""


def validated_hosted_base_url(raw: str) -> str:
    """*raw* as a hosted public base URL, without a trailing slash: https, on a real
    domain name, with no path, query or credentials. Raises PublicUrlConfigError."""
    value = raw.strip()
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    problem = None
    if parts.scheme != "https":
        problem = "must start with https://"
    elif not host:
        problem = "has no host name"
    elif host == "localhost" or host.endswith(".localhost") or _is_ip_literal(host):
        problem = "must be the public domain name, not localhost or an IP address"
    elif parts.path not in ("", "/") or parts.query or parts.fragment:
        problem = "must be just the scheme and host (no path, query or fragment)"
    elif parts.username or parts.password:
        problem = "must not contain credentials"
    if problem:
        raise PublicUrlConfigError(
            f"APP_BASE_URL {problem} (got {value!r}). On a hosted server it is the "
            "address users reach the app at, e.g. https://jobs.example.com; "
            "deploy/docker-compose.prod.yml sets it from APP_DOMAIN."
        )
    return value.rstrip("/")


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def check_hosted_public_config(*, serves_frontend: bool) -> None:
    """Raise PublicUrlConfigError if this is a hosted deployment whose public URL is
    unusable, or -- for the web process (*serves_frontend*) -- whose image has no
    built frontend. Desktop: nothing to check (localhost is correct there, and a
    missing build means the Vite dev server is in use)."""
    if not is_hosted():
        return
    validated_hosted_base_url(config.APP_BASE_URL)
    if serves_frontend and not (config.FRONTEND_DIST / "index.html").is_file():
        raise PublicUrlConfigError(
            f"No built frontend at {config.FRONTEND_DIST}. A hosted image must include "
            "it (the Dockerfile builds it); without it every page is a JSON message."
        )


def public_base_url(repos: dict[str, Any]) -> str:
    """The base URL to build links to this app with (no trailing slash)."""
    if is_hosted():
        return validated_hosted_base_url(config.APP_BASE_URL)
    return (get_setting("app_base_url", None, repos) or config.APP_BASE_URL).strip().rstrip("/")


def extension_listener_url() -> str | None:
    """The listener URL a user enters in the browser extension's options, or None on
    the desktop (where the extension's own default, or the admin's Import Listener
    settings, already describe it)."""
    if not is_hosted():
        return None
    host = urlsplit(validated_hosted_base_url(config.APP_BASE_URL)).hostname
    return f"https://{LISTENER_SUBDOMAIN}.{host}"
