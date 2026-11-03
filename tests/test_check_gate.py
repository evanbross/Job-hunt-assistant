# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-03
"""scripts/check.py, the local stand-in for the disabled CI workflow. These run
against the real .gitlab-ci.yml, so an edit to a CI job that the gate no
longer mirrors fails here."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def gate_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_gate", REPO_ROOT / "scripts" / "check.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ci(gate_module: ModuleType) -> object:
    return gate_module.CiConfig()


def test_every_gate_command_is_in_its_ci_jobs_script(gate_module: ModuleType, ci: object) -> None:
    for check in gate_module.CHECKS:
        script = ci.script_lines(check.ci_job)
        for command in check.commands:
            wanted = " ".join(command)
            assert any(wanted in line for line in script), (
                f"{check.ci_job}: `{wanted}` is not in the CI job's script {script}"
            )


def test_every_ci_job_with_a_script_is_mirrored_by_the_gate(gate_module: ModuleType, ci: object) -> None:
    """A new CI quality job needs a gate check too -- or an explicit exemption here."""
    not_run_locally = {"pip-audit", "npm-audit"}  # need network; advisory-only in CI anyway
    gate_jobs = {check.ci_job for check in gate_module.CHECKS}
    assert ci.job_names() - not_run_locally == gate_jobs


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("backend/db/repositories/jobs.py", "backend/**/*", True),
        ("backend/app.py", "backend/**/*", True),
        ("app.py", "*.py", True),
        ("backend/app.py", "*.py", False),  # `*` stays within one segment, as in GitLab
        (".gitignore", ".gitignore", True),
        ("frontend/src/utils/triagePanelUtils.ts", "frontend/src/utils/triagePanelUtils.ts", True),
        ("docs/notes.md", "backend/**/*", False),
    ],
)
def test_matches_any_follows_gitlab_changes_globs(
    gate_module: ModuleType, path: str, pattern: str, expected: bool
) -> None:
    assert gate_module.matches_any(path, [pattern]) is expected


@pytest.mark.parametrize(
    ("changed", "expected_jobs"),
    [
        ({"browser-extension/content.js"}, ["extension"]),
        ({"frontend/src/App.jsx"}, ["frontend"]),
        ({"backend/user_events.py"}, ["ruff", "pytest"]),
        ({"shared/skip_reasons.json"}, ["ruff", "frontend", "pytest"]),
        ({"docs/code-review-2026-11-02-triage.md"}, []),
        ({".gitlab-ci.yml"}, ["ruff", "frontend", "extension", "pytest"]),
        (None, ["ruff", "frontend", "extension", "pytest"]),
    ],
)
def test_selected_checks_follow_the_ci_rules(
    gate_module: ModuleType, ci: object, changed: set[str] | None, expected_jobs: list[str]
) -> None:
    gate = gate_module.Gate(ci)
    assert [check.ci_job for check in gate.selected(changed)] == expected_jobs


def test_pre_push_new_branch_runs_everything(gate_module: ModuleType) -> None:
    line = f"refs/heads/evan-x {'a' * 40} refs/heads/evan-x {'0' * 40}"
    assert gate_module.ChangedFiles.for_pre_push([line]) is None


def test_pre_push_branch_delete_checks_nothing(gate_module: ModuleType) -> None:
    line = f"(delete) {'0' * 40} refs/heads/evan-x {'b' * 40}"
    assert gate_module.ChangedFiles.for_pre_push([line]) == set()


def test_pre_push_unknown_remote_sha_runs_everything(gate_module: ModuleType) -> None:
    line = f"refs/heads/main {'a' * 40} refs/heads/main {'c' * 40}"
    assert gate_module.ChangedFiles.for_pre_push([line]) is None
