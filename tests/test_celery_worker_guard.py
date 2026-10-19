# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-fable-5 | 2026-07-16
"""Tests for the duplicate-Celery-worker guard used by app.py at startup."""

from __future__ import annotations

from unittest.mock import patch

from celery.exceptions import OperationalError

from backend.celery_app import worker_is_running


def test_worker_is_running_true_when_a_worker_replies() -> None:
    with patch(
        "backend.celery_app.celery_app.control.ping",
        return_value=[{"celery@Sprout": {"ok": "pong"}}],
    ):
        assert worker_is_running() is True


def test_worker_is_running_false_when_no_worker_replies() -> None:
    with patch("backend.celery_app.celery_app.control.ping", return_value=[]):
        assert worker_is_running() is False


def test_worker_is_running_false_when_broker_unreachable() -> None:
    """A broker outage must not crash startup — the caller falls back to spawning."""
    with patch(
        "backend.celery_app.celery_app.control.ping",
        side_effect=OperationalError("connection refused"),
    ):
        assert worker_is_running() is False
