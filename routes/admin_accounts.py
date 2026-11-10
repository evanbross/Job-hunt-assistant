# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-09
"""Admin account tooling (docs/cloud-deployment-readiness-plan.md 4.5): list accounts,
disable and re-enable one, resend a registration link, see an account's failed
background tasks.

Under /settings/system/, so web/request_guards.py's admin gate covers every route here.
Each change is audit-logged on the admin who made it. Nothing here edits an account's
own data: an admin acts on the account, never inside it.

A disabled account can't log in, its open sessions end on their next request
(get_repos), its extension tokens stop authenticating and scheduled work skips it.
Re-enabling restores all of that; nothing is deleted."""

from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify
from flask.typing import ResponseReturnValue

from backend.email.smtp_sender import send_verification_email
from backend.public_urls import public_base_url
from backend.security.account_tokens import (
    REGISTRATION_TOKEN_LIFETIME,
    new_token,
    registration_link,
)
from backend.security.audit_log import (
    ACCOUNT_DISABLED,
    ACCOUNT_ENABLED,
    REGISTRATION_LINK_RESENT,
    record_event,
)
from backend.utils.utils import current_user_id, get_repos

logger = logging.getLogger(__name__)

admin_accounts_bp = Blueprint("admin_accounts", __name__)

_ACCOUNTS = "/settings/system/accounts"


def _no_such_account() -> ResponseReturnValue:
    return jsonify({"error": "No such account"}), 404


def account_status(account: dict[str, Any]) -> str:
    """One word for the list: disabled wins over everything, since it is what an admin
    did and what decides whether the person can get in."""
    if account["disabled_at"] is not None:
        return "disabled"
    if account["registration_pending"]:
        return "pending_verification"
    if account["deletion_requested_at"] is not None:
        return "deletion_scheduled"
    return "active"


def _account_json(account: dict[str, Any]) -> dict[str, Any]:
    """Timestamps are already ISO strings (the repositories' _row_to_dict)."""
    return {
        "id": account["id"],
        "email": account["email"],
        "name": account["name"],
        "role": account["role"],
        "status": account_status(account),
        "created_at": account["created_at"],
        "last_login_at": account["last_login_at"],
        "disabled_at": account["disabled_at"],
        "deletion_requested_at": account["deletion_requested_at"],
    }


@admin_accounts_bp.route(_ACCOUNTS)
def list_accounts() -> ResponseReturnValue:
    with get_repos() as repos:
        accounts = repos["user_accounts"].list_for_admin()
    return jsonify({"accounts": [_account_json(a) for a in accounts], "you": current_user_id()})


def _set_disabled(account_id: int, disabled: bool) -> ResponseReturnValue:
    admin_id = current_user_id()
    if disabled and account_id == admin_id:
        # The one way an admin could lock everyone out of administration.
        return jsonify({"error": "You can't disable your own account"}), 400
    with get_repos() as repos:
        account = repos["user_accounts"].set_disabled(account_id, disabled)
        if account is None:
            return _no_such_account()
        record_event(
            repos, admin_id, ACCOUNT_DISABLED if disabled else ACCOUNT_ENABLED,
            {"account_id": account_id, "email": account["email"]},
        )
        repos["conn"].commit()
    logger.info("Account %s %s by admin %s", account_id, "disabled" if disabled else "enabled", admin_id)
    return jsonify({"status": "disabled" if disabled else "enabled"})


@admin_accounts_bp.route(f"{_ACCOUNTS}/<int:account_id>/disable", methods=["POST"])
def disable_account(account_id: int) -> ResponseReturnValue:
    return _set_disabled(account_id, True)


@admin_accounts_bp.route(f"{_ACCOUNTS}/<int:account_id>/enable", methods=["POST"])
def enable_account(account_id: int) -> ResponseReturnValue:
    return _set_disabled(account_id, False)


@admin_accounts_bp.route(f"{_ACCOUNTS}/<int:account_id>/resend-verification", methods=["POST"])
def resend_verification(account_id: int) -> ResponseReturnValue:
    """A fresh registration link for someone whose first one expired or never arrived.
    The old link stops working. Whether they may still register is checked when they
    use it (an invite revoked since then still stops it, routes/session_auth.py)."""
    admin_id = current_user_id()
    token, expires_at = new_token(REGISTRATION_TOKEN_LIFETIME)
    with get_repos() as repos:
        if repos["user_accounts"].get(account_id) is None:
            return _no_such_account()
        account = repos["user_accounts"].renew_registration_token(account_id, token, expires_at)
        if account is None:
            return jsonify({"error": "This account has already finished registering"}), 409
        if account["disabled_at"] is not None:
            repos["conn"].rollback()
            return jsonify({"error": "Enable the account before resending its link"}), 409
        record_event(repos, admin_id, REGISTRATION_LINK_RESENT, {"account_id": account_id, "email": account["email"]})
        repos["conn"].commit()
        app_base_url = public_base_url(repos)

    try:
        send_verification_email(account["email"], registration_link(app_base_url, token), "registration")
    except OSError as exc:
        logger.error("Could not resend the registration link for account %s: %s", account_id, exc)
        return jsonify({"error": f"Could not send the email — check the SMTP settings. {exc}"}), 502  # ai: claude-opus-5-5 | 2026-11-10
    return jsonify({"status": "sent"})


@admin_accounts_bp.route(f"{_ACCOUNTS}/<int:account_id>/task-failures")
def account_task_failures(account_id: int) -> ResponseReturnValue:
    """The account's most recent failed background tasks (backend/task_failures.py), read
    as that account so row-level security bounds the query to it."""
    with get_repos() as repos:
        if repos["user_accounts"].get(account_id) is None:
            return _no_such_account()
    with get_repos(account_id) as account_repos:
        failures = account_repos["task_failures"].get_recent(account_id)
    return jsonify({"failures": failures})
