# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-08
"""One log format (backend/logging_setup.py, cloud plan 4.3), used by every process."""

from __future__ import annotations

from pathlib import Path

from backend.celery_app import celery_app
from backend.logging_setup import CELERY_TASK_LOG_FORMAT, LOG_FORMAT

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SOURCE_DIRS = ("backend", "routes", "web", "scripts")
_TOP_LEVEL = ("app.py", "wsgi.py", "launcher.py", "config.py", "gunicorn.conf.py")


def test_the_format_says_where_each_line_came_from() -> None:
    for fmt in (LOG_FORMAT, CELERY_TASK_LOG_FORMAT):
        assert "%(filename)s:%(lineno)d" in fmt and "%(levelname)s" in fmt and "%(asctime)s" in fmt


def test_celery_workers_log_in_the_same_format() -> None:
    assert celery_app.conf.worker_log_format == LOG_FORMAT
    assert celery_app.conf.worker_task_log_format == CELERY_TASK_LOG_FORMAT


def test_no_module_keeps_its_own_copy_of_the_format() -> None:
    sources = [p for d in _SOURCE_DIRS for p in (_REPO_ROOT / d).rglob("*.py")]
    sources += [_REPO_ROOT / name for name in _TOP_LEVEL]
    copies = [
        str(p.relative_to(_REPO_ROOT)) for p in sources
        if p.name != "logging_setup.py" and "%(filename)s:%(lineno)d" in p.read_text(encoding="utf-8")
    ]

    assert copies == []
