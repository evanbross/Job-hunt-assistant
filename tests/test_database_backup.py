# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-07
"""Off-site database backups (backend/db/backup.py, docs/cloud-deployment-readiness-plan.md 4.1).

Against the real test database and an S3 API (moto). pg_dump, age and pg_restore are
stand-in Python scripts here -- the test machines don't have them -- that keep each real
command's argument list and environment, so what matters at this layer is real: the
password stays off the command line, a step that fails partway leaves no object in the
bucket, and every run is recorded. The real tools' round trip (dump, encrypt, upload,
download, decrypt, restore, verify) was run in the image against Postgres and MinIO."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import psycopg2
import psycopg2.extras
import pytest
from cryptography.fernet import Fernet
from moto import mock_aws

import backend.db.backup as backup
import config
from backend.crypto import encrypt_secret
from backend.db.app_role import AppRoleConfigError
from backend.db.scheduled_backup import backup_if_due
from backend.db.schema_version import SchemaOutOfDateError
from backend.security.key_rotation import RotationError
from tests.db_roles import runtime_database_url

_BUCKET = "jha-backups"
_RECIPIENT = "age1" + "q" * 58
_FAKE_PASSWORD = "s3cret-owner-password"  # noqa: S105

_FAKE_PG_DUMP = """
import json, os, sys
record, size, code = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
with open(record, "w") as handle:
    json.dump({"argv": sys.argv[4:], "pgpassword": os.environ.get("PGPASSWORD")}, handle)
chunk = bytes(range(256)) * 256
written = 0
while written < size:
    n = min(len(chunk), size - written)
    sys.stdout.buffer.write(chunk[:n])
    written += n
sys.stdout.buffer.flush()
if code:
    sys.stderr.write("pg_dump: error: connection to server lost\\n")
sys.exit(code)
"""

# "Encrypts" by prefixing a header; fails up front when told to.
_FAKE_AGE_ENCRYPT = """
import sys
if sys.argv[1] != "0":
    sys.stderr.write("age: error: malformed recipient\\n")
    sys.exit(int(sys.argv[1]))
sys.stdout.buffer.write(b"AGE:")
while chunk := sys.stdin.buffer.read(65536):
    sys.stdout.buffer.write(chunk)
"""

_FAKE_AGE_DECRYPT = """
import sys
data = sys.stdin.buffer.read()
if not data.startswith(b"AGE:"):
    sys.stderr.write("age: error: no identity matched any of the recipients\\n")
    sys.exit(1)
sys.stdout.buffer.write(data[4:])
"""

_FAKE_PG_RESTORE = """
import sys
with open(sys.argv[1], "wb") as handle:
    handle.write(sys.stdin.buffer.read())
"""


def _script(tmp_path: Path, name: str, source: str) -> str:
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    return str(path)


@pytest.fixture
def s3() -> Any:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET)
        yield client


@pytest.fixture
def settings(s3: Any) -> backup.BackupSettings:
    return backup.BackupSettings(
        s3_bucket=_BUCKET, s3_region="us-east-1", s3_access_key_id="testing",
        s3_secret_access_key="testing", age_recipients=(_RECIPIENT,),  # noqa: S106
    )


class Tools:
    """Points the backup's commands at stand-ins, keeping each real argument list."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.record = tmp_path / "pg_dump.json"
        self.restored = tmp_path / "restored.dump"
        self._monkeypatch = monkeypatch
        self._dump_real, restore_real = backup.pg_dump_command, backup.pg_restore_command
        self.dump(size=1000)
        self.age(code=0)
        decrypt_script = _script(tmp_path, "age_decrypt", _FAKE_AGE_DECRYPT)
        restore_script = _script(tmp_path, "pg_restore", _FAKE_PG_RESTORE)
        monkeypatch.setattr(
            backup, "pg_restore_command",
            lambda conninfo: [sys.executable, restore_script, str(self.restored), *restore_real(conninfo)],
        )
        monkeypatch.setattr(backup, "age_decrypt_command", lambda _identity: [sys.executable, decrypt_script])

    def dump(self, *, size: int, code: int = 0) -> None:
        script = _script(self.tmp_path, "pg_dump", _FAKE_PG_DUMP)
        self._monkeypatch.setattr(
            backup, "pg_dump_command",
            lambda conninfo: [sys.executable, script, str(self.record), str(size), str(code),
                              *self._dump_real(conninfo)],
        )

    def age(self, *, code: int) -> None:
        script = _script(self.tmp_path, "age_encrypt", _FAKE_AGE_ENCRYPT)
        self._monkeypatch.setattr(backup, "age_encrypt_command", lambda _recipients: [sys.executable, script, str(code)])

    @staticmethod
    def expected_dump(size: int) -> bytes:
        return ((bytes(range(256)) * 256) * (size // 65536 + 1))[:size]


@pytest.fixture
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Tools:
    return Tools(tmp_path, monkeypatch)


def _runs(pg_dsn: str) -> list[dict[str, Any]]:
    conn = psycopg2.connect(pg_dsn, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM backup_runs ORDER BY id")
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _objects(s3: Any) -> list[str]:
    return [item["Key"] for item in s3.list_objects_v2(Bucket=_BUCKET).get("Contents", [])]


# ─── settings ───────────────────────────────────────────────────────────────────


def test_no_backup_variables_means_not_configured() -> None:
    assert backup.BackupSettings.from_env({"OTHER": "x", "BACKUP_S3_BUCKET": "  "}) is None


def test_settings_are_read_and_normalized() -> None:
    settings = backup.BackupSettings.from_env({
        "BACKUP_S3_BUCKET": "b", "BACKUP_S3_PREFIX": "/nightly", "BACKUP_INTERVAL_HOURS": "12",
        "BACKUP_AGE_RECIPIENTS": f"{_RECIPIENT}, {'age1' + 'p' * 58}",
    })

    assert settings is not None
    assert settings.s3_prefix == "nightly/"
    assert settings.interval_hours == 12
    assert settings.age_recipients == (_RECIPIENT, "age1" + "p" * 58)


@pytest.mark.parametrize(
    ("environ", "named"),
    [
        ({"BACKUP_AGE_RECIPIENTS": _RECIPIENT}, "BACKUP_S3_BUCKET"),
        ({"BACKUP_S3_BUCKET": "b", "BACKUP_AGE_RECIPIENTS": "ssh-rsa AAAA"}, "BACKUP_AGE_RECIPIENTS"),
        ({"BACKUP_S3_BUCKET": "b", "BACKUP_INTERVAL_HOURS": "0.5"}, "BACKUP_INTERVAL_HOURS"),
        ({"BACKUP_S3_BUCKET": "b", "BACKUP_S3_BUKET": "typo"}, "BACKUP_S3_BUKET"),
        ({"BACKUP_S3_BUCKET": "b", "BACKUP_S3_ACCESS_KEY_ID": "k"}, "BACKUP_S3_SECRET_ACCESS_KEY"),
    ],
)
def test_a_partial_or_invalid_configuration_is_an_error_naming_the_variable(
    environ: dict[str, str], named: str
) -> None:
    with pytest.raises(backup.BackupConfigError, match=named):
        backup.BackupSettings.from_env(environ)


def test_a_backup_is_never_written_unencrypted(pg_dsn: str, s3: Any) -> None:
    settings = backup.BackupSettings(s3_bucket=_BUCKET)

    with pytest.raises(backup.BackupConfigError, match="BACKUP_AGE_RECIPIENTS"):
        backup.run_backup(pg_dsn, settings)
    assert _objects(s3) == [] and _runs(pg_dsn) == []


def test_check_config_wants_settings_recipients_and_the_programs(monkeypatch: pytest.MonkeyPatch) -> None:
    complete = {"BACKUP_S3_BUCKET": "b", "BACKUP_AGE_RECIPIENTS": _RECIPIENT}
    monkeypatch.setattr(backup.shutil, "which", lambda name: f"/usr/bin/{name}")
    backup.check_config(complete)

    with pytest.raises(backup.BackupConfigError, match="not configured"):
        backup.check_config({})
    with pytest.raises(backup.BackupConfigError, match="BACKUP_AGE_RECIPIENTS"):
        backup.check_config({"BACKUP_S3_BUCKET": "b"})
    monkeypatch.setattr(backup.shutil, "which", lambda name: None if name == "age" else f"/usr/bin/{name}")
    with pytest.raises(backup.BackupConfigError, match="Not installed: age"):
        backup.check_config(complete)


# ─── taking a backup ────────────────────────────────────────────────────────────


def test_the_password_reaches_pg_dump_through_the_environment_not_the_command_line() -> None:
    conninfo, env = backup.libpq_target(f"postgresql://owner:{_FAKE_PASSWORD}@db.example.com:5432/jobs?sslmode=require")

    assert _FAKE_PASSWORD not in conninfo and _FAKE_PASSWORD not in " ".join(backup.pg_dump_command(conninfo))
    assert env["PGPASSWORD"] == _FAKE_PASSWORD
    assert "sslmode=require" in conninfo and "host=db.example.com" in conninfo


def test_a_backup_streams_the_encrypted_dump_to_the_bucket_and_records_it(
    pg_dsn: str, s3: Any, settings: backup.BackupSettings, tools: Tools
) -> None:
    result = backup.run_backup(pg_dsn, settings, now=datetime(2026, 11, 7, 3, 0, tzinfo=UTC))

    assert result is not None
    assert result.object_key.startswith("jha-backups/2026/11/jha-20261107T030000Z-")
    assert result.object_key.endswith(".dump.age")
    stored = s3.get_object(Bucket=_BUCKET, Key=result.object_key)["Body"].read()
    assert stored == b"AGE:" + Tools.expected_dump(1000)
    [run] = _runs(pg_dsn)
    assert run["status"] == "succeeded" and run["object_key"] == result.object_key
    assert run["size_bytes"] == len(stored) == result.size_bytes
    assert run["sha256"] == result.sha256
    called = json.loads(tools.record.read_text())
    assert called["argv"][:3] == ["pg_dump", "--format=custom", "--schema=public"]
    password = psycopg2.extensions.parse_dsn(pg_dsn).get("password")
    if password:
        assert called["pgpassword"] == password
        assert password not in " ".join(called["argv"])


@pytest.mark.parametrize("size", [1000, 12 * 1024 * 1024], ids=["single-part", "multipart"])
def test_a_dump_that_fails_partway_leaves_nothing_in_the_bucket(
    pg_dsn: str, s3: Any, settings: backup.BackupSettings, tools: Tools, size: int
) -> None:
    tools.dump(size=size, code=3)

    with pytest.raises(backup.BackupFailed, match="pg_dump exited with status 3: pg_dump: error: connection to server lost"):
        backup.run_backup(pg_dsn, settings)

    assert _objects(s3) == []
    assert s3.list_multipart_uploads(Bucket=_BUCKET).get("Uploads", []) == []
    [run] = _runs(pg_dsn)
    assert run["status"] == "failed" and "connection to server lost" in run["error"]


def test_an_encryption_failure_leaves_nothing_in_the_bucket(
    pg_dsn: str, s3: Any, settings: backup.BackupSettings, tools: Tools
) -> None:
    tools.age(code=1)

    with pytest.raises(backup.BackupFailed, match="age exited with status 1: age: error: malformed recipient"):
        backup.run_backup(pg_dsn, settings)

    assert _objects(s3) == []
    assert _runs(pg_dsn)[0]["status"] == "failed"


def test_two_backups_never_run_at_once(pg_dsn: str, s3: Any, settings: backup.BackupSettings, tools: Tools) -> None:
    holder = psycopg2.connect(pg_dsn)
    try:
        holder.cursor().execute("SELECT pg_advisory_lock(%s)", (backup.BACKUP_LOCK_ID,))
        assert backup.run_backup(pg_dsn, settings) is None
    finally:
        holder.close()

    assert _objects(s3) == [] and _runs(pg_dsn) == []
    assert backup.run_backup(pg_dsn, settings) is not None  # the lock was released, not leaked


def test_a_backup_refuses_a_role_row_level_security_applies_to(
    pg_dsn: str, s3: Any, settings: backup.BackupSettings, tools: Tools
) -> None:
    """As the runtime role pg_dump would see no account's rows -- an empty "backup"."""
    with pytest.raises(AppRoleConfigError):
        backup.run_backup(runtime_database_url(pg_dsn), settings)
    assert _objects(s3) == []


def test_the_app_cannot_read_or_forge_backup_records(pg_dsn: str) -> None:
    conn = psycopg2.connect(runtime_database_url(pg_dsn))
    try:
        cur = conn.cursor()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("SELECT * FROM backup_runs")
        conn.rollback()
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("INSERT INTO backup_runs (object_key, schema_hash, status) VALUES ('x', 'x', 'succeeded')")
    finally:
        conn.close()


# ─── the schedule ───────────────────────────────────────────────────────────────


_CONFIGURED = {"BACKUP_S3_BUCKET": _BUCKET, "BACKUP_S3_REGION": "us-east-1", "BACKUP_AGE_RECIPIENTS": _RECIPIENT,
               "BACKUP_S3_ACCESS_KEY_ID": "testing", "BACKUP_S3_SECRET_ACCESS_KEY": "testing"}


def test_an_unconfigured_desktop_install_skips_backups_quietly(
    pg_dsn: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "APP_MODE", "desktop")
    with caplog.at_level(logging.ERROR):
        assert backup_if_due(pg_dsn, {}) is False
    assert caplog.records == []


def test_an_unconfigured_hosted_install_says_so_every_tick(
    pg_dsn: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "APP_MODE", "hosted")
    with caplog.at_level(logging.ERROR):
        assert backup_if_due(pg_dsn, {}) is False
    assert "No database backups are configured" in caplog.text


def test_a_backup_is_due_once_the_last_success_is_an_interval_old(pg_dsn: str, s3: Any, tools: Tools) -> None:
    start = datetime.now(UTC)

    assert backup_if_due(pg_dsn, _CONFIGURED, now=start) is True
    assert backup_if_due(pg_dsn, _CONFIGURED, now=start + timedelta(hours=23)) is False
    assert backup_if_due(pg_dsn, _CONFIGURED, now=start + timedelta(hours=24, minutes=1)) is True
    assert len(_objects(s3)) == 2


def test_a_failed_backup_is_retried_at_the_next_tick(pg_dsn: str, s3: Any, tools: Tools) -> None:
    tools.dump(size=1000, code=1)
    with pytest.raises(backup.BackupFailed):
        backup_if_due(pg_dsn, _CONFIGURED)

    tools.dump(size=1000)
    assert backup_if_due(pg_dsn, _CONFIGURED) is True
    assert [run["status"] for run in _runs(pg_dsn)] == ["failed", "succeeded"]


# ─── restoring ──────────────────────────────────────────────────────────────────


def test_a_restore_decrypts_into_pg_restore_without_owner_or_grants(tools: Tools, tmp_path: Path) -> None:
    dump = Tools.expected_dump(5000)

    backup.decrypt_and_restore([b"AGE:" + dump[:100], dump[100:]], tmp_path / "id.txt", "postgresql://o:pw@h/db")

    assert tools.restored.read_bytes() == dump
    command = backup.pg_restore_command("dbname=x")
    for flag in ("--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction"):
        assert flag in command


def test_a_restore_with_the_wrong_identity_fails_naming_age(tools: Tools, tmp_path: Path) -> None:
    with pytest.raises(backup.BackupFailed, match="age exited with status 1: age: error: no identity matched"):
        backup.decrypt_and_restore([b"not encrypted for us"], tmp_path / "id.txt", "postgresql://o:pw@h/db")


def test_a_restore_refuses_a_database_that_is_not_empty(pg_dsn: str, tmp_path: Path) -> None:
    identity = tmp_path / "id.txt"
    identity.write_text("AGE-SECRET-KEY-1...", encoding="utf-8")

    with pytest.raises(backup.BackupFailed, match="not empty"):
        backup.restore_backup(pg_dsn, identity, file=tmp_path / "x.dump.age")

    assert _runs(pg_dsn) == []  # the schema (and this table) still there


def test_a_backup_reaches_the_bucket_and_comes_back_byte_for_byte(
    pg_dsn: str, s3: Any, settings: backup.BackupSettings, tools: Tools
) -> None:
    result = backup.run_backup(pg_dsn, settings)
    assert result is not None

    assert backup.list_object_keys(settings) == [result.object_key]
    assert backup.latest_object_key(settings) == result.object_key
    body = settings.s3_client().get_object(Bucket=_BUCKET, Key=result.object_key)["Body"]
    backup.decrypt_and_restore(body.iter_chunks(4096), Path("id.txt"), "postgresql://o:pw@h/db")
    assert tools.restored.read_bytes() == Tools.expected_dump(1000)


# ─── verifying a restored database ──────────────────────────────────────────────


def _store_secret_under(pg_dsn: str, key: bytes) -> None:
    conn = psycopg2.connect(pg_dsn)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM user_accounts LIMIT 1")
        user_id = cur.fetchone()[0]
        token = Fernet(key).encrypt(b'{"token": "x"}').decode()
        cur.execute(
            "INSERT INTO google_oauth_tokens (user_id, token_encrypted, updated_at) VALUES (%s, %s, 'now')",
            (user_id, token),
        )
        conn.commit()
    finally:
        conn.close()


def test_verify_passes_a_migrated_database_whose_secrets_open_with_this_key(pg_dsn: str) -> None:
    conn = psycopg2.connect(pg_dsn)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM user_accounts LIMIT 1")
        cur.execute(
            "INSERT INTO google_oauth_tokens (user_id, token_encrypted, updated_at) VALUES (%s, %s, 'now')",
            (cur.fetchone()[0], encrypt_secret('{"token": "x"}')),
        )
        conn.commit()
    finally:
        conn.close()

    report = backup.verify_restored_database(pg_dsn)

    assert report.row_counts["user_accounts"] >= 1
    assert report.secrets_checked["Google tokens"] == 1


def test_verify_fails_when_the_key_in_custody_does_not_open_the_backup(pg_dsn: str) -> None:
    _store_secret_under(pg_dsn, Fernet.generate_key())

    with pytest.raises(RotationError, match="google_oauth_tokens|Google tokens"):
        backup.verify_restored_database(pg_dsn)


def test_verify_fails_until_the_restored_database_is_migrated(pg_dsn: str) -> None:
    conn = psycopg2.connect(pg_dsn)
    try:
        conn.cursor().execute("UPDATE schema_state SET migration_hash = 'from-an-older-version'")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(SchemaOutOfDateError):
        backup.verify_restored_database(pg_dsn)


def test_verify_fails_when_row_level_security_is_off(pg_dsn: str) -> None:
    conn = psycopg2.connect(pg_dsn)
    try:
        conn.cursor().execute("ALTER TABLE jobs DISABLE ROW LEVEL SECURITY")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(backup.BackupFailed, match="Row-level security is off on: jobs"):
        backup.verify_restored_database(pg_dsn)


# ─── the command line ───────────────────────────────────────────────────────────


def test_the_command_line_reports_a_configuration_error_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for name in list(backup.os.environ):
        if name.startswith("BACKUP_"):
            monkeypatch.delenv(name)

    assert backup.main(["check-config"]) == 1
    assert "Backups are not configured" in caplog.text
