# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-08
"""scripts/release.py and deploy/host-deploy.sh, the deploy pipeline (cloud plan 4.4).

These run real git (throwaway repositories with a bare "origin") and the real
host-deploy.sh under sh. Only docker is a stand-in: a script on PATH that records how it
was called and fails on request.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
HOST_SCRIPT = REPO_ROOT / "deploy" / "host-deploy.sh"
SH = shutil.which("sh") or "sh"
IMAGE = "registry.example.com/job-hunt-assistant:20261108-0123456789ab"

# Records every docker call; fails one whose arguments contain $DOCKER_FAIL_ON.
_FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "$DOCKER_LOG"
case "$*" in
  *"${DOCKER_FAIL_ON:-<never>}"*) exit 1 ;;
esac
"""
# Stands in for ssh: runs the remote command locally under sh, stdin passed through.
_FAKE_SSH = """import subprocess, sys
sys.exit(subprocess.run([sys.argv[1], "-c", sys.argv[3]]).returncode)
"""


@pytest.fixture(scope="module")
def release() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release", REPO_ROOT / "scripts" / "release.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolves annotations through it
    spec.loader.exec_module(module)
    return module


# ── git: which commit a release is ─────────────────────────────────────────────

# ai: claude-opus-5-5 | 2026-11-11
# Variables that point git at a particular repository. Git sets some of these for
# hooks (the pre-push gate runs this suite), and any of them overrides the cwd.
_INHERITED_REPO_LOCATION_VARS: tuple[str, ...] = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_QUARANTINE_PATH",
)


@pytest.fixture
def git_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic git: no global/system config (hooks, signing, autocrlf), fixed identity,
    and no inherited repository location."""
    # ai: claude-opus-5-5 | 2026-11-11 -- git exports GIT_DIR (and friends) to hooks; a
    # pre-push run from a worktree inherited them, so the temp repo's `git commit`
    # acted on the worktree's repository instead and every clone-based test errored.
    for name in _INHERITED_REPO_LOCATION_VARS:
        monkeypatch.delenv(name, raising=False)
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Release Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "release@example.invalid")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def clone(tmp_path: Path, git_env: None) -> Path:
    """A clone of a bare origin, with one pushed commit holding the deploy files."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "--initial-branch=main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", str(origin), str(work))
    (work / "deploy").mkdir()
    for name in ("docker-compose.prod.yml", "Caddyfile"):
        (work / "deploy" / name).write_text(f"{name} at first commit\n", encoding="utf-8")
    shutil.copy(HOST_SCRIPT, work / "deploy" / "host-deploy.sh")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "first")
    _git(work, "push", "origin", "HEAD:main")
    return work


def _commit(work: Path, message: str) -> str:
    _git(work, "commit", "--allow-empty", "-m", message)
    return _git(work, "rev-parse", "HEAD")


def test_a_pushed_commit_is_released_under_its_date_and_short_sha(release: ModuleType, clone: Path) -> None:
    repo = release.Repository(clone)
    repo.fetch()

    commit = repo.commit("origin/main")

    assert commit.sha == _git(clone, "rev-parse", "HEAD")
    assert re.fullmatch(rf"\d{{8}}-{commit.sha[:12]}", commit.version)


def test_a_commit_only_this_machine_has_is_refused(release: ModuleType, clone: Path) -> None:
    unpushed = _commit(clone, "not pushed")
    repo = release.Repository(clone)
    repo.fetch()

    with pytest.raises(release.ReleaseError, match="not on origin/main: push it first"):
        repo.commit(unpushed)


def test_a_tagged_commit_is_released_under_its_tag(release: ModuleType, clone: Path) -> None:
    _git(clone, "tag", "v1.4.0")
    repo = release.Repository(clone)

    assert repo.commit("v1.4.0").version == "v1.4.0"

    _git(clone, "tag", "v1.4.0-again")
    with pytest.raises(release.ReleaseError, match="several tags"):
        repo.commit("v1.4.0")


@pytest.mark.parametrize(
    "repository",
    ["registry.example.com/jha:1.0", "Registry.example.com/jha", "jha", "registry.example.com/jha@sha256:ab"],
)
def test_an_image_repository_with_a_tag_or_capitals_is_refused(release: ModuleType, repository: str) -> None:
    with pytest.raises(release.ReleaseError, match="not an image repository"):
        release.image_reference(repository, "20261108-0123456789ab")


def test_an_image_reference_is_the_repository_tagged_with_the_version(release: ModuleType) -> None:
    assert release.image_reference("localhost:5000/team/jha", "v1.4.0") == "localhost:5000/team/jha:v1.4.0"


# ── build ─────────────────────────────────────────────────────────────────────


class FakeDocker:
    def __init__(self, *, in_registry: bool = False, revision: str = "") -> None:
        self.in_registry = in_registry
        self.revision_label = revision
        self.built: list[tuple[Path, str, Any, str]] = []
        self.pushed: list[str] = []

    def exists_in_registry(self, image: str) -> bool:
        return self.in_registry

    def build(self, context: Path, image: str, commit: Any) -> None:
        # What a real `docker build` would read: the checkout as it is at build time.
        self.built.append((context, image, commit, _git(context, "rev-parse", "HEAD")))

    def push(self, image: str) -> str:
        self.pushed.append(image)
        return f"{image.rsplit(':', 1)[0]}@sha256:{'0' * 64}"

    def revision(self, image: str) -> str:
        return self.revision_label


def test_build_tests_and_builds_the_same_checkout_of_the_commit_then_removes_it(
    release: ModuleType, clone: Path
) -> None:
    sha = _git(clone, "rev-parse", "HEAD")
    (clone / "uncommitted.txt").write_text("must not reach the image", encoding="utf-8")
    tested: list[Path] = []

    def checks(worktree: Path) -> None:
        assert _git(worktree, "rev-parse", "HEAD") == sha
        assert not (worktree / "uncommitted.txt").exists()
        tested.append(worktree)

    docker = FakeDocker()
    image = release.Builder(release.Repository(clone), docker, checks).build("origin/main", "registry.example.com/jha")

    (context, built_image, commit, built_sha) = docker.built[0]
    assert tested == [context] and built_sha == sha and commit.sha == sha
    assert built_image == image == docker.pushed[0]
    assert not context.exists()
    assert sha not in _git(clone, "worktree", "list")


def test_a_failing_check_builds_and_pushes_nothing(release: ModuleType, clone: Path) -> None:
    def checks(worktree: Path) -> None:
        raise release.ReleaseError("pytest failed")

    docker = FakeDocker()
    with pytest.raises(release.ReleaseError, match="pytest failed"):
        release.Builder(release.Repository(clone), docker, checks).build("origin/main", "registry.example.com/jha")

    assert docker.built == [] and docker.pushed == []
    assert len(_git(clone, "worktree", "list").splitlines()) == 1


def test_a_tag_already_in_the_registry_is_never_rebuilt(release: ModuleType, clone: Path) -> None:
    def checks(worktree: Path) -> None:
        raise AssertionError("the tests must not run for a tag that can't be pushed")

    with pytest.raises(release.ReleaseError, match="never overwritten"):
        release.Builder(release.Repository(clone), FakeDocker(in_registry=True), checks).build(
            "origin/main", "registry.example.com/jha"
        )


def test_the_image_carries_the_commit_deploy_looks_up(release: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """The build arguments release.py passes are the ones the Dockerfile turns into the
    revision label (read back by `deploy`) and the error-tracking release."""
    calls: list[list[str]] = []
    monkeypatch.setattr(release, "run", lambda argv, **_: calls.append(argv) or "")

    release.Docker().build(Path("context"), IMAGE, release.Commit(sha="a" * 40, version="v1.4.0"))

    assert "APP_RELEASE=v1.4.0" in calls[0] and f"APP_REVISION={'a' * 40}" in calls[0]
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert f'{release.REVISION_LABEL}="${{APP_REVISION}}"' in dockerfile
    assert 'ENV SENTRY_RELEASE="${APP_RELEASE}"' in dockerfile


# ── host-deploy.sh, run for real with a stand-in docker ───────────────────────


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_bytes(_FAKE_DOCKER.encode("utf-8"))
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("DOCKER_LOG", str(log))
    monkeypatch.delenv("DOCKER_FAIL_ON", raising=False)
    return log


@pytest.fixture
def deploy_dir(tmp_path: Path) -> Path:
    """A server's deploy directory: its own secrets, plus this commit's deploy files."""
    directory = tmp_path / "srv" / "jha"
    (directory / "secrets" / "redis-tls").mkdir(parents=True)
    for name in (".env.production", ".env.migrate", ".env.backup", "secrets/redis-tls/ca.crt"):
        (directory / name).write_text("x\n", encoding="utf-8")
    for name in ("docker-compose.prod.yml", "Caddyfile", "host-deploy.sh"):
        shutil.copy(REPO_ROOT / "deploy" / name, directory / name)
    return directory


def _host_deploy(directory: Path, image: str = IMAGE) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed script
        [SH, str(directory / "host-deploy.sh"), image], capture_output=True, text=True, check=False
    )


def _calls(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_a_deploy_backs_up_then_migrates_then_starts_and_records_the_image(
    fake_docker: Path, deploy_dir: Path
) -> None:
    (deploy_dir / ".env.release").write_text("JHA_IMAGE=registry.example.com/job-hunt-assistant:old\n", encoding="utf-8")

    result = _host_deploy(deploy_dir)

    assert result.returncode == 0, result.stderr
    compose = "compose -f docker-compose.prod.yml --env-file .env.production --env-file .env.release.next"
    assert _calls(fake_docker) == [
        f"pull {IMAGE}",
        f"{compose} run --rm maintenance backup run",
        f"{compose} run --rm migrate",
        f"{compose} up -d --remove-orphans --wait --wait-timeout 300",
    ]
    assert (deploy_dir / ".env.release").read_text(encoding="utf-8") == f"JHA_IMAGE={IMAGE}\n"
    assert "job-hunt-assistant:old" in (deploy_dir / ".env.release.previous").read_text(encoding="utf-8")
    assert not (deploy_dir / ".env.release.next").exists()
    assert not (deploy_dir / ".deploy.lock").exists()


@pytest.mark.parametrize(
    ("fail_on", "ran", "says"),
    [
        ("backup run", 2, "nothing changed"),
        ("run --rm migrate", 3, "after the database was migrated"),
        ("up -d", 4, "roll back by deploying the image in .env.release"),
    ],
)
def test_a_failed_step_stops_the_deploy_and_keeps_the_recorded_image(
    fake_docker: Path, deploy_dir: Path, monkeypatch: pytest.MonkeyPatch, fail_on: str, ran: int, says: str
) -> None:
    (deploy_dir / ".env.release").write_text("JHA_IMAGE=old\n", encoding="utf-8")
    monkeypatch.setenv("DOCKER_FAIL_ON", fail_on)

    result = _host_deploy(deploy_dir)

    assert result.returncode != 0
    assert len(_calls(fake_docker)) == ran
    assert says in result.stderr
    assert (deploy_dir / ".env.release").read_text(encoding="utf-8") == "JHA_IMAGE=old\n"
    assert not (deploy_dir / ".env.release.next").exists()
    assert not (deploy_dir / ".deploy.lock").exists()


def test_a_second_deploy_waits_for_the_first(fake_docker: Path, deploy_dir: Path) -> None:
    (deploy_dir / ".deploy.lock").mkdir()

    result = _host_deploy(deploy_dir)

    assert result.returncode != 0 and "another deploy is running" in result.stderr
    assert _calls(fake_docker) == []
    assert (deploy_dir / ".deploy.lock").exists()  # the running deploy's lock is left alone


def test_a_server_missing_its_own_files_deploys_nothing(fake_docker: Path, deploy_dir: Path) -> None:
    (deploy_dir / ".env.backup").unlink()

    result = _host_deploy(deploy_dir)

    assert result.returncode != 0 and ".env.backup is missing" in result.stderr
    assert _calls(fake_docker) == []


def test_the_compose_file_takes_the_image_from_the_release_file() -> None:
    """host-deploy.sh writes JHA_IMAGE to a release file, not .env.production: every
    application service must read its image from that variable."""
    services = yaml.safe_load((REPO_ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8"))["services"]
    images = {name: svc["image"] for name, svc in services.items() if name not in {"caddy", "redis"}}
    assert images and all(image.startswith("${JHA_IMAGE") for image in images.values()), images
    example = (REPO_ROOT / "deploy" / ".env.production.example").read_text(encoding="utf-8").splitlines()
    assert not [line for line in example if line.startswith(("JHA_IMAGE=", "SENTRY_RELEASE="))]


def test_host_deploy_keeps_lf_line_endings() -> None:
    attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "deploy/host-deploy.sh text eol=lf" in attributes
    assert b"\r\n" not in HOST_SCRIPT.read_bytes()


# ── deploy: the image's commit's files reach the server, then host-deploy runs ──


@pytest.fixture
def fake_ssh(tmp_path: Path) -> list[str]:
    script = tmp_path / "fake_ssh.py"
    script.write_text(_FAKE_SSH, encoding="utf-8")
    return [sys.executable, str(script), SH]


def test_deploy_uploads_the_images_own_commits_files_and_runs_them(
    release: ModuleType, clone: Path, fake_docker: Path, fake_ssh: list[str], deploy_dir: Path
) -> None:
    deployed_sha = _git(clone, "rev-parse", "HEAD")
    # A later pushed commit changes the compose file; the deploy must not use it.
    (clone / "deploy" / "docker-compose.prod.yml").write_text("a later compose file\n", encoding="utf-8")
    _git(clone, "commit", "-am", "later")
    _git(clone, "push", "origin", "HEAD:main")
    for name in ("docker-compose.prod.yml", "Caddyfile", "host-deploy.sh"):
        (deploy_dir / name).unlink()

    release.Deployer(release.Repository(clone), FakeDocker(revision=deployed_sha), fake_ssh).deploy(
        IMAGE, "deploy@server", deploy_dir.as_posix()
    )

    compose_file = (deploy_dir / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert compose_file == "docker-compose.prod.yml at first commit\n"
    assert (deploy_dir / "host-deploy.sh").read_bytes() == HOST_SCRIPT.read_bytes()
    assert _calls(fake_docker)[0] == f"pull {IMAGE}"
    assert (deploy_dir / ".env.release").read_text(encoding="utf-8") == f"JHA_IMAGE={IMAGE}\n"
    assert not list(deploy_dir.glob(".*.upload"))


def test_an_image_from_an_unpushed_commit_is_not_deployed(
    release: ModuleType, clone: Path, fake_docker: Path, fake_ssh: list[str], deploy_dir: Path
) -> None:
    unpushed = _commit(clone, "not pushed")

    with pytest.raises(release.ReleaseError, match="push it first"):
        release.Deployer(release.Repository(clone), FakeDocker(revision=unpushed), fake_ssh).deploy(
            IMAGE, "deploy@server", deploy_dir.as_posix()
        )
    assert _calls(fake_docker) == []


def test_remote_commands_survive_awkward_paths(release: ModuleType, tmp_path: Path) -> None:
    directory = (tmp_path / "it's a dir; rm -rf x").as_posix()

    subprocess.run(  # noqa: S603 - the command under test
        [SH, "-c", release.remote_upload_command(directory, "Caddyfile")], input=b"contents", check=True
    )

    assert Path(directory, "Caddyfile").read_bytes() == b"contents"
    assert release.remote_deploy_command(directory, IMAGE).startswith("sh '")
