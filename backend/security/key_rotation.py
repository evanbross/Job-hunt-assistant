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

"""Re-encrypt every stored ciphertext under the current SETTINGS_ENCRYPTION_KEY.

After a new key is made primary (with the old one kept in
SETTINGS_ENCRYPTION_KEY_FALLBACKS), values written earlier are still readable
but sit under the old key. This walks every place the app stores ciphertext and
re-encrypts what is not yet under the primary key, so the old key can be
retired. The list of locations is explicit: guessing at "encrypted-looking"
columns would risk rewriting plaintext."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.crypto import needs_rotation, reencrypt
from backend.settings_registry import SETTINGS


class RotationError(RuntimeError):
    """A stored value could not be read with any configured key."""


@dataclass(frozen=True)
class EncryptedColumn:
    label: str
    table: str
    column: str
    key_columns: tuple[str, ...]
    # Restrict to these values of `restrict_column` (used for settings tables,
    # where only registry keys marked is_secret are ciphertext).
    restrict_column: str | None = None
    restrict_values: tuple[str, ...] = ()


@dataclass
class RotationReport:
    scanned: dict[str, int] = field(default_factory=dict)
    rotated: dict[str, int] = field(default_factory=dict)

    @property
    def total_rotated(self) -> int:
        return sum(self.rotated.values())


# ai: claude-sonnet-5 | 2026-09-26
def encrypted_columns() -> list[EncryptedColumn]:
    """Every location that holds ciphertext. Table and column names are
    constants defined here, never derived from data, so interpolating them
    into SQL below is safe."""
    system_secrets = tuple(
        key for key, spec in SETTINGS.items() if spec.scope == "system" and spec.is_secret
    )
    user_secrets = tuple(
        key for key, spec in SETTINGS.items() if spec.scope == "user" and spec.is_secret
    )
    return [
        EncryptedColumn(
            "system settings", "system_settings", "value", ("key",), "key", system_secrets
        ),
        EncryptedColumn(
            "user settings", "user_settings", "value", ("user_id", "key"), "key", user_secrets
        ),
        EncryptedColumn("resume files", "resume", "file_encrypted", ("id",)),
        EncryptedColumn("Google tokens", "google_oauth_tokens", "token_encrypted", ("user_id",)),
        EncryptedColumn("Greenhouse sessions", "greenhouse_sessions", "state_encrypted", ("user_id",)),
    ]


# ai: claude-sonnet-5 | 2026-09-26
def rotate_all(conn: Any, *, dry_run: bool = False) -> RotationReport:
    """Re-encrypt (or, with dry_run, just count) every ciphertext not yet under
    the primary key. Does not commit: the caller commits on success, so a failure
    part-way leaves the database exactly as it was."""
    report = RotationReport()
    cur = conn.cursor()
    for target in encrypted_columns():
        key_list = ", ".join(target.key_columns)
        where = f"{target.column} IS NOT NULL"
        params: list[Any] = []
        if target.restrict_column is not None:
            if not target.restrict_values:
                report.scanned[target.label] = 0
                report.rotated[target.label] = 0
                continue
            where += f" AND {target.restrict_column} = ANY(%s)"
            params.append(list(target.restrict_values))
        cur.execute(
            f"SELECT {key_list}, {target.column} AS ciphertext FROM {target.table} WHERE {where}",  # noqa: S608 - column/table names from encrypted_columns(), never user input
            params,
        )
        rows = cur.fetchall()
        report.scanned[target.label] = len(rows)
        rotated = 0
        for row in rows:
            token = row["ciphertext"]
            try:
                stale = needs_rotation(token)
            except ValueError as exc:
                identity = ", ".join(f"{col}={row[col]}" for col in target.key_columns)
                raise RotationError(
                    f"Cannot read {target.label} ({identity}) with any configured key: {exc}"
                ) from exc
            if not stale:
                continue
            rotated += 1
            if dry_run:
                continue
            match = " AND ".join(f"{col} = %s" for col in target.key_columns)
            cur.execute(
                f"UPDATE {target.table} SET {target.column} = %s WHERE {match}",
                [reencrypt(token), *(row[col] for col in target.key_columns)],
            )
        report.rotated[target.label] = rotated
    return report
