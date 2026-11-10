# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-10
"""Every string in a JSON request body is trimmed before route code sees it,
except passwords (web/request_input.py)."""

from __future__ import annotations

from flask import Flask, request

from web.request_input import trim_user_input


def test_strings_are_trimmed_at_every_depth() -> None:
    body = {
        "host": " smtp.example.com ",
        "tags": ["  remote", "senior  "],
        "contact": {"name": "\tAda Lovelace\n", "notes": "  line one\nline two  "},
        "count": 3,
        "enabled": True,
        "missing": None,
    }

    assert trim_user_input(body) == {
        "host": "smtp.example.com",
        "tags": ["remote", "senior"],
        "contact": {"name": "Ada Lovelace", "notes": "line one\nline two"},
        "count": 3,
        "enabled": True,
        "missing": None,
    }


def test_passwords_are_kept_exactly_as_typed() -> None:
    body = {
        "password": " leading space",
        "current_password": "trailing ",
        "new_password": " both ",
        "smtp_password": " relay ",
        "smtp_user": " relay-user ",
    }

    assert trim_user_input(body) == {
        "password": " leading space",
        "current_password": "trailing ",
        "new_password": " both ",
        "smtp_password": " relay ",
        "smtp_user": "relay-user",
    }


def test_a_bare_string_body_is_trimmed() -> None:
    assert trim_user_input("  hello  ") == "hello"


def test_the_app_hands_routes_trimmed_json(flask_app: Flask) -> None:
    """The real app, so this fails if web/factory.py stops installing the request class."""
    with flask_app.test_request_context(
        "/api/anything", method="POST", json={"company": " Acme ", "new_password": " keep "}
    ):
        assert request.get_json() == {"company": "Acme", "new_password": " keep "}
        assert request.json == {"company": "Acme", "new_password": " keep "}
