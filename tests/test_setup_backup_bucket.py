# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-07
"""scripts/setup_backup_bucket.py against an S3 API (moto): it creates the bucket once,
sets retention, proves the server key writes, and writes a deploy/.env.backup that the
backup module itself accepts."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from moto import mock_aws

from backend.db.backup import BackupSettings, list_object_keys

_RECIPIENT = "age1" + "q" * 58


@pytest.fixture(scope="module")
def setup_script() -> Any:
    path = Path(__file__).resolve().parent.parent / "scripts" / "setup_backup_bucket.py"
    spec = importlib.util.spec_from_file_location("setup_backup_bucket", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.fixture
def settings() -> Any:
    with mock_aws():
        yield BackupSettings(
            s3_bucket="jha-backups", s3_region="us-east-1", s3_prefix="nightly/",
            s3_access_key_id="testing", s3_secret_access_key="testing",  # noqa: S106
            age_recipients=(_RECIPIENT,), interval_hours=12,
        )


def test_the_bucket_is_created_once_with_a_retention_rule(setup_script: Any, settings: BackupSettings) -> None:
    client = settings.s3_client()

    assert setup_script.ensure_bucket(client, "jha-backups", "us-east-1") is True
    assert setup_script.ensure_bucket(client, "jha-backups", "us-east-1") is False
    assert setup_script.set_retention(client, "jha-backups", "nightly/", 35) is True
    [rule] = client.get_bucket_lifecycle_configuration(Bucket="jha-backups")["Rules"]
    assert rule["Expiration"]["Days"] == 35 and rule["Filter"]["Prefix"] == "nightly/"


def test_the_server_key_check_writes_a_marker_backup_list_ignores(
    setup_script: Any, settings: BackupSettings
) -> None:
    client = settings.s3_client()
    setup_script.ensure_bucket(client, "jha-backups", "us-east-1")

    key = setup_script.check_server_can_write(settings)

    assert key.startswith("nightly/setup-check-")
    assert client.get_object(Bucket="jha-backups", Key=key)["Body"].read()
    assert list_object_keys(settings) == []


def test_the_env_file_round_trips_through_the_backup_settings(
    setup_script: Any, settings: BackupSettings, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env.backup"
    setup_script.write_env_file(env_file, settings, overwrite=False)

    values = dict(
        line.split("=", 1) for line in env_file.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    assert BackupSettings.from_env(values) == settings
    with pytest.raises(FileExistsError):
        setup_script.write_env_file(env_file, settings, overwrite=False)


def test_an_existing_identity_is_never_overwritten(setup_script: Any, tmp_path: Path) -> None:
    identity = tmp_path / "id.txt"
    identity.write_text("AGE-SECRET-KEY-1EXISTING", encoding="utf-8")

    with pytest.raises(FileExistsError):
        setup_script.generate_identity(identity)
    assert identity.read_text(encoding="utf-8") == "AGE-SECRET-KEY-1EXISTING"
