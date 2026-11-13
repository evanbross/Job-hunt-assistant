# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-07
"""Off-site, encrypted database backups, and restoring them (docs/cloud-deployment-readiness-plan.md 4.1).

Neon's point-in-time restore is the first line of defence; this is the second, for what
PITR can't cover: losing the Neon project or account, or damage noticed after the PITR
window. Postgres is the only durable state a hosted install has (documents are stored
in it; Redis holds only in-flight work), so one dump is a whole backup.

A backup is `pg_dump --format=custom` of the public schema, as the schema owner (row-level
security exempts it, and pg_dump refuses rather than silently dumping a filtered table),
piped through `age` to the configured public keys, and streamed to an S3-compatible
bucket. Nothing touches the disk, and the server holds only public keys: it can write
backups but never read one, so a compromised server can't read old backups. The bucket
credentials need only write access; expiry is the bucket's own lifecycle rule. The dump
reaches the bucket only if every step succeeded -- a dump that dies partway is aborted,
never stored as a short file that looks like a backup.

Restoring needs the age identity (the private key), which never lives on the server,
and runs anywhere the image runs. Both ends are stock tools -- the object is just an
age-encrypted pg_dump archive -- so a restore never depends on this code working
(deploy/README.md has the by-hand commands).

    python -m backend.db.backup run                     # one backup now
    python -m backend.db.backup status                  # recent runs (owner URL)
    python -m backend.db.backup list                    # objects in the bucket
    python -m backend.db.backup restore --target-url URL --identity FILE (--latest | --key KEY | --file PATH)
    python -m backend.db.backup verify --database-url URL
    python -m backend.db.backup check-config            # the maintenance worker's start-up check

Scheduled runs come from beat via backend/db/scheduled_backup.py.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import shutil
import subprocess  # noqa: S404 - runs pg_dump/age/pg_restore with fixed argument lists, no shell
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

import boto3
import psycopg2
import psycopg2.extensions
import psycopg2.extras
from boto3.exceptions import S3UploadFailedError
from boto3.s3.transfer import TransferConfig
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from psycopg2 import sql
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

import config
from backend.db import get_connection
from backend.db.app_role import AppRoleConfigError, assert_exempt_from_rls
from backend.db.repositories.backup_runs import SUCCEEDED, PostgresBackupRunRepository
from backend.db.row_level_security import tenant_tables
from backend.db.schema_version import (
    SchemaOutOfDateError,
    assert_schema_is_current,
    migration_source_hash,
)
from backend.logging_setup import configure_logging
from backend.security.key_rotation import RotationError, rotate_all

logger = logging.getLogger(__name__)

# An age X25519 recipient: "age1" + 58 bech32 characters.
_AGE_RECIPIENT = re.compile(r"^age1[02-9ac-hj-np-z]{58}$")
# Session-level advisory lock, so a scheduled run and a manual one never dump at once.
BACKUP_LOCK_ID = 0x4A48_4142  # "JHAB"
_CHUNK = 1024 * 1024
_ENV_PREFIX = "BACKUP_"


class BackupConfigError(RuntimeError):
    """The BACKUP_* settings are incomplete or invalid."""


class BackupFailed(RuntimeError):
    """A backup or restore step failed; nothing was stored (or restored)."""


class BackupSettings(BaseModel):
    """The BACKUP_* environment variables, validated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    s3_bucket: str
    s3_endpoint_url: str | None = None  # unset = AWS S3; set for R2, B2, MinIO, ...
    s3_region: str | None = None
    s3_prefix: str = "jha-backups/"
    s3_access_key_id: str | None = None  # unset = boto3's own chain (instance role, ...)
    s3_secret_access_key: str | None = None
    age_recipients: tuple[str, ...] = ()
    interval_hours: float = 24.0

    @field_validator("s3_prefix")
    @classmethod
    def _one_trailing_slash(cls, value: str) -> str:
        value = value.strip().strip("/")
        return f"{value}/" if value else ""

    @field_validator("age_recipients", mode="before")
    @classmethod
    def _split_recipients(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part for part in re.split(r"[\s,]+", value) if part)
        return value

    @field_validator("age_recipients")
    @classmethod
    def _valid_recipients(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        bad = [r for r in value if not _AGE_RECIPIENT.match(r)]
        if bad:
            raise ValueError(f"not age public keys (age1...): {', '.join(bad)}")
        return value

    @field_validator("interval_hours")
    @classmethod
    def _at_least_an_hour(cls, value: float) -> float:
        if value < 1:
            raise ValueError("must be at least 1")
        return value

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> BackupSettings | None:
        """The settings, or None when backups aren't configured at all (no BACKUP_*
        variable set). Raises BackupConfigError for a partial or invalid configuration,
        rather than quietly running without backups."""
        values = {
            name[len(_ENV_PREFIX):].lower(): raw.strip()
            for name, raw in environ.items()
            if name.startswith(_ENV_PREFIX) and raw.strip()
        }
        if not values:
            return None
        try:
            settings = cls.model_validate(values)
        except ValidationError as exc:
            problems = "; ".join(
                f"{_ENV_PREFIX}{'.'.join(str(p) for p in err['loc']).upper()}: {err['msg']}"
                for err in exc.errors()
            )
            raise BackupConfigError(f"Invalid backup settings -- {problems}") from exc
        if bool(settings.s3_access_key_id) != bool(settings.s3_secret_access_key):
            raise BackupConfigError(
                "Set both BACKUP_S3_ACCESS_KEY_ID and BACKUP_S3_SECRET_ACCESS_KEY, or neither."
            )
        return settings

    def require_recipients(self) -> tuple[str, ...]:
        if not self.age_recipients:
            raise BackupConfigError(
                "BACKUP_AGE_RECIPIENTS is not set: backups are only ever written encrypted."
            )
        return self.age_recipients

    def s3_client(self) -> Any:
        return boto3.client(
            "s3",
            endpoint_url=self.s3_endpoint_url,
            region_name=self.s3_region,
            aws_access_key_id=self.s3_access_key_id,
            aws_secret_access_key=self.s3_secret_access_key,
            config=BotoConfig(retries={"mode": "standard", "max_attempts": 5}),
        )


@dataclass(frozen=True)
class BackupResult:
    object_key: str
    size_bytes: int
    sha256: str


# ── the commands (module-level so tests can swap in stand-ins) ───────────────────


def pg_dump_command(conninfo: str) -> list[str]:
    return ["pg_dump", "--format=custom", "--schema=public", "--no-password", f"--dbname={conninfo}"]


def age_encrypt_command(recipients: Sequence[str]) -> list[str]:
    command = ["age", "--encrypt"]
    for recipient in recipients:
        command += ["--recipient", recipient]
    return command


def age_decrypt_command(identity_file: Path) -> list[str]:
    return ["age", "--decrypt", "--identity", str(identity_file)]


def pg_restore_command(conninfo: str) -> list[str]:
    # No owner or grants from the source: the restoring login owns everything, and
    # `migrate` then provisions the runtime and read-only roles' grants afresh.
    return [
        "pg_restore", "--no-owner", "--no-privileges", "--exit-on-error",
        "--single-transaction", "--no-password", f"--dbname={conninfo}",
    ]


def libpq_target(dsn: str) -> tuple[str, dict[str, str]]:
    """`dsn` split into a conninfo string without the password, for a command line
    (which any process on the host can read), and an environment carrying the password."""
    params = psycopg2.extensions.parse_dsn(dsn)
    password = params.pop("password", None)
    env = dict(os.environ)
    if password:
        env["PGPASSWORD"] = password
    return psycopg2.extensions.make_dsn(**params), env


def object_key(prefix: str, when: datetime, schema_hash: str) -> str:
    """`<prefix>YYYY/MM/jha-<UTC time>-<schema>.dump.age`. Sorts by time; the schema
    tag says which migration code produced it."""
    stamp = when.astimezone(UTC)
    return f"{prefix}{stamp:%Y/%m}/jha-{stamp:%Y%m%dT%H%M%SZ}-{schema_hash[:12]}.dump.age"


# ── a pipeline of processes ─────────────────────────────────────────────────────


@dataclass
class _Step:
    name: str
    process: subprocess.Popen[bytes]
    stderr: IO[bytes]

    def error(self) -> str | None:
        code = self.process.wait()
        if code == 0:
            return None
        self.stderr.seek(0)
        detail = self.stderr.read().decode("utf-8", "replace").strip()
        return f"{self.name} exited with status {code}" + (f": {detail}" if detail else "")


def _start(name: str, command: list[str], *, stdin: Any, env: Mapping[str, str] | None = None) -> _Step:
    stderr = tempfile.TemporaryFile()  # noqa: SIM115 - closed by _close_all
    process = subprocess.Popen(  # noqa: S603 - fixed argument list, no shell
        command, stdin=stdin, stdout=subprocess.PIPE, stderr=stderr, env=env
    )
    return _Step(name, process, stderr)


def _failures(steps: Iterable[_Step]) -> list[str]:
    return [error for step in steps if (error := step.error())]


def _close_all(steps: Iterable[_Step]) -> None:
    for step in steps:
        if step.process.poll() is None:
            step.process.kill()
            step.process.wait()
        if step.process.stdout is not None:
            step.process.stdout.close()
        step.stderr.close()


class _CheckedOutput:
    """The pipeline's last output, as the file object the uploader reads. At end of
    stream it waits for every step and raises BackupFailed if any failed -- inside the
    upload, so the upload aborts and no object is ever created from a broken dump."""

    def __init__(self, steps: list[_Step]) -> None:
        self._steps = steps
        stdout = steps[-1].process.stdout
        assert stdout is not None  # noqa: S101 - _start always pipes stdout
        self._stream = stdout
        self._digest = hashlib.sha256()
        self.size = 0

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()

    def read(self, size: int = -1) -> bytes:
        data = self._stream.read(size if size and size > 0 else -1)
        if data:
            self._digest.update(data)
            self.size += len(data)
            return data
        failures = _failures(self._steps)
        if failures:
            raise BackupFailed("; ".join(failures))
        return b""


def _dump_and_upload(owner_dsn: str, settings: BackupSettings, key: str) -> BackupResult:
    conninfo, env = libpq_target(owner_dsn)
    steps: list[_Step] = []
    try:
        steps.append(_start("pg_dump", pg_dump_command(conninfo), stdin=subprocess.DEVNULL, env=env))
        steps.append(_start("age", age_encrypt_command(settings.require_recipients()), stdin=steps[0].process.stdout))
        assert steps[0].process.stdout is not None  # noqa: S101
        steps[0].process.stdout.close()  # age owns the read end now; pg_dump sees SIGPIPE if age dies
        output = _CheckedOutput(steps)
        settings.s3_client().upload_fileobj(
            output, settings.s3_bucket, key,
            ExtraArgs={"ContentType": "application/octet-stream"},
            Config=TransferConfig(max_concurrency=4),
        )
    finally:
        _close_all(steps)
    return BackupResult(object_key=key, size_bytes=output.size, sha256=output.sha256)


def run_backup(owner_dsn: str, settings: BackupSettings, *, now: datetime | None = None) -> BackupResult | None:
    """Dump, encrypt and upload one backup, recording it in backup_runs. Returns None if
    another backup holds the lock. Raises BackupFailed (after recording the failure) if
    any step fails, and AppRoleConfigError unless `owner_dsn` is exempt from RLS."""
    settings.require_recipients()
    schema_hash = migration_source_hash()
    key = object_key(settings.s3_prefix, now or datetime.now(UTC), schema_hash)
    conn = get_connection(owner_dsn)
    try:
        assert_exempt_from_rls(conn)
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (BACKUP_LOCK_ID,))
        if not cur.fetchone()["locked"]:
            conn.rollback()
            logger.info("Skipping the backup: another one is running.")
            return None
        try:
            runs = PostgresBackupRunRepository(conn)
            run_id = runs.start(key, schema_hash)
            conn.commit()
            try:
                result = _dump_and_upload(owner_dsn, settings, key)
            except (BackupFailed, BotoCoreError, ClientError, S3UploadFailedError, OSError) as exc:
                runs.fail(run_id, f"{type(exc).__name__}: {exc}")  # recorded, then re-raised
                conn.commit()
                raise
            runs.succeed(run_id, result.size_bytes, result.sha256)
            conn.commit()
        finally:
            cur = conn.cursor()
            cur.execute("SELECT pg_advisory_unlock(%s)", (BACKUP_LOCK_ID,))
            conn.commit()
    finally:
        conn.close()
    logger.info("Backed up the database to s3://%s/%s (%d bytes).", settings.s3_bucket, key, result.size_bytes)
    return result


def last_success_at(owner_dsn: str) -> datetime | None:
    conn = get_connection(owner_dsn)
    try:
        at = PostgresBackupRunRepository(conn).last_success_at()
        conn.rollback()
    finally:
        conn.close()
    return at


# ── restore ─────────────────────────────────────────────────────────────────────


def _connect(dsn: str) -> Any:
    """A plain connection, not the app's pool: a restore target is not the app's database."""
    return psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)


def _prepare_empty_target(target_dsn: str) -> None:
    """Refuse anything but an empty database, then drop its empty public schema: the
    archive holds the public schema itself (pg_dump --schema=public), so restoring
    recreates it. DROP SCHEMA without CASCADE fails if anything is in it."""
    conn = _connect(target_dsn)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT count(*) AS n FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'public'"
        )
        if cur.fetchone()["n"]:
            raise BackupFailed(
                "The target database is not empty. Restore into a new, empty database"
                " (a fresh Neon branch or database), never over one in use."
            )
        cur.execute("DROP SCHEMA IF EXISTS public")
        conn.commit()
    finally:
        conn.close()


def _recreate_public_schema(target_dsn: str) -> None:
    conn = _connect(target_dsn)
    try:
        conn.cursor().execute("CREATE SCHEMA IF NOT EXISTS public")
        conn.commit()
    finally:
        conn.close()


def decrypt_and_restore(chunks: Iterable[bytes], identity_file: Path, target_dsn: str) -> None:
    """Feed an encrypted backup through `age --decrypt` into `pg_restore`. Raises
    BackupFailed naming every step that failed; pg_restore's --single-transaction means
    a failure leaves nothing half-restored."""
    conninfo, env = libpq_target(target_dsn)
    steps: list[_Step] = []
    try:
        steps.append(_start("age", age_decrypt_command(identity_file), stdin=subprocess.PIPE))
        steps.append(_start("pg_restore", pg_restore_command(conninfo), stdin=steps[0].process.stdout, env=env))
        age_in, age_out = steps[0].process.stdin, steps[0].process.stdout
        assert age_in is not None and age_out is not None  # noqa: S101 - piped above
        age_out.close()  # pg_restore owns the read end now
        try:
            for chunk in chunks:
                age_in.write(chunk)
            age_in.close()
        except BrokenPipeError:
            pass  # a step died; its exit status below says why
        failures = _failures(steps)
    finally:
        _close_all(steps)
    if failures:
        raise BackupFailed("; ".join(failures))


def _file_chunks(path: Path) -> Iterable[bytes]:
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            yield chunk


def latest_object_key(settings: BackupSettings) -> str:
    keys = list_object_keys(settings)
    if not keys:
        raise BackupFailed(f"No backups under s3://{settings.s3_bucket}/{settings.s3_prefix}")
    return keys[-1]


def list_object_keys(settings: BackupSettings) -> list[str]:
    """Every backup object's key, oldest first (keys sort by time)."""
    paginator = settings.s3_client().get_paginator("list_objects_v2")
    keys = [
        item["Key"]
        for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=settings.s3_prefix)
        for item in page.get("Contents", [])
        if item["Key"].endswith(".dump.age")
    ]
    return sorted(keys)


def restore_backup(
    target_dsn: str,
    identity_file: Path,
    *,
    settings: BackupSettings | None = None,
    key: str | None = None,
    file: Path | None = None,
) -> None:
    """Restore one backup -- an object in the bucket (`key`) or a downloaded copy
    (`file`) -- into the empty database at `target_dsn`. Afterwards run `migrate`
    against it (it grants the runtime role), then `verify`."""
    if not identity_file.is_file():
        raise BackupFailed(f"No age identity file at {identity_file}")
    _prepare_empty_target(target_dsn)
    try:
        if file is not None:
            decrypt_and_restore(_file_chunks(file), identity_file, target_dsn)
        elif settings is not None and key is not None:
            body = settings.s3_client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"]
            try:
                decrypt_and_restore(body.iter_chunks(_CHUNK), identity_file, target_dsn)
            finally:
                body.close()
        else:
            raise BackupFailed("Name a backup: a bucket key or a local file.")
    except BackupFailed:
        _recreate_public_schema(target_dsn)  # --single-transaction rolled the restore back
        raise


# ── verify a restored database ──────────────────────────────────────────────────


@dataclass(frozen=True)
class VerifyReport:
    row_counts: dict[str, int]
    secrets_checked: dict[str, int]


def verify_restored_database(dsn: str) -> VerifyReport:
    """Prove a restored (and migrated) database is usable: migrated by this code, row-level
    security armed on every account table, and every stored secret readable with the
    SETTINGS_ENCRYPTION_KEY in this environment -- the check that the key held in custody
    really opens this backup. Raises SchemaOutOfDateError, AppRoleConfigError,
    RotationError or BackupFailed."""
    conn = get_connection(dsn)
    try:
        assert_schema_is_current(conn)
        assert_exempt_from_rls(conn)  # must see every account's rows to count and decrypt them
        cur = conn.cursor()
        cur.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'public' AND c.relrowsecurity"
            " AND EXISTS (SELECT 1 FROM pg_policies p WHERE p.schemaname = 'public' AND p.tablename = c.relname)"
        )
        armed = {row["relname"] for row in cur.fetchall()}
        unarmed = [table for table in tenant_tables(cur) if table not in armed]
        if unarmed:
            raise BackupFailed(f"Row-level security is off on: {', '.join(unarmed)}. Run migrate.")
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        tables = [row["tablename"] for row in cur.fetchall()]
        counts: dict[str, int] = {}
        for table in tables:
            cur.execute(
                sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))
            )
            counts[table] = int(cur.fetchone()["n"])
        secrets = rotate_all(conn, dry_run=True).scanned
        conn.rollback()
    finally:
        conn.close()
    return VerifyReport(row_counts=counts, secrets_checked=secrets)


# ── command line ────────────────────────────────────────────────────────────────


def _settings_or_exit(environ: Mapping[str, str]) -> BackupSettings:
    settings = BackupSettings.from_env(environ)
    if settings is None:
        raise BackupConfigError("Backups are not configured: set BACKUP_S3_BUCKET and BACKUP_AGE_RECIPIENTS.")
    return settings


def _cmd_run(_args: argparse.Namespace) -> int:
    result = run_backup(config.DATABASE_OWNER_URL, _settings_or_exit(os.environ))
    if result is None:
        print("Another backup is running; nothing done.")
        return 1
    print(f"{result.object_key}  {result.size_bytes} bytes  sha256 {result.sha256}")
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    conn = get_connection(config.DATABASE_OWNER_URL)
    try:
        runs = PostgresBackupRunRepository(conn).recent()
        conn.rollback()
    finally:
        conn.close()
    if not runs:
        print("No backups recorded.")
    for run in runs:
        detail = f"{run['size_bytes']} bytes" if run["status"] == SUCCEEDED else (run["error"] or "")
        print(f"{run['started_at']}  {run['status']:<9}  {run['object_key']}  {detail}")
    return 0


# The programs a backup and a restore run.
REQUIRED_PROGRAMS = ("pg_dump", "pg_restore", "age")


def check_config(environ: Mapping[str, str]) -> None:
    """Raise BackupConfigError unless backups can run here: the settings are complete
    (with at least one age recipient) and the programs are installed. The maintenance
    worker runs this before starting (deploy/docker-entrypoint.sh), so a deployment
    without working backups fails at start-up instead of an hour later."""
    _settings_or_exit(environ).require_recipients()
    missing = [name for name in REQUIRED_PROGRAMS if shutil.which(name) is None]
    if missing:
        raise BackupConfigError(f"Not installed: {', '.join(missing)} (the image's Dockerfile installs them).")


def _cmd_check_config(_args: argparse.Namespace) -> int:
    check_config(os.environ)
    print("Backups are configured.")
    return 0


def _cmd_list(_args: argparse.Namespace) -> int:
    for key in list_object_keys(_settings_or_exit(os.environ)):
        print(key)
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    settings = None if args.file else _settings_or_exit(os.environ)
    key = None
    if settings is not None:
        key = args.key or latest_object_key(settings)
        print(f"Restoring s3://{settings.s3_bucket}/{key}")
    restore_backup(args.target_url, args.identity, settings=settings, key=key, file=args.file)
    print("Restored. Next: run `migrate` against the restored database (DATABASE_OWNER_URL ="
          " its owner, DATABASE_URL = a runtime login for it), then `verify`.")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    report = verify_restored_database(args.database_url)
    for table, count in report.row_counts.items():
        print(f"{table:<40} {count:>10}")
    checked = ", ".join(f"{label} {n}" for label, n in report.secrets_checked.items())
    print(f"Every stored secret decrypts with this SETTINGS_ENCRYPTION_KEY ({checked}).")
    print("The restored database is migrated, row-level security is armed, and it is usable.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m backend.db.backup", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="take one backup now (as DATABASE_OWNER_URL)")
    commands.add_parser("status", help="recent backup runs recorded in the database")
    commands.add_parser("list", help="backup objects in the bucket (needs list access)")
    commands.add_parser("check-config", help="fail unless backups are configured and can run here")
    restore = commands.add_parser("restore", help="restore a backup into an empty database")
    restore.add_argument("--target-url", required=True, help="the EMPTY database to restore into, as its owner")
    restore.add_argument("--identity", required=True, type=Path, help="the age identity (private key) file")
    which = restore.add_mutually_exclusive_group(required=True)
    which.add_argument("--latest", action="store_true", help="the newest object in the bucket")
    which.add_argument("--key", help="a bucket object key")
    which.add_argument("--file", type=Path, help="a downloaded .dump.age file")
    verify = commands.add_parser("verify", help="check a restored, migrated database is usable")
    verify.add_argument("--database-url", required=True, help="the restored database, as its owner")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    handlers = {
        "run": _cmd_run, "status": _cmd_status, "list": _cmd_list,
        "restore": _cmd_restore, "verify": _cmd_verify, "check-config": _cmd_check_config,
    }
    try:
        return handlers[args.command](args)
    except (BackupConfigError, BackupFailed, AppRoleConfigError, SchemaOutOfDateError, RotationError) as exc:
        logger.critical("%s", exc)
        return 1
    except psycopg2.Error as exc:
        logger.critical("Database error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
