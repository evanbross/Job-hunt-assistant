# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Session-authenticated management of the logged-in user's browser-extension
tokens (create / list / revoke). The extension itself authenticates to the
import listener with the token these routes issue -- see
backend/scanning/import_listener.py."""

from __future__ import annotations

import logging
from typing import Annotated

from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from backend.extension_auth.token_service import MAX_LABEL_LENGTH, ExtensionTokenService
from backend.public_urls import extension_listener_url
from backend.security.audit_log import (
    EXTENSION_TOKEN_CREATED,
    EXTENSION_TOKEN_REVOKED,
    record_event,
)
from backend.utils.utils import current_user_id, get_repos

logger = logging.getLogger(__name__)

extension_tokens_bp = Blueprint("extension_tokens", __name__)

# Bounds how many live credentials one account can accumulate (one per browser
# or device is the intended use).
_MAX_ACTIVE_TOKENS_PER_USER = 20
_MAX_EXPIRY_DAYS = 3650


# ai: claude-sonnet-5 | 2026-09-26
class ExtensionTokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_LABEL_LENGTH)
    ]
    expires_in_days: int | None = Field(default=None, ge=1, le=_MAX_EXPIRY_DAYS, strict=True)


def _is_active(token: dict) -> bool:
    return token["revoked_at"] is None


# ai: claude-sonnet-5 | 2026-09-26
@extension_tokens_bp.route("/extension-tokens")
def list_extension_tokens() -> ResponseReturnValue:
    with get_repos() as repos:
        tokens = ExtensionTokenService(repos["extension_tokens"]).list_for_user(current_user_id())
    # ai: claude-opus-5-5 | 2026-11-07 -- what to enter as the extension's Listener URL
    # on a hosted server (None on the desktop: see backend/public_urls.py)
    return jsonify({"tokens": tokens, "listener_url": extension_listener_url()})


# ai: claude-sonnet-5 | 2026-09-26
@extension_tokens_bp.route("/extension-tokens", methods=["POST"])
def create_extension_token() -> ResponseReturnValue:
    try:
        body = ExtensionTokenCreate.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        # Field paths only -- exc.errors() also echoes the rejected input values.
        fields = sorted({".".join(str(part) for part in err["loc"]) for err in exc.errors()})
        return jsonify({"error": "Invalid request", "fields": fields}), 400

    user_id = current_user_id()
    with get_repos() as repos:
        service = ExtensionTokenService(repos["extension_tokens"])
        active_count = sum(1 for token in service.list_for_user(user_id) if _is_active(token))
        if active_count >= _MAX_ACTIVE_TOKENS_PER_USER:
            return (
                jsonify(
                    {
                        "error": f"You already have {_MAX_ACTIVE_TOKENS_PER_USER} active "
                        "tokens. Revoke one you no longer use first."
                    }
                ),
                409,
            )
        issued = service.issue(user_id, body.label, expires_in_days=body.expires_in_days)
        record_event(repos, user_id, EXTENSION_TOKEN_CREATED, {"label": body.label})
        repos["conn"].commit()

    logger.info("Extension token %s issued for user %s", issued.record["id"], user_id)
    response = jsonify({"token": issued.plaintext, "record": issued.record})
    response.status_code = 201
    # The plaintext appears only in this response; keep it out of every cache.
    response.headers["Cache-Control"] = "no-store"
    return response


# ai: claude-sonnet-5 | 2026-09-26
@extension_tokens_bp.route("/extension-tokens/<int:token_id>", methods=["DELETE"])
def revoke_extension_token(token_id: int) -> ResponseReturnValue:
    user_id = current_user_id()
    with get_repos() as repos:
        revoked = ExtensionTokenService(repos["extension_tokens"]).revoke(user_id, token_id)
        if not revoked:
            return jsonify({"error": "Token not found"}), 404
        record_event(repos, user_id, EXTENSION_TOKEN_REVOKED, {"token_id": token_id})
        repos["conn"].commit()
    logger.info("Extension token %s revoked for user %s", token_id, user_id)
    return jsonify({"ok": True})
