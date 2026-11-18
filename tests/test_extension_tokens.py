# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import config
from backend.db import (
    PostgresExtensionTokenRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.extension_auth.token_service import (
    TOKEN_PREFIX,
    ExtensionTokenService,
    hash_token,
)
from backend.extension_auth.ws_tickets import WebSocketTicketStore
from tests.db_roles import runtime_database_url
from web.extension_listener import authenticate_extension_token

# ai: claude-sonnet-5 | 2026-09-26


def _new_user(conn: object, email: str = "owner@example.com") -> int:
    account = PostgresUserAccountRepository(conn).create_pending(
        email, f"seed-{email}", "2099-01-01"
    )
    return account["id"]


def test_issue_returns_plaintext_once_and_stores_only_a_hash(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        service = ExtensionTokenService(PostgresExtensionTokenRepository(conn))

        issued = service.issue(user_id, "Brave on desktop")
        conn.commit()

        assert issued.plaintext.startswith(TOKEN_PREFIX)
        assert "token_hash" not in issued.record
        assert issued.plaintext not in issued.record.values()
        assert issued.record["token_prefix"] == issued.plaintext[: len(issued.record["token_prefix"])]
        assert issued.record["label"] == "Brave on desktop"

        cur = conn.cursor()
        cur.execute("SELECT token_hash FROM extension_tokens WHERE id = %s", (issued.record["id"],))
        assert cur.fetchone()["token_hash"] == hash_token(issued.plaintext)
    finally:
        conn.close()


def test_authenticate_resolves_the_owning_user(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        first_id = _new_user(conn)
        second_id = _new_user(conn, "second@example.com")
        service = ExtensionTokenService(PostgresExtensionTokenRepository(conn))
        first = service.issue(first_id, "first")
        second = service.issue(second_id, "second")

        assert service.authenticate(first.plaintext).user_id == first_id
        assert service.authenticate(second.plaintext).user_id == second_id
    finally:
        conn.close()


@pytest.mark.parametrize(
    "presented",
    ["", "garbage", "jha_short", "jha_" + "x" * 200, "Bearer jha_" + "x" * 43],
)
def test_authenticate_rejects_malformed_or_unknown_tokens(pg_dsn: str, presented: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        _new_user(conn)
        service = ExtensionTokenService(PostgresExtensionTokenRepository(conn))
        assert service.authenticate(presented) is None
    finally:
        conn.close()


def test_authenticate_rejects_a_well_formed_token_that_was_never_issued(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        _new_user(conn)
        service = ExtensionTokenService(PostgresExtensionTokenRepository(conn))
        assert service.authenticate(TOKEN_PREFIX + "a" * 43) is None
    finally:
        conn.close()


def test_revoked_token_no_longer_authenticates(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        service = ExtensionTokenService(PostgresExtensionTokenRepository(conn))
        issued = service.issue(user_id, "laptop")
        assert service.authenticate(issued.plaintext) is not None

        assert service.revoke(user_id, issued.record["id"]) is True
        assert service.authenticate(issued.plaintext) is None
        assert service.revoke(user_id, issued.record["id"]) is False
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-18 -- used by a password change or reset
def test_revoke_all_ends_every_token_of_that_user_only(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        owner = _new_user(conn)
        other = _new_user(conn, "other@example.com")
        service = ExtensionTokenService(PostgresExtensionTokenRepository(conn))
        laptop = service.issue(owner, "laptop")
        desktop = service.issue(owner, "desktop")
        already_revoked = service.issue(owner, "old")
        service.revoke(owner, already_revoked.record["id"])
        others = service.issue(other, "theirs")

        assert service.revoke_all(owner) == 2

        assert service.authenticate(laptop.plaintext) is None
        assert service.authenticate(desktop.plaintext) is None
        assert service.authenticate(others.plaintext) is not None
        assert service.revoke_all(owner) == 0
    finally:
        conn.close()


def test_expired_token_no_longer_authenticates(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        service = ExtensionTokenService(PostgresExtensionTokenRepository(conn))
        issued_at = datetime.now(UTC)
        issued = service.issue(user_id, "short-lived", expires_in_days=1, now=issued_at)

        assert service.authenticate(issued.plaintext, now=issued_at + timedelta(hours=23)) is not None
        assert service.authenticate(issued.plaintext, now=issued_at + timedelta(days=1, seconds=1)) is None
    finally:
        conn.close()


def test_one_user_cannot_revoke_or_list_another_users_tokens(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        owner_id = _new_user(conn)
        other_id = _new_user(conn, "other@example.com")
        service = ExtensionTokenService(PostgresExtensionTokenRepository(conn))
        issued = service.issue(owner_id, "mine")

        assert service.revoke(other_id, issued.record["id"]) is False
        assert service.authenticate(issued.plaintext) is not None
        assert service.list_for_user(other_id) == []
        assert [t["label"] for t in service.list_for_user(owner_id)] == ["mine"]
    finally:
        conn.close()


def test_last_used_is_recorded_but_not_rewritten_on_every_request(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        service = ExtensionTokenService(PostgresExtensionTokenRepository(conn))
        issued = service.issue(user_id, "laptop")
        start = datetime.now(UTC)

        assert service.list_for_user(user_id)[0]["last_used_at"] is None
        service.authenticate(issued.plaintext, now=start)
        first_seen = service.list_for_user(user_id)[0]["last_used_at"]
        assert first_seen is not None

        service.authenticate(issued.plaintext, now=start + timedelta(seconds=10))
        assert service.list_for_user(user_id)[0]["last_used_at"] == first_seen

        service.authenticate(issued.plaintext, now=start + timedelta(minutes=5))
        assert service.list_for_user(user_id)[0]["last_used_at"] != first_seen
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("label", "expires_in_days"),
    [("", None), ("   ", None), ("x" * 101, None), ("ok", 0), ("ok", -3)],
)
def test_issue_validates_label_and_expiry(
    pg_dsn: str, label: str, expires_in_days: int | None
) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        service = ExtensionTokenService(PostgresExtensionTokenRepository(conn))
        with pytest.raises(ValueError):
            service.issue(user_id, label, expires_in_days=expires_in_days)
    finally:
        conn.close()


def test_deleting_a_user_removes_their_tokens(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        service = ExtensionTokenService(PostgresExtensionTokenRepository(conn))
        issued = service.issue(user_id, "laptop")

        PostgresUserAccountRepository(conn).delete(user_id)
        assert service.authenticate(issued.plaintext) is None
    finally:
        conn.close()


def test_ticket_is_single_use() -> None:
    store = WebSocketTicketStore()
    ticket = store.issue(42)

    assert store.redeem(ticket) == 42
    assert store.redeem(ticket) is None


def test_unknown_ticket_is_rejected() -> None:
    assert WebSocketTicketStore().redeem("not-a-ticket") is None


def test_expired_ticket_is_rejected_and_consumed() -> None:
    store = WebSocketTicketStore(ttl_seconds=0)
    ticket = store.issue(7)

    assert store.redeem(ticket) is None
    assert store.redeem(ticket) is None


def test_tickets_are_bound_to_the_issuing_user() -> None:
    store = WebSocketTicketStore()
    first, second = store.issue(1), store.issue(2)

    assert store.redeem(second) == 2
    assert store.redeem(first) == 1
    assert first != second


def test_app_authenticator_resolves_a_real_token_and_rejects_others(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        issued = ExtensionTokenService(PostgresExtensionTokenRepository(conn)).issue(user_id, "laptop")
        conn.commit()
    finally:
        conn.close()

    assert authenticate_extension_token(issued.plaintext) == user_id
    assert authenticate_extension_token(issued.plaintext + "x") is None
    assert authenticate_extension_token("") is None

    conn = get_connection(pg_dsn)
    try:
        used = ExtensionTokenService(PostgresExtensionTokenRepository(conn)).list_for_user(user_id)
    finally:
        conn.close()
    assert used[0]["last_used_at"] is not None
