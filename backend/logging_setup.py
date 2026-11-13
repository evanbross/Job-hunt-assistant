# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-08
"""The one log format and the one way processes set up logging (cloud plan 4.3).

Every process logs to stdout in this format: the container runtime (or the desktop
launcher) collects stdout, so nothing writes log files of its own.
"""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
# Celery workers: the same, plus which task run a line came from.
CELERY_TASK_LOG_FORMAT = (
    "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d)"
    " [%(task_name)s(%(task_id)s)]: %(message)s"
)


def configure_logging(level: int = logging.INFO) -> None:
    """Root logger to stdout in LOG_FORMAT. Call once, from a process entry point."""
    logging.basicConfig(level=level, format=LOG_FORMAT, stream=sys.stdout)
