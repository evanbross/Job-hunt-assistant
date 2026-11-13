# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""Stand-ins for Celery dispatch and task state, for tests of who may start a run."""

from __future__ import annotations

from types import SimpleNamespace

from backend.active_tasks import ActiveTasks, MemoryActiveTaskStore, set_active_tasks


class RecordedDelays:
    """Replaces a Celery task's `.delay`: records each call, returns task-1, task-2, ..."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args: object, **kwargs: object) -> SimpleNamespace:
        self.calls.append((args, kwargs))
        return SimpleNamespace(id=f"task-{len(self.calls)}")


def install_everything_running_registry() -> ActiveTasks:
    """A registry (shared, as Redis is) whose every recorded task is still running.
    conftest's isolated_active_tasks fixture clears it after the test."""
    active_tasks = ActiveTasks(MemoryActiveTaskStore(), task_state=lambda task_id: "STARTED")
    set_active_tasks(active_tasks)
    return active_tasks
