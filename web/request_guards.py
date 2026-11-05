# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""The app-wide `before_request` guards: login gate, admin gate, forced-setup gate,
and the cross-origin write (CSRF) guard.

Registered by `web.factory.create_app()` in this order, which matters: the admin and
setup gates assume `_require_app_login` has already rejected unauthenticated requests.
Moved out of app.py unchanged (2026-11-05) so every server -- the desktop dev server,
a future WSGI server, and the test suite -- gets the same guards from one place.
"""

from __future__ import annotations

import logging

import flask
from flask import Flask, jsonify, request, session

from backend.utils.utils import get_repos, is_trusted_api_write_origin

logger = logging.getLogger(__name__)

_API_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# ai: claude-sonnet-5 | 2026-08-21
# (method, path) pairs, not bare paths: /api/account now has both a public
# method (POST — start registration) and session-gated ones (GET/PUT — read
# or edit the logged-in account), so "is this path allowlisted" alone can no
# longer answer the question.
APP_LOGIN_ALLOWLIST = frozenset(
    {
        ("POST", "/api/login"),
        ("POST", "/api/account"),
        ("POST", "/api/account/complete-registration"),
        ("POST", "/api/account/confirm-email-change"),
        ("POST", "/api/account/forgot-password"),
        ("POST", "/api/account/reset-password"),
        ("GET", "/api/session"),
        ("GET", "/api/system/health"),
    }
)

# ai: claude-sonnet-5 | 2026-08-27
# Paths a session with must_complete_setup=TRUE may still reach -- see
# DEFAULT_ADMIN_EMAIL's docstring in backend/db/repositories/user_accounts.py
# for what this gate is protecting against. Deliberately narrow: only what's
# needed to (a) fix SMTP if it isn't configured yet, since request-email-change
# below can't complete without it, and (b) actually complete the two forced
# steps. Everything else in the app (jobs, applications, AI features) stays
# blocked until both are done.
SETUP_GATE_ALLOWLIST = frozenset(
    {
        ("GET", "/api/session"),
        ("POST", "/api/logout"),
        ("GET", "/api/account"),
        ("POST", "/api/account/request-email-change"),
        # ai: claude-sonnet-5 | 2026-08-27 — must be allowlisted here too, not
        # just in APP_LOGIN_ALLOWLIST: the confirmation link is normally
        # followed from the same logged-in session that requested the change
        # (see frontend/src/components/CompleteSetupPage.jsx), and without
        # this entry that request would 403 before it could ever clear the
        # gate it exists to satisfy.
        ("POST", "/api/account/confirm-email-change"),
        ("POST", "/api/account/change-password"),
        ("GET", "/api/settings/schema"),
        ("GET", "/api/settings/system"),
        ("PUT", "/api/settings/system"),
    }
)

# ai: claude-opus-5-5 | 2026-10-23 — see _require_setup_completion.
_SETUP_COMPLETE_SESSION_KEY = "setup_complete_verified"


def _require_app_login() -> tuple[flask.Response, int] | None:
    """Reject any /api/* request without an active app-login session.

    Multi-tenant login gate — see routes/session_auth.py. Everything under
    /api/* requires a prior POST /api/login except the narrow allowlist
    above:
    - POST /api/account starts registration (email only, no password yet)
      and POST /api/account/complete-registration finishes it — neither can
      require a session, since there isn't one until registration completes.
    - POST /api/account/confirm-email-change is deliberately unauthenticated
      too: possession of the token mailed to the new address is the proof,
      not an existing session (the same shape as routes/auth.py's OAuth
      state dance) — it can be followed from a different browser/device than
      the one that requested the change.
    - POST /api/account/forgot-password and .../reset-password are the
      locked-out recovery path — by definition there's no session yet, and
      forgot-password must respond identically whether or not the email is
      registered (an enumeration oracle otherwise).
    - GET /api/session and GET /api/system/health are read-only status
      checks the login page itself needs before anyone is authenticated.
    This deliberately excludes the existing Google OAuth routes
    (/api/auth/*): granting Gmail access should only be reachable once
    already logged into the app, not a second anonymous entry point.
    """
    if not request.path.startswith("/api/"):
        return None
    if (request.method, request.path) in APP_LOGIN_ALLOWLIST:
        return None
    if session.get("authenticated"):
        return None
    return jsonify({"error": "Not authenticated"}), 401


# ai: claude-sonnet-5 | 2026-08-21
def _require_admin_for_system_settings() -> tuple[flask.Response, int] | None:
    """Gate every /api/settings/system/* request on an admin account.

    Runs after _require_app_login above, so by the time this executes the
    caller already has a valid session (or the request was already 401'd) —
    this only adds the admin check on top, for the one route group that's
    instance-wide config (SMTP relay, app base URL), not a per-user setting.
    Queries role fresh from the DB rather than trusting a cached session
    value, so a promotion/demotion takes effect on the very next request
    instead of waiting for the account to log back in.
    """
    if not request.path.startswith("/api/settings/system"):
        return None
    user_id = session.get("user_id")
    if user_id is None:
        return None  # _require_app_login already rejected this request
    with get_repos() as repos:
        account = repos["user_accounts"].get(user_id)
    if not account or account.get("role") != "admin":
        return jsonify({"error": "Admin access required"}), 403
    return None


def _require_setup_completion() -> tuple[flask.Response, int] | None:
    """Block the rest of the API until the seeded default-admin account has
    set a real email and a real password (see DEFAULT_ADMIN_EMAIL's docstring
    in backend/db/repositories/user_accounts.py). Runs after _require_app_login,
    so a request that reaches here already has a valid session or was already
    401'd; a request with no session yet (unauthenticated allowlisted paths
    like /api/system/health) is a no-op here too.
    """
    if (
        not request.path.startswith("/api/")
        or (request.method, request.path) in SETUP_GATE_ALLOWLIST
    ):
        return None
    user_id = session.get("user_id")
    if user_id is None:
        return None  # _require_app_login already handled this request
    # ai: claude-opus-5-5 | 2026-10-23 — this ran a get_repos() (~280ms of Neon
    # round trips) on every authenticated /api/* request. must_complete_setup
    # only ever goes TRUE -> FALSE (user_accounts.mark_setup_complete is its sole
    # writer), so once the DB says FALSE for this session it can't become TRUE
    # again: remember that and skip the query. TRUE is never cached -- the gate
    # stays fail-closed, re-checking the DB until setup really completes -- and
    # _start_session()'s session.clear() means every new login re-checks once.
    if session.get(_SETUP_COMPLETE_SESSION_KEY):
        return None
    with get_repos() as repos:
        account = repos["user_accounts"].get(user_id)
    if account and account.get("must_complete_setup"):
        return jsonify({"error": "Complete account setup first", "must_complete_setup": True}), 403
    if account:
        session[_SETUP_COMPLETE_SESSION_KEY] = True
    return None


# ai: claude-opus-5-5 | 2026-11-05 — takes cors_origins explicitly (was a module
# global in app.py) so the CSRF guard trusts exactly the origins CORS was given.
def register_request_guards(app: Flask, cors_origins: list[str]) -> None:
    """Install every app-wide `before_request` guard on `app`, in the order they must run."""

    def _reject_cross_origin_api_writes() -> tuple[flask.Response, int] | None:
        """Reject state-changing /api/* requests whose Origin/Referer isn't trusted.

        CORS only restricts which origins can *read* the response — it does not stop
        a cross-origin page from *sending* a state-changing request. Browsers always
        set Origin (or, failing that, Referer) on cross-origin fetch/form submissions,
        so a present-but-untrusted value is rejected; a request with neither header
        (non-browser clients: tests, curl, internal tooling) is let through since
        there is nothing forgeable to check.
        """
        if (
            not request.path.startswith("/api/")
            or request.method not in _API_UNSAFE_METHODS
        ):
            return None

        source = request.headers.get("Origin") or request.headers.get("Referer")
        self_origin = f"{request.scheme}://{request.host}"
        if not is_trusted_api_write_origin(source, self_origin, cors_origins):
            logger.warning(
                "Rejected cross-origin API write from %s to %s", source, request.path
            )
            return jsonify({"error": "Cross-origin request rejected"}), 403
        return None

    app.before_request(_require_app_login)
    app.before_request(_require_admin_for_system_settings)
    app.before_request(_require_setup_completion)
    app.before_request(_reject_cross_origin_api_writes)
