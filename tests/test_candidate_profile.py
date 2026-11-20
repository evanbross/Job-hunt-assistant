# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, session

import config
from backend.cover_letter.cover_letter_learning import (
    CoverLetterStyleLearner,
    insert_learned_rule,
)
from backend.db import (
    PostgresCandidateProfileRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.repositories.user_accounts import DEFAULT_ADMIN_EMAIL
from backend.db.row_level_security import set_current_user
from backend.db.schema import _migrate_legacy_candidate_profiles
from backend.scoring.fit_scorer import FitScorer, ScorerUserContext
from backend.utils.utils import (
    CandidateProfileMissingError,
    get_candidate_profile,
    get_repos,
)
from routes.settings import settings_bp
from routes.setup import setup_bp
from tests.db_roles import runtime_database_url
from tests.login_session import sign_in
from web import create_app

# ai: claude-sonnet-5 | 2026-09-26

LEARNED_HEADER = "## Learned Cover Letter Preferences (auto-captured)"


def _new_user(conn: Any, email: str = "owner@example.com") -> int:
    account = PostgresUserAccountRepository(conn).create_pending(
        email, f"seed-{email}", "2099-01-01"
    )
    return account["id"]


def _save(pg_dsn: str, user_id: int, content: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        set_current_user(conn.cursor(), user_id)
        PostgresCandidateProfileRepository(conn).upsert(user_id, content, "2026-01-01T00:00:00+00:00")
        conn.commit()
    finally:
        conn.close()


# ─── accessor ───────────────────────────────────────────────────────────────────


def test_each_user_reads_only_their_own_profile(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        first_id, second_id = _new_user(conn), _new_user(conn, "second@example.com")
        conn.commit()
    finally:
        conn.close()
    _save(pg_dsn, first_id, "  first user's career  ")
    _save(pg_dsn, second_id, "second user's career")

    with get_repos(first_id) as repos:
        assert get_candidate_profile(first_id, repos) == "first user's career"
    with get_repos(second_id) as repos:
        assert get_candidate_profile(second_id, repos) == "second user's career"


@pytest.mark.parametrize("stored", [None, "", "   \n  "])
def test_a_missing_or_blank_profile_raises_a_clear_error(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch, stored: str | None
) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        conn.commit()
    finally:
        conn.close()
    if stored is not None:
        _save(pg_dsn, user_id, stored)

    with get_repos(user_id) as repos, pytest.raises(CandidateProfileMissingError, match="Profile Settings"):
        get_candidate_profile(user_id, repos)


# ai: claude-opus-5-5 | 2026-11-05 -- through a real request, so this also proves the
# factory registers the handler (it used to call the handler function directly).
def test_the_missing_profile_error_is_a_409_not_a_500() -> None:
    app = create_app()

    @app.route("/raises-missing-profile")
    def _raises_missing_profile() -> str:
        raise CandidateProfileMissingError("add one")

    response = app.test_client().get("/raises-missing-profile")

    assert response.status_code == 409
    assert response.get_json() == {"error": "add one", "code": "candidate_profile_missing"}


def test_deleting_a_user_removes_their_profile(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        set_current_user(conn.cursor(), user_id)
        repo = PostgresCandidateProfileRepository(conn)
        repo.upsert(user_id, "text", "2026-01-01")
        PostgresUserAccountRepository(conn).delete(user_id)
        assert repo.get(user_id) is None
    finally:
        conn.close()


# ─── prompts use the caller's own profile ───────────────────────────────────────


def test_fit_scoring_prompt_contains_only_the_scored_users_profile(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        first_id, second_id = _new_user(conn), _new_user(conn, "second@example.com")
        conn.commit()
    finally:
        conn.close()
    _save(pg_dsn, first_id, "PROFILE-OF-THE-FIRST-USER")
    _save(pg_dsn, second_id, "PROFILE-OF-THE-SECOND-USER")

    with patch("backend.scoring.fit_scorer.AIProviderClient", return_value=MagicMock()):
        prompt = FitScorer(second_id)._build_system_prompt(
            ScorerUserContext.load(second_id), "resume", [], []
        )

    assert "PROFILE-OF-THE-SECOND-USER" in prompt
    assert "PROFILE-OF-THE-FIRST-USER" not in prompt


# ─── learned rules ──────────────────────────────────────────────────────────────


def test_insert_learned_rule_creates_the_section_and_appends_after_existing_bullets() -> None:
    once = insert_learned_rule("# Me\n\nStuff", "Never say synergy")
    assert once == f"# Me\n\nStuff\n\n{LEARNED_HEADER}\n- Never say synergy\n"

    twice = insert_learned_rule(once, "Keep it short")
    assert twice.splitlines()[-2:] == ["- Never say synergy", "- Keep it short"]


def test_insert_learned_rule_ignores_a_rule_already_present() -> None:
    assert insert_learned_rule(f"{LEARNED_HEADER}\n- Keep it short\n", "Keep it short") is None


def test_learned_rules_are_written_to_the_right_users_row(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        first_id, second_id = _new_user(conn), _new_user(conn, "second@example.com")
        conn.commit()
    finally:
        conn.close()
    _save(pg_dsn, first_id, "first profile")
    _save(pg_dsn, second_id, "second profile")

    assert CoverLetterStyleLearner(first_id)._append_rule("Avoid clichés") is True
    assert CoverLetterStyleLearner(first_id)._append_rule("Avoid clichés") is False

    with get_repos(first_id) as repos:
        assert "Avoid clichés" in get_candidate_profile(first_id, repos)
    with get_repos(second_id) as repos:
        assert get_candidate_profile(second_id, repos) == "second profile"


def test_learning_without_a_profile_does_nothing(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        conn.commit()
    finally:
        conn.close()

    assert CoverLetterStyleLearner(user_id)._append_rule("anything") is False


# ─── save routes ────────────────────────────────────────────────────────────────


@pytest.fixture
def profile_client(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, int], dict[str, Any], list[int]]:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        ids = {"a": _new_user(conn, "a@example.com"), "b": _new_user(conn, "b@example.com")}
        conn.commit()
    finally:
        conn.close()
    session_user = {"id": ids["a"]}
    rescored: list[int] = []

    def fake_rescore(user_id: int) -> dict[str, int]:
        rescored.append(user_id)
        return {"rescored": 3, "failed": 0}

    for module in ("routes.settings", "routes.setup"):
        monkeypatch.setattr(f"{module}.current_user_id", lambda: session_user["id"])
    monkeypatch.setattr("routes.settings.rescore_active_queue_jobs", fake_rescore)
    flask_app = Flask(__name__)
    flask_app.secret_key = "test"
    flask_app.register_blueprint(settings_bp, url_prefix="/api")
    flask_app.register_blueprint(setup_bp, url_prefix="/api")

    # get_repos() (backend/db/row_level_security.py) reads the real Flask session, not
    # the current_user_id() monkeypatch above -- keep it in sync with session_user, which
    # tests reassign to switch identity, so RLS sees whichever account is "logged in".
    @flask_app.before_request
    def _sync_rls_session() -> None:
        sign_in(session, session_user["id"])

    return flask_app.test_client(), ids, session_user, rescored


def test_put_then_get_round_trips_for_the_logged_in_user_only(profile_client: Any) -> None:
    client, ids, session_user, _ = profile_client

    response = client.put("/api/candidate-profile", json={"content": "  My career summary\n"})
    assert response.status_code == 200
    assert response.get_json()["char_count"] == len("My career summary")

    assert client.get("/api/candidate-profile").get_json()["content"] == "My career summary"
    session_user["id"] = ids["b"]
    assert client.get("/api/candidate-profile").get_json() == {
        "content": "", "char_count": 0, "updated_at": None,
    }


def test_active_jobs_are_rescored_only_when_the_profile_actually_changed(profile_client: Any) -> None:
    client, ids, _, rescored = profile_client

    first = client.put("/api/candidate-profile", json={"content": "v1"}).get_json()
    same = client.put("/api/candidate-profile", json={"content": "v1"}).get_json()
    changed = client.put("/api/candidate-profile", json={"content": "v2"}).get_json()

    assert first["profile_changed"] is True and first["rescore_summary"] == {"rescored": 3, "failed": 0}
    assert same["profile_changed"] is False and same["rescore_summary"] == {"rescored": 0, "failed": 0}
    assert changed["profile_changed"] is True
    assert rescored == [ids["a"], ids["a"]]


@pytest.mark.parametrize(
    "body",
    [{}, {"content": ""}, {"content": "   "}, {"content": 5}, {"content": "x" * 100_001}, {"content": "ok", "user_id": 2}],
)
def test_put_rejects_invalid_bodies(profile_client: Any, body: dict[str, Any]) -> None:
    client, _, _, rescored = profile_client
    assert client.put("/api/candidate-profile", json=body).status_code == 400
    assert client.get("/api/candidate-profile").get_json()["content"] == ""
    assert rescored == []


def test_the_setup_wizard_saves_to_the_same_place_without_rescoring(profile_client: Any) -> None:
    client, _, _, rescored = profile_client

    assert client.post("/api/setup/candidate-profile", json={"content": "wizard text"}).status_code == 200

    assert client.get("/api/candidate-profile").get_json()["content"] == "wizard text"
    assert rescored == []
    assert client.post("/api/setup/candidate-profile", json={"content": " "}).status_code == 400


def test_the_config_route_no_longer_exposes_a_profile_path(profile_client: Any) -> None:
    client, _, _, _ = profile_client
    body = client.get("/api/config").get_json()
    assert "candidate_profile_path" not in body


# ai: claude-opus-5-5 | 2026-11-09
def test_the_config_route_leaves_out_notify_addresses_when_hosted(
    profile_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Settings page saves back what GET returned, so a hosted GET must not hand
    it a desktop-only key the PUT would then refuse."""
    client, _, _, _ = profile_client
    notify_keys = {"career_scan_digest_notify_email", "jd_health_check_notify_email"}
    assert notify_keys <= client.get("/api/config").get_json().keys()

    monkeypatch.setattr(config, "APP_MODE", "hosted")
    body = client.get("/api/config").get_json()

    assert not notify_keys & body.keys()
    assert client.put("/api/config", json=body).status_code == 200


# ─── one-time migration of the legacy shared file ───────────────────────────────


def _admin_and_member(conn: Any) -> tuple[int, int]:
    admin = PostgresUserAccountRepository(conn).get_by_email(DEFAULT_ADMIN_EMAIL)["id"]
    return admin, _new_user(conn, "member@example.com")


def _set_path_setting(conn: Any, user_id: int, path: Path) -> None:
    conn.cursor().execute(
        "INSERT INTO user_settings (user_id, key, value) VALUES (%s, 'candidate_profile_path', %s)",
        (user_id, str(path)),
    )


def _profile_of(conn: Any, user_id: int) -> str | None:
    row = PostgresCandidateProfileRepository(conn).get(user_id)
    return row["content"] if row else None


def test_the_shared_default_file_goes_to_the_first_account_only(
    pg_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / "candidate_profile.md"
    shared.write_text("  the owner's profile  ", encoding="utf-8")
    monkeypatch.setattr(config, "LEGACY_CANDIDATE_PROFILE_PATH", shared)
    conn = get_connection(pg_dsn)
    try:
        admin, member = _admin_and_member(conn)

        _migrate_legacy_candidate_profiles(conn)

        assert _profile_of(conn, admin) == "the owner's profile"
        assert _profile_of(conn, member) is None
        assert shared.exists()
    finally:
        conn.close()


def test_an_account_with_its_own_configured_path_gets_that_file(
    pg_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared, mine = tmp_path / "candidate_profile.md", tmp_path / "mine.md"
    shared.write_text("owner profile", encoding="utf-8")
    mine.write_text("member profile", encoding="utf-8")
    monkeypatch.setattr(config, "LEGACY_CANDIDATE_PROFILE_PATH", shared)
    conn = get_connection(pg_dsn)
    try:
        admin, member = _admin_and_member(conn)
        _set_path_setting(conn, member, mine)

        _migrate_legacy_candidate_profiles(conn)

        assert _profile_of(conn, admin) == "owner profile"
        assert _profile_of(conn, member) == "member profile"
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM user_settings WHERE key = 'candidate_profile_path'")
        assert cur.fetchone()["n"] == 0
    finally:
        conn.close()


def test_migration_never_overwrites_a_saved_profile_and_skips_unreadable_paths(
    pg_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / "candidate_profile.md"
    shared.write_text("stale file", encoding="utf-8")
    monkeypatch.setattr(config, "LEGACY_CANDIDATE_PROFILE_PATH", shared)
    conn = get_connection(pg_dsn)
    try:
        admin, member = _admin_and_member(conn)
        PostgresCandidateProfileRepository(conn).upsert(admin, "already saved", "2026-01-01")
        _set_path_setting(conn, member, tmp_path / "does-not-exist.md")

        _migrate_legacy_candidate_profiles(conn)
        _migrate_legacy_candidate_profiles(conn)

        assert _profile_of(conn, admin) == "already saved"
        assert _profile_of(conn, member) is None
    finally:
        conn.close()
