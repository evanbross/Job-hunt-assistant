# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-18
"""One-time account tokens (registration, email change, password reset) are
stored only as a digest (2026-11-18 full code review): a database snapshot must
not hold a working account link. Runs against the real schema, since the
guarantee lives in the repository's SQL and the migration's backfill."""

from __future__ import annotations

from typing import Any

import pytest

import config
from backend.db import get_connection
from backend.db.schema import migrate_schema
from backend.security.account_tokens import (
    PASSWORD_RESET_TOKEN_LIFETIME,
    REGISTRATION_TOKEN_LIFETIME,
    hash_account_token,
    new_token,
)
from backend.utils.utils import get_repos
from tests.db_roles import runtime_database_url


@pytest.fixture(autouse=True)
def _test_db(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))


def _stored_row(email: str) -> dict[str, Any]:
    with get_repos() as repos:
        row = repos["user_accounts"].get_by_email(email)
    assert row is not None
    return row


def _assert_token_not_stored(row: dict[str, Any], token: str) -> None:
    assert token not in {str(value) for value in row.values()}
    assert row["verification_token_hash"] == hash_account_token(token)


def test_a_registration_token_is_stored_only_as_its_digest_and_still_completes() -> None:
    token, expires_at = new_token(REGISTRATION_TOKEN_LIFETIME)
    with get_repos() as repos:
        repos["user_accounts"].create_pending("new@example.com", token, expires_at)
        repos["conn"].commit()

    _assert_token_not_stored(_stored_row("new@example.com"), token)

    with get_repos() as repos:
        assert repos["user_accounts"].complete_registration(hash_account_token(token), "h") is None
        account = repos["user_accounts"].complete_registration(token, "password-hash")
        repos["conn"].commit()
    assert account is not None and account["verification_token_hash"] is None


def test_a_password_reset_token_is_stored_only_as_its_digest_and_still_resets() -> None:
    registration, expires_at = new_token(REGISTRATION_TOKEN_LIFETIME)
    reset, reset_expires_at = new_token(PASSWORD_RESET_TOKEN_LIFETIME)
    with get_repos() as repos:
        repos["user_accounts"].create_pending("owner@example.com", registration, expires_at)
        repos["user_accounts"].complete_registration(registration, "old-hash")
        repos["user_accounts"].request_password_reset("owner@example.com", reset, reset_expires_at)
        repos["conn"].commit()

    _assert_token_not_stored(_stored_row("owner@example.com"), reset)

    with get_repos() as repos:
        account = repos["user_accounts"].reset_password(reset, "new-hash")
        repos["conn"].commit()
    assert account is not None and account["password_hash"] == "new-hash"


def test_an_email_change_token_is_stored_only_as_its_digest_and_still_confirms() -> None:
    registration, expires_at = new_token(REGISTRATION_TOKEN_LIFETIME)
    change, change_expires_at = new_token(REGISTRATION_TOKEN_LIFETIME)
    with get_repos() as repos:
        account = repos["user_accounts"].create_pending("old@example.com", registration, expires_at)
        repos["user_accounts"].complete_registration(registration, "hash")
        repos["user_accounts"].request_email_change(
            account["id"], "moved@example.com", change, change_expires_at
        )
        repos["conn"].commit()

    _assert_token_not_stored(_stored_row("old@example.com"), change)

    with get_repos() as repos:
        confirmed = repos["user_accounts"].confirm_email_change(change)
        repos["conn"].commit()
    assert confirmed is not None and confirmed["email"] == "moved@example.com"


def test_migrating_hashes_tokens_stored_before_hashing_so_emailed_links_keep_working(
    pg_dsn: str,
) -> None:
    """Puts user_accounts back in its pre-hashing shape (raw verification_token
    column), runs the real migration, and completes the registration with the raw
    token -- which only works if the migration's SQL digest equals hash_account_token."""
    token, expires_at = new_token(REGISTRATION_TOKEN_LIFETIME)
    conn = get_connection(pg_dsn)
    try:
        cur = conn.cursor()
        cur.execute(
            "ALTER TABLE user_accounts RENAME COLUMN verification_token_hash TO verification_token"
        )
        cur.execute(
            "ALTER TABLE user_accounts RENAME CONSTRAINT user_accounts_verification_token_hash_key"
            " TO user_accounts_verification_token_key"
        )
        cur.execute(
            "INSERT INTO user_accounts (email, password_hash, verification_token,"
            " verification_purpose, verification_expires_at)"
            " VALUES ('legacy@example.com', NULL, %s, 'registration', %s)",
            (token, expires_at),
        )
        conn.commit()

        migrate_schema(conn)
        migrate_schema(conn)  # idempotent: a second run must not hash the digest again
    finally:
        conn.close()

    _assert_token_not_stored(_stored_row("legacy@example.com"), token)
    with get_repos() as repos:
        account = repos["user_accounts"].complete_registration(token, "password-hash")
        repos["conn"].commit()
    assert account is not None and account["email"] == "legacy@example.com"
