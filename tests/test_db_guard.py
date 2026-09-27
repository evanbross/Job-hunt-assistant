# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# LICENSE: Personal Use Only
# This source code is made available for personal, non-commercial use only.
# You may read, study, and run this code for personal purposes.
# You may NOT:
#   - Use this code, in whole or in part, in any commercial product or service
#   - Sell, sublicense, or otherwise monetize this code or derivatives of it
#   - Redistribute this code without explicit written permission from ZombieTech LLC
#
# No warranty is provided, express or implied. Use at your own risk.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================


from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.db_guard import UnsafeTestDatabaseError, assert_safe_test_database

# ai: claude-sonnet-5 | 2026-09-26

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_URL = "postgresql://app:pw@localhost:5432/jobhunt"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://postgres:pw@localhost:5432/jha_test_db?sslmode=disable",
        "postgresql://postgres:pw@127.0.0.1:5433/jobhunt_test",
        "postgresql://postgres:pw@[::1]:5432/TEST",
    ],
)
def test_a_local_test_database_is_accepted(url: str) -> None:
    assert_safe_test_database(url, APP_URL)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("postgresql://u:pw@db.neon.tech:5432/jha_test_db", "not local"),
        ("postgresql://u:pw@localhost:5432/jobhunt", "does not contain 'test'"),
        ("postgresql://u:pw@localhost:5432", "does not contain 'test'"),
        ("postgresql://u:pw@10.0.0.5:5432/jha_test_db", "not local"),
    ],
)
def test_anything_else_is_refused_with_the_reason(url: str, reason: str) -> None:
    with pytest.raises(UnsafeTestDatabaseError, match=reason):
        assert_safe_test_database(url, APP_URL)


def test_the_applications_own_database_is_refused_even_if_it_looks_like_a_test_one() -> None:
    app_url = "postgresql://app:pw@localhost:5432/jha_test_db"

    with pytest.raises(UnsafeTestDatabaseError, match="own DATABASE_URL"):
        assert_safe_test_database("postgresql://other:pw2@localhost:5432/jha_test_db", app_url)


def test_the_suite_aborts_before_running_anything_against_a_real_database() -> None:
    """End to end: a child pytest pointed at a remote database exits with an error and runs no test."""
    env = {**os.environ, "TEST_DATABASE_URL": "postgresql://u:pw@db.example.com:5432/production"}
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "pytest", "tests/test_client_ip.py", "-q", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 2
    assert "Refusing to run the test suite" in result.stdout + result.stderr
    assert " passed" not in result.stdout


def test_a_named_extra_host_is_accepted_but_other_checks_still_apply() -> None:
    """CI reaches its Postgres service by name; the job opts that one host in explicitly."""
    ci_hosts = frozenset({"postgres"})

    assert_safe_test_database("postgresql://u:pw@postgres:5432/jha_test_db", APP_URL, ci_hosts)
    with pytest.raises(UnsafeTestDatabaseError, match="not local"):
        assert_safe_test_database("postgresql://u:pw@other-host:5432/jha_test_db", APP_URL, ci_hosts)
    with pytest.raises(UnsafeTestDatabaseError, match="does not contain 'test'"):
        assert_safe_test_database("postgresql://u:pw@postgres:5432/jobhunt", APP_URL, ci_hosts)
