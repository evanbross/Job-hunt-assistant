# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""The test database's two roles, mirroring production's (config.py, backend/db/app_role.py).

TEST_DATABASE_URL logs in as the schema owner (jha_test_app): pg_dsn returns it, so a
test's own raw setup and assertions can read and write any account's rows, run DDL, and
call migrate_database. The app code under test serves as a separate runtime role, the
way it does in production, so row-level security applies to it and a missing grant
fails here instead of in a deployment. migrate_database creates that role from this URL.
"""

from __future__ import annotations

import os
from urllib.parse import quote, urlparse, urlunparse

_DEFAULT_RUNTIME_USER = "jha_test_runtime"
_DEFAULT_RUNTIME_PASSWORD = "jha_test_runtime_password"


def runtime_database_url(owner_dsn: str) -> str:
    """The runtime role's URL for the same test database as `owner_dsn`:
    TEST_APP_DATABASE_URL if set, else the owner URL with the runtime role's credentials."""
    explicit = os.environ.get("TEST_APP_DATABASE_URL")
    if explicit:
        return explicit
    parsed = urlparse(owner_dsn)
    host = parsed.hostname or "localhost"
    netloc = (
        f"{quote(_DEFAULT_RUNTIME_USER, safe='')}:{quote(_DEFAULT_RUNTIME_PASSWORD, safe='')}"
        f"@{host}" + (f":{parsed.port}" if parsed.port else "")
    )
    return urlunparse(parsed._replace(netloc=netloc))
