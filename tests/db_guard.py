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

"""Refuses to let the suite touch any database except a local, dedicated test one.

The pg_dsn fixture TRUNCATEs every table before each test, so a TEST_DATABASE_URL (or
LOCAL_POSTGRES_* values) pointing at the application's real database, or at a remote host,
would wipe production data."""

from __future__ import annotations

from urllib.parse import urlparse

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class UnsafeTestDatabaseError(RuntimeError):
    pass


# ai: claude-sonnet-5 | 2026-09-26
def _identity(url: str) -> tuple[str, int, str]:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower(), parsed.port or 5432, parsed.path.lstrip("/").lower()


def assert_safe_test_database(
    test_url: str, app_url: str = "", extra_hosts: frozenset[str] = frozenset()
) -> None:
    """Raise unless *test_url* is a local database whose name says it is for tests and
    which is not the application's own database (*app_url*).

    *extra_hosts* names additional hosts to accept, for a CI job whose throwaway Postgres
    service is reached by service name rather than localhost. It is an explicit opt-in
    (see TEST_DATABASE_EXTRA_HOSTS in conftest), never a wildcard."""
    host, port, name = _identity(test_url)
    problems: list[str] = []
    if host not in _LOCAL_HOSTS | extra_hosts:
        problems.append(f"host {host or '(none)'!r} is not local")
    if "test" not in name:
        problems.append(f"database name {name or '(none)'!r} does not contain 'test'")
    if app_url and _identity(app_url) == (host, port, name):
        problems.append("it is the application's own DATABASE_URL")
    if problems:
        raise UnsafeTestDatabaseError(
            "Refusing to run the test suite: the test database is unsafe because "
            + "; ".join(problems)
            + ". The suite truncates every table. Point TEST_DATABASE_URL / LOCAL_POSTGRES_* "
            "at a local database named like 'jha_test_db'."
        )
