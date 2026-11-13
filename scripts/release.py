# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-08
"""Build, publish and deploy a release (docs/cloud-deployment-readiness-plan.md 4.4).

CI is off (see the top of .gitlab-ci.yml), so releases are built here, the same way
the pre-push gate runs here:

    uv run python scripts/release.py build [REF] --repository registry.example.com/job-hunt-assistant
    uv run python scripts/release.py deploy IMAGE --host deploy@server.example.com [--dir /opt/jha]

`build` checks out REF (default origin/main) into a fresh worktree, runs every check
(scripts/check.py --all) there, builds the image from that same checkout, and pushes
it. So the image is exactly a commit that is on origin/main and passed the tests, and
nothing uncommitted in this directory can reach it. A tag already in the registry is
never overwritten.

`deploy` reads the image's commit from its label, uploads that commit's compose file,
Caddyfile and deploy/host-deploy.sh to the server, and runs host-deploy.sh there:
pull, back up, migrate, start and wait for health. Rolling back is deploying the
previous image (the server keeps its name in .env.release.previous).

JHA_IMAGE_REPOSITORY, JHA_DEPLOY_HOST and JHA_DEPLOY_DIR can stand in for the options.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_REF = "origin/main"
REVISION_LABEL = "org.opencontainers.image.revision"
DEFAULT_DEPLOY_DIR = "/opt/jha"
# What the server needs from the image's commit besides the image itself. The secrets
# beside them (.env.*, secrets/) are the server's own and are never uploaded.
DEPLOY_FILES: tuple[str, ...] = ("deploy/docker-compose.prod.yml", "deploy/Caddyfile", "deploy/host-deploy.sh")
HOST_SCRIPT = "host-deploy.sh"
# Node projects the full gate tests; a fresh worktree has no node_modules.
NODE_PROJECTS: tuple[str, ...] = ("frontend", "browser-extension")

_SHA = re.compile(r"[0-9a-f]{40}")
# registry/path/name: lower-case path components, no tag or digest (the version is the tag).
_REPOSITORY = re.compile(r"[a-z0-9.\-]+(?::\d+)?(?:/[a-z0-9]+(?:[._\-][a-z0-9]+)*)+")


class ReleaseError(RuntimeError):
    """A release step refused or failed; the message says which and why."""


def run(argv: list[str], *, cwd: Path = REPO_ROOT, capture: bool = False, stdin: bytes | None = None) -> str:
    """Run a command; raise ReleaseError naming it if it fails. Windows needs the
    resolved path for .cmd shims such as npm."""
    executable = shutil.which(argv[0]) or argv[0]
    result = subprocess.run(  # noqa: S603 - fixed tools, arguments are ours
        [executable, *argv[1:]], cwd=cwd, input=stdin, capture_output=capture, check=False
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip() if capture else ""
        raise ReleaseError(f"`{' '.join(argv)}` failed (exit {result.returncode}){': ' + detail if detail else ''}")
    return result.stdout.decode("utf-8", "replace").strip() if capture else ""


@dataclass(frozen=True)
class Commit:
    """A commit on origin/main, and the version its image is tagged with."""

    sha: str
    version: str


class Repository:
    """The git side: which commit a release is, and the files it deploys."""

    def __init__(self, root: Path = REPO_ROOT, main_ref: str = MAIN_REF) -> None:
        self.root = root
        self.main_ref = main_ref

    def _git(self, *args: str) -> str:
        return run(["git", *args], cwd=self.root, capture=True)

    def fetch(self) -> None:
        remote = self.main_ref.split("/", 1)[0]
        self._git("fetch", "--quiet", "--tags", remote)

    def commit(self, ref: str) -> Commit:
        """Resolve *ref*, refusing a commit that isn't on origin/main: a deployed image
        must be a commit anyone can check out, not something only this machine has."""
        sha = self._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        self.require_on_main(sha)
        return Commit(sha=sha, version=self.version(sha))

    def require_on_main(self, sha: str) -> None:
        result = subprocess.run(  # noqa: S603 - fixed git arguments
            ["git", "merge-base", "--is-ancestor", sha, self.main_ref], cwd=self.root, capture_output=True, check=False
        )
        if result.returncode == 1:
            raise ReleaseError(f"{sha[:12]} is not on {self.main_ref}: push it first")
        if result.returncode != 0:
            raise ReleaseError(f"can't tell whether {sha[:12]} is on {self.main_ref}: {result.stderr.decode().strip()}")

    def version(self, sha: str) -> str:
        """The commit's tag if it has exactly one, else <commit date, UTC>-<short sha>."""
        tags = self._git("tag", "--points-at", sha).split()
        if len(tags) == 1:
            return tags[0]
        if len(tags) > 1:
            raise ReleaseError(f"{sha[:12]} has several tags ({', '.join(tags)}); release it by one of them")
        committed = datetime.fromtimestamp(int(self._git("show", "-s", "--format=%ct", sha)), UTC)
        return f"{committed:%Y%m%d}-{sha[:12]}"

    def file_at(self, sha: str, path: str) -> bytes:
        result = subprocess.run(  # noqa: S603 - fixed git arguments
            ["git", "show", f"{sha}:{path}"], cwd=self.root, capture_output=True, check=False
        )
        if result.returncode != 0:
            raise ReleaseError(f"{path} doesn't exist at {sha[:12]}")
        return result.stdout


def image_reference(repository: str, version: str) -> str:
    if not _REPOSITORY.fullmatch(repository):
        raise ReleaseError(
            f"{repository!r} is not an image repository like registry.example.com/job-hunt-assistant "
            "(lower case, no tag: the version is the tag)"
        )
    return f"{repository}:{version}"


class Docker:
    """The docker CLI calls a release makes."""

    def exists_in_registry(self, image: str) -> bool:
        """Ask the registry itself (not the local image store). Only a definite "not
        found" means no: an error such as a refused login must not read as "free to push"."""
        # `docker manifest inspect` can't read a plain-http registry (a local one) without
        # --insecure; buildx imagetools reads both.
        result = subprocess.run(  # noqa: S603 - fixed docker arguments
            [shutil.which("docker") or "docker", "buildx", "imagetools", "inspect", image],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return True
        error = result.stderr.decode("utf-8", "replace").strip()
        if "not found" in error.lower():
            return False
        raise ReleaseError(f"can't tell whether {image} is already in the registry: {error}")

    def build(self, context: Path, image: str, commit: Commit) -> None:
        run(
            [
                "docker", "build", "--pull",
                "--build-arg", f"APP_RELEASE={commit.version}",
                "--build-arg", f"APP_REVISION={commit.sha}",
                "-t", image, str(context),
            ]
        )

    def push(self, image: str) -> str:
        """Push, and return repository@digest as the registry now has it (the local
        image's RepoDigests lists every repository it was ever pushed to)."""
        run(["docker", "push", image])
        digest = run(["docker", "buildx", "imagetools", "inspect", image, "--format", "{{.Manifest.Digest}}"], capture=True)
        return f"{image.rsplit(':', 1)[0]}@{digest}"

    def revision(self, image: str) -> str:
        """The commit an image was built from (the Dockerfile's revision label)."""
        inspect = ["docker", "image", "inspect", "--format", f'{{{{index .Config.Labels "{REVISION_LABEL}"}}}}', image]
        try:
            label = run(inspect, capture=True)
        except ReleaseError:
            run(["docker", "pull", image])
            label = run(inspect, capture=True)
        if not _SHA.fullmatch(label):
            raise ReleaseError(f"{image} has no commit label ({REVISION_LABEL}); build it with `release.py build`")
        return label


def run_every_check(worktree: Path, env_file: Path = REPO_ROOT / ".env") -> None:
    """The full gate, in a fresh checkout. It needs the test settings (the local test
    database) from .env, which is git-ignored so the checkout lacks it; the image's
    build context is an allowlist that leaves it out (.dockerignore)."""
    if env_file.exists():
        shutil.copy2(env_file, worktree / ".env")
    for project in NODE_PROJECTS:
        print(f"==> npm ci ({project})")
        run(["npm", "ci", "--no-audit", "--no-fund"], cwd=worktree / project)
    print("==> every check (scripts/check.py --all)")
    run(["uv", "run", "--frozen", "python", "scripts/check.py", "--all"], cwd=worktree)


class Builder:
    """`build`: test and build one commit in its own worktree, then publish it."""

    def __init__(self, repo: Repository, docker: Docker, checks: Callable[[Path], None] = run_every_check) -> None:
        self.repo = repo
        self.docker = docker
        self.checks = checks

    def build(self, ref: str, repository: str) -> str:
        self.repo.fetch()
        commit = self.repo.commit(ref)
        image = image_reference(repository, commit.version)
        # Checked before the tests' minutes, and tags are never reused: a deployed tag
        # must keep meaning the image that was tested.
        if self.docker.exists_in_registry(image):
            raise ReleaseError(f"{image} is already in the registry; tags are never overwritten")
        worktree = self.repo.root.parent / f"jha-release-{commit.sha[:12]}"
        print(f"==> {commit.sha[:12]} as {image}, in {worktree}")
        run(["git", "worktree", "add", "--detach", str(worktree), commit.sha], cwd=self.repo.root)
        try:
            self.checks(worktree)
            print("==> build")
            self.docker.build(worktree, image, commit)
        finally:
            run(["git", "worktree", "remove", "--force", str(worktree)], cwd=self.repo.root)
        print("==> push")
        digest = self.docker.push(image)
        print(f"==> released {image}\n    {digest}")
        return image


class Deployer:
    """`deploy`: put an image's commit's deploy files on the server and run them."""

    def __init__(self, repo: Repository, docker: Docker, ssh: Sequence[str] = ("ssh",)) -> None:
        self.repo = repo
        self.docker = docker
        self.ssh = list(ssh)

    def deploy(self, image: str, host: str, directory: str) -> None:
        sha = self.docker.revision(image)
        self.repo.fetch()
        self.repo.require_on_main(sha)
        print(f"==> {image} (commit {sha[:12]}) to {host}:{directory}")
        for path in DEPLOY_FILES:
            self._upload(host, directory, Path(path).name, self.repo.file_at(sha, path))
        run([*self.ssh, host, remote_deploy_command(directory, image)])

    def _upload(self, host: str, directory: str, name: str, content: bytes) -> None:
        run([*self.ssh, host, remote_upload_command(directory, name)], stdin=content)


def remote_upload_command(directory: str, name: str) -> str:
    """Write stdin to <directory>/<name> on the server: whole or not at all."""
    target = shlex.quote(f"{directory}/{name}")
    partial = shlex.quote(f"{directory}/.{name}.upload")
    return f"mkdir -p {shlex.quote(directory)} && cat > {partial} && mv {partial} {target}"


def remote_deploy_command(directory: str, image: str) -> str:
    return f"sh {shlex.quote(f'{directory}/{HOST_SCRIPT}')} {shlex.quote(image)}"


def _option(value: str | None, env_name: str, flag: str) -> str:
    resolved = value or os.environ.get(env_name, "")
    if not resolved:
        raise ReleaseError(f"give {flag} or set {env_name}")
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="test, build and push the image for a commit on origin/main")
    build.add_argument("ref", nargs="?", default=MAIN_REF, help=f"commit, tag or branch (default {MAIN_REF})")
    build.add_argument("--repository", help="image repository, e.g. registry.example.com/job-hunt-assistant")
    deploy = commands.add_parser("deploy", help="deploy a built image to the server")
    deploy.add_argument("image", help="the image reference `build` printed")
    deploy.add_argument("--host", help="ssh destination, e.g. deploy@server.example.com")
    deploy.add_argument("--dir", help=f"the server's deploy directory (default {DEFAULT_DEPLOY_DIR})")
    deploy.add_argument("--ssh", default="ssh", help='ssh client and its options (default "ssh")')
    args = parser.parse_args(argv)

    repo, docker = Repository(), Docker()
    try:
        if args.command == "build":
            Builder(repo, docker).build(args.ref, _option(args.repository, "JHA_IMAGE_REPOSITORY", "--repository"))
        else:
            directory = args.dir or os.environ.get("JHA_DEPLOY_DIR") or DEFAULT_DEPLOY_DIR
            Deployer(repo, docker, shlex.split(args.ssh)).deploy(args.image, _option(args.host, "JHA_DEPLOY_HOST", "--host"), directory)
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
