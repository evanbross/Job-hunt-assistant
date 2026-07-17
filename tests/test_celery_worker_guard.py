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
