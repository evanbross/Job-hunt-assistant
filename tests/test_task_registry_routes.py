# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""Every per-account "Run" button checks the shared registry (backend/active_tasks.py),
so a second web worker can't start a run the first one already started
(docs/cloud-deployment-readiness-plan.md 1.5)."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Blueprint, Flask

import routes.applications as applications
import routes.board_search as board_search
import routes.fit as fit
import routes.scan as scan
from backend.celery_app import celery_app
from tests.task_doubles import RecordedDelays, install_everything_running_registry

_USER = 1


_SUBMITTERS: dict[str, tuple[Any, str, str, Callable[[], str | None]]] = {
    "email scan": (
        scan.run_scan_task, "SCAN_TASK_KIND", "scan", lambda: scan._submit_scan(_USER)
    ),
    "board search": (
        board_search.run_board_search_task,
        "BOARD_SEARCH_TASK_KIND",
        "board_search",
        lambda: board_search._submit_board_search(_USER),
    ),
    "re-evaluate": (
        fit.run_re_evaluate_task,
        "RE_EVALUATE_TASK_KIND",
        "fit",
        lambda: fit._submit_re_evaluate(_USER),
    ),
    "closed-req scan": (
        applications.run_req_closed_scan_task,
        "CLOSED_REQ_SCAN_TASK_KIND",
        "applications",
        lambda: applications._submit_closed_req_scan(_USER, frozenset({"linkedin"})),
    ),
}
_MODULES = {
    "scan": scan,
    "board_search": board_search,
    "fit": fit,
    "applications": applications,
}


@pytest.mark.parametrize("name", list(_SUBMITTERS))
def test_a_run_another_worker_started_blocks_a_second(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    task, kind_attr, module_name, submit = _SUBMITTERS[name]
    active_tasks = install_everything_running_registry()
    delays = RecordedDelays()
    monkeypatch.setattr(task, "delay", delays)

    first = submit()
    second = submit()

    assert first == "task-1"
    assert second is None
    assert len(delays.calls) == 1
    # Recorded where the status route (on any worker) looks for it.
    record = active_tasks.latest(getattr(_MODULES[module_name], kind_attr), _USER)
    assert record is not None and record.task_id == "task-1"


def test_the_kinds_are_distinct() -> None:
    """Two kinds sharing a name would block each other's runs."""
    kinds = [getattr(_MODULES[module], attr) for _task, attr, module, _submit in _SUBMITTERS.values()]
    assert len(set(kinds)) == len(kinds)


def test_rescan_all_does_not_clear_results_under_a_running_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rescan-all deletes the account's email results before its scan. Doing that
    while another scan is writing them would lose that scan's work."""
    install_everything_running_registry()
    monkeypatch.setattr(scan.run_scan_task, "delay", RecordedDelays())
    cleared: list[str] = []
    scan._submit_scan(_USER)  # a scan another tab or worker started

    result = scan._submit_scan(_USER, rescan_all=True, prepare=lambda: cleared.append("cleared"))

    assert result is None
    assert cleared == []


def test_rescan_all_clears_before_its_scan_is_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    install_everything_running_registry()
    order: list[str] = []

    def delay(*_args: object, **_kwargs: object) -> SimpleNamespace:
        order.append("queued")
        return SimpleNamespace(id="task-1")

    monkeypatch.setattr(scan.run_scan_task, "delay", delay)

    scan._submit_scan(_USER, rescan_all=True, prepare=lambda: order.append("cleared"))

    assert order == ["cleared", "queued"]


# ─── runs that used to be one in-process thread shared by every account ────────

_THREAD_RUNS_NOW_TASKS = {
    "WTTJ repair": ("routes.jobs", "run_wttj_repair_task", "/api/jobs/repair-wttj-urls", {}),
    "broken-import rescan": (
        "routes.jobs",
        "run_rescan_broken_imports_task",
        "/api/jobs/rescan-broken-imports",
        {},
    ),
    "import repair": ("routes.jobs", "run_import_repair_task", "/api/jobs/repair-import-issues", {}),
}


def _client_for(module_name: str, monkeypatch: pytest.MonkeyPatch, user: dict[str, int]) -> Any:
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "current_user_id", lambda: user["id"])
    app = Flask(__name__)
    blueprint = next(v for k, v in vars(module).items() if k.endswith("_bp") and isinstance(v, Blueprint))
    app.register_blueprint(blueprint, url_prefix="/api")
    return app.test_client()


@pytest.mark.parametrize("name", list(_THREAD_RUNS_NOW_TASKS))
def test_a_second_start_is_refused_but_another_account_can_start_its_own(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """These ran as one thread per web process with one shared state: a second web
    worker could start a duplicate, and one account's run blocked every other's."""
    module_name, task_name, path, body = _THREAD_RUNS_NOW_TASKS[name]
    install_everything_running_registry()
    delays = RecordedDelays()
    monkeypatch.setattr(getattr(importlib.import_module(module_name), task_name), "delay", delays)
    user = {"id": 1}
    client = _client_for(module_name, monkeypatch, user)

    assert client.post(path, json=body).status_code == 200
    assert client.post(path, json=body).status_code == 409
    user["id"] = 2
    assert client.post(path, json=body).status_code == 200

    assert [args[0] for args, _kwargs in delays.calls] == [1, 2]


def test_the_worker_registers_every_task_these_routes_queue() -> None:
    """A task whose module isn't in celery_app's include list is queued and never run."""
    celery_app.loader.import_default_modules()
    queued = [task for task, _attr, _module, _submit in _SUBMITTERS.values()] + [
        getattr(importlib.import_module(module), task)
        for module, task, _path, _body in _THREAD_RUNS_NOW_TASKS.values()
    ]

    assert {task.name for task in queued} <= set(celery_app.tasks)
