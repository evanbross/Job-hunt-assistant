# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-03
"""Local quality gate: the same checks the GitLab CI jobs run, run here
instead, because the CI workflow is disabled to save shared-runner minutes
(see the top of .gitlab-ci.yml).

Which checks run is decided by the CI jobs' own `rules: changes:` path lists,
read from .gitlab-ci.yml, so this gate and CI can never disagree about what a
change touches. The commands themselves are listed below; tests/
test_check_gate.py fails if one stops matching its CI job's script.

    uv run python scripts/check.py           # checks for what differs from upstream
    uv run python scripts/check.py --all     # every check
    (pre-push)  .githooks/pre-push           # enable: git config core.hooksPath .githooks

The checks run against the working tree. With --pre-push that is not
necessarily what is being pushed, so uncommitted changes produce a warning.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_FILE = REPO_ROOT / ".gitlab-ci.yml"
_ZERO_SHA = "0" * 40
# Python tools run inside the project environment, exactly as uv.lock pins it.
_UV_RUN = ("uv", "run", "--frozen")


@dataclass(frozen=True)
class Check:
    """One CI job's checks: *ci_job* names the job whose `rules: changes:`
    decide whether this runs, and whose script must contain each of
    *commands* (minus *runner*)."""

    ci_job: str
    cwd: Path
    commands: tuple[tuple[str, ...], ...]
    runner: tuple[str, ...] = ()

    def argv(self, command: tuple[str, ...]) -> list[str]:
        return [*self.runner, *command]


# Fastest first: a push stops at the first failure, before pytest's minutes.
# npm ci is deliberately not run -- that's CI's clean install; locally the
# installed node_modules is what's being tested.
CHECKS: tuple[Check, ...] = (
    Check("ruff", REPO_ROOT, (("ruff", "check", "."),), runner=_UV_RUN),
    Check(
        "frontend",
        REPO_ROOT / "frontend",
        (
            ("npm", "run", "lint:ci"),
            ("npm", "run", "type-check"),
            ("npm", "run", "test"),
            ("npm", "run", "build"),
        ),
    ),
    Check("extension", REPO_ROOT / "browser-extension", (("npm", "test"),)),
    Check("pytest", REPO_ROOT, (("pytest", "-q"),), runner=_UV_RUN),
)


class GateError(Exception):
    """The gate itself can't run (bad CI file, missing tool) -- not a failed check."""


class CiConfig:
    """The parts of .gitlab-ci.yml the gate reads."""

    def __init__(self, path: Path = CI_FILE) -> None:
        with path.open(encoding="utf-8") as handle:
            self._config: dict[str, Any] = yaml.safe_load(handle)

    def _job(self, name: str) -> dict[str, Any]:
        job = self._config.get(name)
        if not isinstance(job, dict):
            raise GateError(f"{CI_FILE.name} has no job named {name!r}")
        return job

    def change_patterns(self, job_name: str) -> list[str]:
        """Every path pattern in the job's `rules: changes:` (anchors resolved)."""
        patterns: list[str] = []
        for rule in self._job(job_name).get("rules", []):
            changes = rule.get("changes")
            if changes is None:
                continue
            if not isinstance(changes, list):
                # The {paths:, compare_to:} form isn't used here; fail loudly if it appears.
                raise GateError(f"{job_name}: only list-form `changes:` is supported")
            patterns.extend(str(pattern) for pattern in changes)
        if not patterns:
            raise GateError(f"{job_name}: no `rules: changes:` paths to decide when it runs")
        return patterns

    def job_names(self) -> set[str]:
        """Every real job (one with a script), not the hidden `.anchor` keys."""
        return {
            name
            for name, job in self._config.items()
            if not name.startswith(".") and isinstance(job, dict) and "script" in job
        }

    def script_lines(self, job_name: str) -> list[str]:
        return [" ".join(str(line).split()) for line in self._job(job_name).get("script", [])]


def matches_any(path: str, patterns: Iterable[str]) -> bool:
    """GitLab's `changes:` glob semantics: `*` stays within one path segment,
    `**` spans any number of them, dotfiles are not special."""
    pure = PurePosixPath(path)
    return any(pure.full_match(pattern) for pattern in patterns)


class ChangedFiles:
    """Which files a run should consider -- or None, meaning 'run everything'."""

    @staticmethod
    def _git(*args: str) -> list[str] | None:
        result = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            return None
        return [line for line in result.stdout.splitlines() if line]

    @classmethod
    def for_pre_push(cls, stdin_lines: Iterable[str]) -> set[str] | None:
        """git's pre-push stdin: '<local ref> <local sha> <remote ref> <remote sha>'
        per ref being pushed."""
        changed: set[str] = set()
        for line in stdin_lines:
            parts = line.split()
            if len(parts) != 4:
                continue
            _local_ref, local_sha, _remote_ref, remote_sha = parts
            if local_sha == _ZERO_SHA:
                continue  # deleting a remote branch; nothing to check
            if remote_sha == _ZERO_SHA:
                return None  # new branch: no remote base to diff against
            names = cls._git("diff", "--name-only", remote_sha, local_sha)
            if names is None:
                return None  # remote sha not fetched locally; be safe
            changed.update(names)
        return changed

    @classmethod
    def against_upstream(cls) -> set[str] | None:
        """Committed-but-unpushed plus uncommitted (including untracked) changes."""
        tracked = cls._git("diff", "--name-only", "@{upstream}")
        if tracked is None:
            return None  # no upstream configured
        untracked = cls._git("ls-files", "--others", "--exclude-standard") or []
        return set(tracked) | set(untracked)

    @classmethod
    def working_tree_dirty(cls) -> bool:
        return bool(cls._git("status", "--porcelain", "--untracked-files=no"))


class Gate:
    def __init__(self, ci: CiConfig, checks: tuple[Check, ...] = CHECKS) -> None:
        self._ci = ci
        self._checks = checks

    def selected(self, changed: set[str] | None) -> list[Check]:
        if changed is None:
            return list(self._checks)
        return [
            check
            for check in self._checks
            if any(matches_any(path, self._ci.change_patterns(check.ci_job)) for path in changed)
        ]

    @staticmethod
    def _resolve(argv: list[str]) -> list[str]:
        # shutil.which finds npm.cmd on Windows, which a bare "npm" would not.
        executable = shutil.which(argv[0])
        if executable is None:
            raise GateError(f"{argv[0]} is not on PATH")
        return [executable, *argv[1:]]

    def run(self, checks: list[Check]) -> bool:
        for check in checks:
            for command in check.commands:
                argv = check.argv(command)
                print(f"\n=== [{check.ci_job}] {' '.join(argv)}", flush=True)
                result = subprocess.run(self._resolve(argv), cwd=check.cwd, check=False)
                if result.returncode != 0:
                    print(f"\nFAILED [{check.ci_job}]: {' '.join(argv)}", file=sys.stderr)
                    return False
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="run every check")
    mode.add_argument(
        "--pre-push",
        action="store_true",
        help="read git's pre-push ref list from stdin (used by .githooks/pre-push)",
    )
    # git passes the remote's name and URL to a pre-push hook; accepted and ignored.
    parser.add_argument("hook_args", nargs="*", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.all:
        changed = None
    elif args.pre_push:
        changed = ChangedFiles.for_pre_push(sys.stdin)
        if ChangedFiles.working_tree_dirty():
            print(
                "warning: uncommitted changes -- checks run against the working tree,"
                " not exactly what is being pushed",
                file=sys.stderr,
            )
    else:
        changed = ChangedFiles.against_upstream()

    try:
        gate = Gate(CiConfig())
        checks = gate.selected(changed)
        if not checks:
            print("check: nothing to check for these changes")
            return 0
        scope = "everything" if changed is None else f"{len(changed)} changed file(s)"
        print(f"check: {', '.join(c.ci_job for c in checks)} ({scope})")
        ok = gate.run(checks)
    except GateError as exc:
        print(f"check: cannot run the gate: {exc}", file=sys.stderr)
        return 2
    if ok:
        print("\ncheck: all checks passed")
        return 0
    if args.pre_push:
        print("Push blocked. Fix the failure, or `git push --no-verify` to override.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
