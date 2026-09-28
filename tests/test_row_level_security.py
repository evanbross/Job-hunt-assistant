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

"""Postgres row-level security (backend/db/row_level_security.py) as a backstop behind
every repository's own WHERE-clause filtering.

These tests deliberately bypass the Python repository layer -- raw SQL, no WHERE user_id
-- because that is exactly the case RLS exists for: a filter that was never written.
tests/test_tenant_isolation_matrix.py covers the ordinary app-level path (every route,
through its repository); this file covers what happens when that layer is skipped
entirely, including the real gap this closed: routes/voice.py's LLM-generated SQL never
mentions user_id at all (see backend/sql_guard.py's ALLOWED_COLUMNS)."""

from __future__ import annotations

import psycopg2
import psycopg2.extras
import pytest

import app as app_module
import config
from backend.db.row_level_security import set_current_user, tenant_tables
from backend.utils.utils import get_repos
from routes.voice import _execute_readonly

# ai: claude-sonnet-5 | 2026-09-27


@pytest.fixture(autouse=True)
def _test_db(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # pg_dsn itself already points config.POSTGRES_HOST/PORT/DB/SSLMODE at the test
    # database (needed for get_readonly_database_url() below); DATABASE_URL is separate.
    monkeypatch.setattr(config, "DATABASE_URL", pg_dsn)


def _raw_connection(dsn: str) -> psycopg2.extensions.connection:
    return psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)


def _seed_job(dsn: str, user_id: int, dedup_key: str) -> int:
    conn = _raw_connection(dsn)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        set_current_user(cur, user_id)  # WITH CHECK now requires this to match, by design
        cur.execute(
            "INSERT INTO jobs (user_id, dedup_key, company, job_title, url, source,"
            " status, date_found) VALUES (%s, %s, 'Acme', 'Engineer',"
            " %s, 'email', 'pending_review', '2026-09-27') RETURNING id",
            (user_id, dedup_key, f"https://example.com/{dedup_key}"),
        )
        return int(cur.fetchone()["id"])
    finally:
        conn.close()


def _two_users(pg_dsn: str) -> tuple[int, int]:
    conn = _raw_connection(pg_dsn)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        ids = []
        for email in ("rls-a@example.com", "rls-b@example.com"):
            cur.execute(
                "INSERT INTO user_accounts (email, password_hash, role)"
                " VALUES (%s, 'x', 'user') RETURNING id",
                (email,),
            )
            ids.append(int(cur.fetchone()["id"]))
        return ids[0], ids[1]
    finally:
        conn.close()


# ─── the policy itself, with the app's own (table-owning) role ─────────────────


def test_an_unfiltered_select_only_returns_the_current_users_rows(pg_dsn: str) -> None:
    user_a, user_b = _two_users(pg_dsn)
    _seed_job(pg_dsn, user_a, "a-job")
    _seed_job(pg_dsn, user_b, "b-job")

    conn = _raw_connection(pg_dsn)
    try:
        cur = conn.cursor()
        set_current_user(cur, user_a)
        cur.execute("SELECT dedup_key FROM jobs")  # no WHERE at all
        rows = {r["dedup_key"] for r in cur.fetchall()}
    finally:
        conn.close()

    assert rows == {"a-job"}


def test_no_session_identity_means_no_rows_not_all_rows(pg_dsn: str) -> None:
    """Fail closed: forgetting to set the identity must not leak everyone's data."""
    user_a, _ = _two_users(pg_dsn)
    _seed_job(pg_dsn, user_a, "a-job")

    conn = _raw_connection(pg_dsn)
    try:
        cur = conn.cursor()
        cur.execute("SELECT dedup_key FROM jobs")  # app.current_user_id never set
        rows = cur.fetchall()
    finally:
        conn.close()

    assert rows == []


def test_an_insert_for_a_different_user_is_rejected(pg_dsn: str) -> None:
    """WITH CHECK, not just USING: a write, not only a read, is also policed."""
    user_a, user_b = _two_users(pg_dsn)

    conn = _raw_connection(pg_dsn)
    try:
        cur = conn.cursor()
        set_current_user(cur, user_a)
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute(
                "INSERT INTO jobs (user_id, dedup_key, company, job_title, url, source,"
                " status, date_found) VALUES (%s, 'sneaky', 'Acme', 'Eng',"
                " 'https://example.com/sneaky', 'email', 'pending_review', '2026-09-27')",
                (user_b,),
            )
    finally:
        conn.rollback()
        conn.close()


def test_switching_identity_on_the_same_connection_switches_visibility(pg_dsn: str) -> None:
    user_a, user_b = _two_users(pg_dsn)
    _seed_job(pg_dsn, user_a, "a-job")
    _seed_job(pg_dsn, user_b, "b-job")

    conn = _raw_connection(pg_dsn)
    try:
        cur = conn.cursor()
        set_current_user(cur, user_a)
        cur.execute("SELECT dedup_key FROM jobs")
        as_a = {r["dedup_key"] for r in cur.fetchall()}
        set_current_user(cur, user_b)
        cur.execute("SELECT dedup_key FROM jobs")
        as_b = {r["dedup_key"] for r in cur.fetchall()}
    finally:
        conn.close()

    assert (as_a, as_b) == ({"a-job"}, {"b-job"})


# ─── get_repos(): the app's own choke point ─────────────────────────────────────


def test_get_repos_with_an_explicit_user_id_is_scoped(pg_dsn: str) -> None:
    user_a, user_b = _two_users(pg_dsn)
    _seed_job(pg_dsn, user_a, "a-job")
    _seed_job(pg_dsn, user_b, "b-job")

    with get_repos(user_a) as repos:
        cur = repos["conn"].cursor()
        cur.execute("SELECT dedup_key FROM jobs")  # still no WHERE
        rows = {r["dedup_key"] for r in cur.fetchall()}

    assert rows == {"a-job"}


def test_get_repos_outside_a_request_with_no_user_id_sees_nothing(pg_dsn: str) -> None:
    user_a, _ = _two_users(pg_dsn)
    _seed_job(pg_dsn, user_a, "a-job")

    with get_repos() as repos:  # no Flask request context in a plain test function
        cur = repos["conn"].cursor()
        cur.execute("SELECT dedup_key FROM jobs")
        rows = cur.fetchall()

    assert rows == []


def test_get_repos_inside_a_request_uses_the_session_user(pg_dsn: str) -> None:
    user_a, user_b = _two_users(pg_dsn)
    _seed_job(pg_dsn, user_a, "a-job")
    _seed_job(pg_dsn, user_b, "b-job")

    with app_module.app.test_request_context("/"):
        from flask import session

        session["user_id"] = user_a
        with get_repos() as repos:
            cur = repos["conn"].cursor()
            cur.execute("SELECT dedup_key FROM jobs")
            rows = {r["dedup_key"] for r in cur.fetchall()}

    assert rows == {"a-job"}


def test_identity_survives_a_rollback_later_in_the_same_connection(pg_dsn: str) -> None:
    """get_repos() commits the identity before yielding, specifically so a later
    IntegrityError-and-retry elsewhere in the request can't silently undo it."""
    user_a, _ = _two_users(pg_dsn)
    _seed_job(pg_dsn, user_a, "a-job")

    with get_repos(user_a) as repos:
        conn = repos["conn"]
        cur = conn.cursor()
        cur.execute("SELECT 1")  # start a fresh transaction on this connection
        conn.rollback()  # unrelated mid-request rollback
        cur = conn.cursor()
        cur.execute("SELECT dedup_key FROM jobs")
        rows = {r["dedup_key"] for r in cur.fetchall()}

    assert rows == {"a-job"}


# ─── extension_tokens: deliberately excluded ────────────────────────────────────


def test_extension_tokens_has_no_rls_policy(pg_dsn: str) -> None:
    """Its own repository-level WHERE user_id = %s filtering is the only guard -- RLS
    would make ExtensionTokenService.authenticate()'s by-hash lookup impossible, since
    the account it belongs to is what that lookup is determining."""
    conn = _raw_connection(pg_dsn)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'extension_tokens'"
        )
        assert cur.fetchone() is None
        cur.execute(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'extension_tokens'"
        )
        assert cur.fetchone()["relrowsecurity"] is False
    finally:
        conn.close()


def test_a_token_lookup_works_with_no_identity_set(pg_dsn: str) -> None:
    user_a, _ = _two_users(pg_dsn)
    conn = _raw_connection(pg_dsn)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO extension_tokens (user_id, token_hash, token_prefix, label,"
            " created_at) VALUES (%s, 'hash-x', 'jha_x', 'test', '2026-09-27T00:00:00+00:00')",
            (user_a,),
        )
    finally:
        conn.close()

    conn = _raw_connection(pg_dsn)  # a fresh connection, no app.current_user_id ever set
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM extension_tokens WHERE token_hash = 'hash-x'")
        row = cur.fetchone()
    finally:
        conn.close()

    assert row is not None and row["user_id"] == user_a


# ─── migrations run RLS-free, and re-arm it every startup ───────────────────────


def test_disable_and_enable_are_idempotent(pg_dsn: str) -> None:
    from backend.db import init_db

    init_db(pg_dsn)  # already armed once by the pg_dsn fixture; must not error re-running
    init_db(pg_dsn)


def test_every_tenant_table_is_armed_after_init_db(pg_dsn: str) -> None:
    conn = _raw_connection(pg_dsn)
    try:
        cur = conn.cursor()
        tables = tenant_tables(cur)
        assert "jobs" in tables and "extension_tokens" not in tables
        for table in tables:
            cur.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = %s", (table,))
            row = cur.fetchone()
            assert row["relrowsecurity"] and row["relforcerowsecurity"], table
    finally:
        conn.close()


# ─── the latest_application_stage view ──────────────────────────────────────────


def test_the_view_is_scoped_through_its_underlying_table(pg_dsn: str) -> None:
    user_a, user_b = _two_users(pg_dsn)
    job_a = _seed_job(pg_dsn, user_a, "a-job")
    job_b = _seed_job(pg_dsn, user_b, "b-job")
    conn = _raw_connection(pg_dsn)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        for job_id, user_id in ((job_a, user_a), (job_b, user_b)):
            set_current_user(cur, user_id)
            cur.execute(
                "INSERT INTO application_stages (job_id, user_id, status, date)"
                " VALUES (%s, %s, 'applied', '2026-09-27')",
                (job_id, user_id),
            )
    finally:
        conn.close()

    conn = _raw_connection(pg_dsn)
    try:
        cur = conn.cursor()
        set_current_user(cur, user_a)
        cur.execute("SELECT job_id FROM latest_application_stage")
        visible = {r["job_id"] for r in cur.fetchall()}
    finally:
        conn.close()

    assert visible == {job_a}


# ─── the real bug this fixes: voice-query's LLM-generated SQL never says user_id ────


def test_execute_readonly_scopes_an_unfiltered_generated_query(pg_dsn: str) -> None:
    """The exact shape backend/sql_guard.py allows through: a plain SELECT with no
    user_id anywhere (that column isn't even in its ALLOWED_COLUMNS). Before this fix,
    this returned every account's jobs; RLS is what actually confines it now."""
    user_a, user_b = _two_users(pg_dsn)
    _seed_job(pg_dsn, user_a, "a-job")
    _seed_job(pg_dsn, user_b, "b-job")

    columns, rows, _truncated = _execute_readonly(
        "SELECT dedup_key FROM jobs ORDER BY dedup_key", user_a
    )

    assert columns == ["dedup_key"]
    assert [r["dedup_key"] for r in rows] == ["a-job"]


def test_voice_query_endpoint_never_returns_another_accounts_jobs(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through POST /api/voice/query, with intent classification stubbed
    (it calls an AI provider) to return SQL a real LLM would plausibly generate --
    unfiltered, because it was never told a user_id column exists."""
    from werkzeug.security import generate_password_hash

    conn = _raw_connection(pg_dsn)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO user_accounts (email, password_hash, role)"
            " VALUES ('voice-a@example.com', %s, 'user') RETURNING id",
            (generate_password_hash("correct horse battery staple"),),
        )
        user_a = int(cur.fetchone()["id"])
        cur.execute(
            "INSERT INTO user_accounts (email, password_hash, role)"
            " VALUES ('voice-b@example.com', 'x', 'user') RETURNING id"
        )
        user_b = int(cur.fetchone()["id"])
    finally:
        conn.close()
    _seed_job(pg_dsn, user_a, "a-job")
    _seed_job(pg_dsn, user_b, "b-job")

    # "url" (not "dedup_key" -- backend/sql_guard.py's ALLOWED_COLUMNS is narrower than
    # the real schema, and this goes through that validator, unlike the direct
    # _execute_readonly() call in the test above).
    monkeypatch.setattr(
        "routes.voice.classify",
        lambda text, user_id: {"type": "sql_query", "sql": "SELECT url FROM jobs"},
    )

    app_module.app.config["TESTING"] = True
    client = app_module.app.test_client()
    login = client.post(
        "/api/login", json={"email": "voice-a@example.com", "password": "correct horse battery staple"}
    )
    assert login.status_code == 200, login.get_json()

    resp = client.post("/api/voice/query", json={"text": "list my jobs"})

    assert resp.status_code == 200, resp.get_json()
    rows = resp.get_json()["rows"]
    assert [r["url"] for r in rows] == ["https://example.com/a-job"]
