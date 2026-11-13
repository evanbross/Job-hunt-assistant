# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""backend/ai_provider.py's AIProviderClient, specifically the per-account
settings resolution in __init__ against a real RLS-armed database.

2026-09-28 code review: AIProviderClient opens its own connection via
open_repositories() directly (a documented circular-import workaround --
backend.utils.utils imports AIProviderError from this module) instead of
get_repos(), and never called set_current_user() on it. Under Postgres
row-level security's fail-closed design, every per-account setting read in
__init__ -- including the API key -- silently came back as unset for every
account, for every provider. This broke every AI-powered feature (fit
scoring, cover letters, board search queries, career-site scoring) the
moment RLS went live; nothing caught it because every other test mocks
AIProviderClient out entirely rather than exercising its constructor
against a real database."""

from __future__ import annotations

import pytest

import config
from backend.ai_provider import AIMessage, AIProviderClient, AIProviderError
from backend.db import get_connection
from backend.db.repositories.user_accounts import PostgresUserAccountRepository
from backend.settings_service import set_setting
from backend.utils.utils import get_repos
from tests.db_roles import runtime_database_url

# ai: claude-sonnet-5 | 2026-09-28


@pytest.fixture(autouse=True)
def _test_db(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))


def _new_user(email: str) -> int:
    conn = get_connection(config.DATABASE_URL)
    # ai: claude-opus-5-5 | 2026-11-03 — close even on failure: a pooled connection left
    # checked out mid-transaction holds its locks until the run ends.
    try:
        account = PostgresUserAccountRepository(conn).create_pending(email, f"seed-{email}", "2099-01-01")
        conn.commit()
    finally:
        conn.close()
    assert account is not None, f"{email} already exists"
    return account["id"]


def test_reads_the_calling_accounts_own_api_key_and_provider() -> None:
    owner_id = _new_user("owner@example.com")
    other_id = _new_user("other@example.com")

    with get_repos(owner_id) as repos:
        set_setting("ai_provider", "anthropic", owner_id, repos)
        set_setting("anthropic_api_key", "sk-ant-owner-key", owner_id, repos)
        repos["conn"].commit()
    with get_repos(other_id) as repos:
        set_setting("ai_provider", "anthropic", other_id, repos)
        set_setting("anthropic_api_key", "sk-ant-other-key", other_id, repos)
        repos["conn"].commit()

    owner_client = AIProviderClient(owner_id)
    other_client = AIProviderClient(other_id)

    assert owner_client._provider == "anthropic"
    assert owner_client._anthropic_client is not None, (
        "the account's own API key must be visible to its own client"
    )
    assert other_client._anthropic_client is not None
    # Different accounts must get their own Anthropic SDK client, built from
    # their own key -- not share one client or silently read each other's.
    assert owner_client._anthropic_client is not other_client._anthropic_client


def test_falls_back_to_config_defaults_when_nothing_is_configured() -> None:
    user_id = _new_user("blank@example.com")

    client = AIProviderClient(user_id)

    assert client._provider == config.AI_PROVIDER.strip().lower()
    assert client._anthropic_client is None, "no API key configured -- must not fabricate a client"


# ai: claude-opus-5-5 | 2026-11-06 -- docs/cloud-deployment-readiness-plan.md 2.4
def test_a_hosted_server_refuses_a_stored_loopback_ollama_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """A URL saved before the hosted rule (or on the desktop) must still be refused
    when used: on a server, loopback is the app's own container."""
    user_id = _new_user("ollama@example.com")
    with get_repos(user_id) as repos:
        set_setting("ai_provider", "ollama", user_id, repos)
        set_setting("ollama_base_url", "http://127.0.0.1:8765", user_id, repos)
        repos["conn"].commit()
    monkeypatch.setattr(config, "APP_MODE", "hosted")
    connected: list[object] = []
    monkeypatch.setattr("backend.ai_provider.safe_urlopen", lambda *a, **k: connected.append(a))

    with pytest.raises(AIProviderError, match="Ollama URL refused"):
        AIProviderClient(user_id).complete(messages=[AIMessage(role="user", content="hi")])

    assert connected == []
