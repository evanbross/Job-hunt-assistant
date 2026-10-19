# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""GET /api/account/export (routes/session_auth.py): everything a data-export request
should give an account back about itself -- every row it owns, across every tenant
table, as one JSON document.

What it deliberately leaves out: live credentials, not data. Exporting your own job
history and profile is the point; exporting a still-usable Google refresh token or
Greenhouse session cookie alongside it would hand out a working session, not a record
of one. Encrypted secret-bearing columns are dropped by name; `user_settings` rows are
additionally checked against backend/settings_registry.py's `is_secret` flag, since a
secret's column name (`value`) doesn't say so on its own. Extension tokens are only
ever stored as a hash (see backend/extension_auth/token_service.py), so there's nothing
sensitive to redact there -- they're included in full, minus the hash itself."""

from __future__ import annotations

import base64
from datetime import date, datetime
from typing import Any

from backend.db.row_level_security import tenant_tables
from backend.resume.resume_file_service import ResumeFileService
from backend.settings_registry import SETTINGS

# Extension tokens are tenant-scoped but excluded from the RLS backstop (see
# row_level_security.py) -- explicitly included here since they're still the
# account's own data.
_EXTRA_TABLES: tuple[str, ...] = ("extension_tokens",)

# column name -> replacement. Applied to any table that has this column.
_REDACT_COLUMNS: frozenset[str] = frozenset(
    {"file_encrypted", "token_encrypted", "state_encrypted", "token_hash"}
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _export_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
    exported = {
        key: _json_safe(value) for key, value in row.items() if key not in _REDACT_COLUMNS
    }
    if table == "user_settings":
        spec = SETTINGS.get(str(row.get("key")))
        if spec is not None and spec.is_secret:
            exported["value"] = "[redacted: secret setting]"
    return exported


def export_user_data(repos: dict[str, Any], user_id: int) -> dict[str, Any]:
    """Every row *user_id* owns, table by table, plus the resume file itself
    (base64-encoded) when one is stored. Reads through the already-open, RLS-scoped
    `repos["conn"]` -- the caller must have opened it with this user's own identity
    (get_repos(user_id), or get_repos() inside that user's own request)."""
    conn = repos["conn"]
    cur = conn.cursor()
    tables = [*tenant_tables(cur), *_EXTRA_TABLES]
    data: dict[str, list[dict[str, Any]]] = {}
    for table in tables:
        cur.execute(f"SELECT * FROM {table} WHERE user_id = %s", (user_id,))  # noqa: S608 - table names from tenant_tables()/the fixed tuple above, never user input
        data[table] = [_export_row(table, dict(row)) for row in cur.fetchall()]

    resume_file = ResumeFileService(repos["resume"]).load_file(user_id)
    if resume_file is not None:
        content, filename = resume_file
        data["resume_file"] = [
            {"filename": filename, "content_base64": base64.b64encode(content).decode("ascii")}
        ]

    return data
