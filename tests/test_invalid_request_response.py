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

# ai: claude-sonnet-5 | 2026-09-29
"""backend.utils.utils.invalid_request_response -- the shared 400 helper every
route's pydantic.ValidationError catch block funnels through."""

from __future__ import annotations

from flask import Flask
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from backend.utils.utils import invalid_request_response


class _SingleFieldModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""

    @field_validator("name")
    @classmethod
    def _require_name(cls, value: str) -> str:
        if not value:
            raise ValueError("name is required")
        return value


class _MultiFieldModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    label: str


def test_single_value_error_surfaces_its_own_message() -> None:
    app = Flask(__name__)
    with app.app_context(), app.test_request_context():
        try:
            _SingleFieldModel.model_validate({"name": ""})
        except ValidationError as exc:
            response, status = invalid_request_response(exc)
        else:
            raise AssertionError("expected ValidationError")

    assert status == 400
    assert response.get_json() == {"error": "name is required"}


def test_multiple_errors_fall_back_to_field_list() -> None:
    app = Flask(__name__)
    with app.app_context(), app.test_request_context():
        try:
            _MultiFieldModel.model_validate({})
        except ValidationError as exc:
            response, status = invalid_request_response(exc)
        else:
            raise AssertionError("expected ValidationError")

    assert status == 400
    body = response.get_json()
    assert body["error"] == "Invalid request"
    assert sorted(body["fields"]) == ["count", "label"]


def test_unexpected_field_falls_back_to_field_list() -> None:
    app = Flask(__name__)
    with app.app_context(), app.test_request_context():
        try:
            _SingleFieldModel.model_validate({"name": "ok", "extra": "nope"})
        except ValidationError as exc:
            response, status = invalid_request_response(exc)
        else:
            raise AssertionError("expected ValidationError")

    assert status == 400
    body = response.get_json()
    assert body["error"] == "Invalid request"
    assert body["fields"] == ["extra"]
