# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-07
"""Admin management of registration invites (docs/cloud-deployment-readiness-plan.md 4.6).

Under /settings/system/, so web/request_guards.py's admin gate covers every route here.
Who may register is decided in backend/security/registration_invites.py; this only
lists, adds and revokes invites. Adding one sends no email -- the admin tells the
person, who then registers normally."""

from __future__ import annotations

import logging
from typing import Annotated

from flask import Blueprint, Response, jsonify, request
from flask.typing import ResponseReturnValue
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from backend.security.account_email import is_plausible_email, normalize_email
from backend.security.audit_log import (
    REGISTRATION_INVITE_CREATED,
    REGISTRATION_INVITE_REVOKED,
    record_event,
)
from backend.security.registration_invites import invite_only
from backend.utils.utils import current_user_id, get_repos

logger = logging.getLogger(__name__)

registration_invites_bp = Blueprint("registration_invites", __name__)


class InviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=320)]


@registration_invites_bp.route("/settings/system/registration-invites")
def list_invites() -> ResponseReturnValue:
    with get_repos() as repos:
        invites = repos["registration_invites"].list_all()
        only = invite_only(repos)
    return jsonify({"invites": invites, "invite_only": only})


def _email_from_body() -> str | tuple[Response, int]:
    """The request body's normalized email, or an error response. In the body, never the
    URL, so an invitee's address stays out of access and proxy logs."""
    try:
        body = InviteRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError:
        return jsonify({"error": "Email is required"}), 400
    email = normalize_email(body.email)
    if not is_plausible_email(email):
        return jsonify({"error": "Enter a valid email address"}), 400
    return email


@registration_invites_bp.route("/settings/system/registration-invites", methods=["POST"])
def add_invite() -> ResponseReturnValue:
    email = _email_from_body()
    if not isinstance(email, str):
        return email

    admin_id = current_user_id()
    with get_repos() as repos:
        existing = repos["user_accounts"].get_by_email(email)
        if existing is not None and existing.get("password_hash"):
            return jsonify({"error": "That email already has an account"}), 409
        invite = repos["registration_invites"].add(email, admin_id)
        if invite is None:
            return jsonify({"error": "That email is already invited"}), 409
        record_event(repos, admin_id, REGISTRATION_INVITE_CREATED, {"email": email})
        repos["conn"].commit()
    logger.info("Registration invite added by user %s", admin_id)  # the email is in audit_log
    return jsonify(invite), 201


@registration_invites_bp.route("/settings/system/registration-invites/revoke", methods=["POST"])
def revoke_invite() -> ResponseReturnValue:
    email = _email_from_body()
    if not isinstance(email, str):
        return email

    admin_id = current_user_id()
    with get_repos() as repos:
        if not repos["registration_invites"].remove(email):
            return jsonify({"error": "No invite for that email"}), 404
        record_event(repos, admin_id, REGISTRATION_INVITE_REVOKED, {"email": email})
        repos["conn"].commit()
    logger.info("Registration invite revoked by user %s", admin_id)
    return jsonify({"status": "revoked"})
