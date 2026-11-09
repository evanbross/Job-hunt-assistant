# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-09
"""Record each background task that raises against the account it ran for (plan 4.5),
so an admin can see an account's failures (Settings > System > Accounts) without
reading worker logs.

Every per-account task takes the account as its `user_id` argument; a task without one
(the scheduler ticks, the sweep, backups) isn't recorded here -- its failures reach the
error tracker and its cron monitor. The message is redacted and capped exactly like an
error-tracker event (backend/error_tracking.py), since an exception can quote a job
description or an email address.

Recording is best effort: a failure to record is logged, never raised, so it can't hide
the task's own failure."""

from __future__ import annotations

import inspect
import logging
from typing import Any

import psycopg2
from celery import Task
from celery.signals import task_failure

from backend.error_tracking import redact
from backend.utils.utils import DatabaseUnavailableError, get_repos

logger = logging.getLogger(__name__)

USER_ARGUMENT = "user_id"


def task_user_id(task: Task, args: tuple[Any, ...], kwargs: dict[str, Any]) -> int | None:
    """The account a task call ran for: its `user_id` argument, passed positionally or by
    name. None for a task that has no such argument."""
    try:
        bound = inspect.signature(task.run).bind_partial(*args, **kwargs)
    except TypeError:
        return None
    user_id = bound.arguments.get(USER_ARGUMENT)
    return user_id if isinstance(user_id, int) and not isinstance(user_id, bool) else None


def record_task_failure(task: Task, task_id: str | None, exception: BaseException, args: Any, kwargs: Any) -> bool:
    """Store one failure for the task's account. True if a row was written."""
    user_id = task_user_id(task, tuple(args or ()), dict(kwargs or {}))
    if user_id is None:
        return False
    try:
        with get_repos(user_id) as repos:
            repos["task_failures"].insert(
                user_id, task.name, task_id, type(exception).__name__, redact(str(exception))
            )
            repos["conn"].commit()
    except (psycopg2.Error, DatabaseUnavailableError) as exc:
        logger.warning("Could not record the failure of %s for user %s: %s", task.name, user_id, exc)
        return False
    return True


@task_failure.connect
def _on_task_failure(
    sender: Task | None = None,
    task_id: str | None = None,
    exception: BaseException | None = None,
    args: Any = None,
    kwargs: Any = None,
    **_: Any,
) -> None:
    if sender is not None and exception is not None:
        record_task_failure(sender, task_id, exception, args, kwargs)
