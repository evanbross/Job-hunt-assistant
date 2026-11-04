# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-10-28
"""backend/candidate_profile_file_sync.py against the real test database: when
a profile file is offered for loading, what loading and dismissing it do, and
the routes and watcher that put it in front of the account."""

from __future__ import annotations

import os
import threading
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from watchdog.events import FileModifiedEvent, FileMovedEvent

import backend.candidate_profile_file_sync as file_sync_module
import config
import routes.settings as settings_routes
from backend.candidate_profile_file_sync import (
    ACCOUNT_EMAIL_SETTING,
    DISMISSED_HASH_SETTING,
    LAST_SYNCED_HASH_SETTING,
    PATH_SETTING,
    CandidateProfileFileSync,
    CandidateProfileFileWatcher,
    FileChangedError,
    FileSyncState,
    _ProfileFileEventHandler,
)
from backend.cover_letter.cover_letter_learning import CoverLetterStyleLearner
from backend.db import PostgresUserAccountRepository, get_connection
from backend.scoring import jd_enrichment
from backend.settings_service import get_setting, set_setting
from backend.user_events import UserEventTopic
from backend.utils.utils import get_repos
from routes.settings import settings_bp
from tests.conftest import RecordingUserEvents

OLD_TIMESTAMP = "2026-01-01T00:00:00+00:00"
# Well before OLD_TIMESTAMP, for a file the stored profile is newer than.
OLDER_MTIME = 1_700_000_000  # 2023-11-14
_PROFILE_FILE = UserEventTopic.CANDIDATE_PROFILE_FILE


def _create_account(pg_dsn: str, email: str) -> int:
    conn = get_connection(pg_dsn)
    try:
        account = PostgresUserAccountRepository(conn).create_pending(email, f"seed-{email}", "2099-01-01")
        conn.commit()
    finally:
        conn.close()
    return int(account["id"])


@pytest.fixture
def owner_id(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(config, "DATABASE_URL", pg_dsn)
    return _create_account(pg_dsn, "owner@example.com")


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    return tmp_path / "candidate_profile.md"


@pytest.fixture
def file_sync() -> CandidateProfileFileSync:
    return CandidateProfileFileSync()


def _configure(path: str, email: str = "owner@example.com") -> None:
    with get_repos() as repos:
        set_setting(PATH_SETTING, path, None, repos)
        set_setting(ACCOUNT_EMAIL_SETTING, email, None, repos)
        repos["conn"].commit()


def _store(user_id: int, content: str, updated_at: str = OLD_TIMESTAMP) -> None:
    with get_repos(user_id) as repos:
        repos["candidate_profiles"].upsert(user_id, content, updated_at)
        repos["conn"].commit()


def _stored(user_id: int) -> str | None:
    with get_repos(user_id) as repos:
        row = repos["candidate_profiles"].get(user_id)
    return None if row is None else str(row["content"])


def _backups(profile_path: Path) -> list[Path]:
    return sorted(profile_path.parent.glob(f"{profile_path.stem}.db-backup-*{profile_path.suffix}"))


def _offered(file_sync: CandidateProfileFileSync, user_id: int) -> str:
    """The file version on offer; fails the test if none is."""
    status = file_sync.status(user_id)
    assert status.state == FileSyncState.RELOAD_AVAILABLE
    return status.file_sha256


# ─── what is offered ────────────────────────────────────────────────────────────


def test_nothing_is_offered_until_both_settings_are_set(
    owner_id: int, file_sync: CandidateProfileFileSync
) -> None:
    assert file_sync.status(owner_id).state == FileSyncState.NOT_CONFIGURED


def test_another_accounts_file_is_not_offered(
    owner_id: int, pg_dsn: str, file_sync: CandidateProfileFileSync, profile: Path
) -> None:
    other_id = _create_account(pg_dsn, "other@example.com")
    profile.write_text("from file", encoding="utf-8")
    _configure(str(profile))

    assert file_sync.status(other_id).state == FileSyncState.OTHER_ACCOUNT


@pytest.mark.parametrize(
    ("path_text", "content", "expected"),
    [
        ("candidate_profile.md", None, FileSyncState.INVALID_PATH),
        ("{dir}/absent.md", None, FileSyncState.FILE_MISSING),
        # An emptied or half-saved file must never be offered over a real profile.
        ("{dir}/candidate_profile.md", "   \n", FileSyncState.INVALID_CONTENT),
    ],
)
def test_an_unusable_file_is_not_offered(
    owner_id: int,
    file_sync: CandidateProfileFileSync,
    tmp_path: Path,
    path_text: str,
    content: str | None,
    expected: FileSyncState,
) -> None:
    if content is not None:
        (tmp_path / "candidate_profile.md").write_text(content, encoding="utf-8")
    _configure(path_text.format(dir=tmp_path))

    assert file_sync.status(owner_id).state == expected


def test_a_newer_file_is_offered_without_touching_the_stored_profile(
    owner_id: int, file_sync: CandidateProfileFileSync, profile: Path
) -> None:
    profile.write_text("  from file\n", encoding="utf-8")
    _store(owner_id, "stored")
    # Mixed case: accounts are looked up casefolded, the same as at login.
    _configure(str(profile), email="Owner@Example.com")

    status = file_sync.status(owner_id)

    assert status.state == FileSyncState.RELOAD_AVAILABLE
    assert len(status.file_sha256) == 64
    assert status.path == str(profile)
    assert _stored(owner_id) == "stored"


def test_a_stored_profile_newer_than_the_file_is_not_offered_for_replacement(
    owner_id: int, file_sync: CandidateProfileFileSync, profile: Path
) -> None:
    """A learned rule appended after the file was last saved must survive."""
    profile.write_text("older file", encoding="utf-8")
    os.utime(profile, (OLDER_MTIME, OLDER_MTIME))
    _store(owner_id, "stored with a learned rule")
    _configure(str(profile))

    assert file_sync.status(owner_id).state == FileSyncState.STORED_IS_NEWER


def test_matching_content_is_up_to_date(
    owner_id: int, file_sync: CandidateProfileFileSync, profile: Path
) -> None:
    profile.write_text("same\n", encoding="utf-8")
    _store(owner_id, "same")
    _configure(str(profile))

    assert file_sync.status(owner_id).state == FileSyncState.UP_TO_DATE


def test_a_file_is_offered_to_an_account_with_no_stored_profile(
    owner_id: int, file_sync: CandidateProfileFileSync, profile: Path
) -> None:
    profile.write_text("from file", encoding="utf-8")
    _configure(str(profile))

    assert file_sync.status(owner_id).state == FileSyncState.RELOAD_AVAILABLE


def test_the_review_diff_shows_what_loading_would_remove_and_add(
    owner_id: int, file_sync: CandidateProfileFileSync, profile: Path
) -> None:
    profile.write_text("shared line\nonly in file\n", encoding="utf-8")
    _store(owner_id, "shared line\n- learned rule only stored")
    _configure(str(profile))

    status, diff = file_sync.review(owner_id)

    assert status.state == FileSyncState.RELOAD_AVAILABLE
    assert "-- learned rule only stored" in diff
    assert "+only in file" in diff
    assert " shared line" in diff


# ─── dismissing ─────────────────────────────────────────────────────────────────


def test_a_dismissed_version_stops_being_offered_until_the_file_changes(
    owner_id: int,
    file_sync: CandidateProfileFileSync,
    profile: Path,
    isolated_user_events: RecordingUserEvents,
) -> None:
    profile.write_text("version one", encoding="utf-8")
    _store(owner_id, "stored")
    _configure(str(profile))

    file_sync.dismiss(owner_id, _offered(file_sync, owner_id))

    assert file_sync.status(owner_id).state == FileSyncState.DISMISSED
    assert _stored(owner_id) == "stored"
    assert isolated_user_events.published == [(owner_id, _PROFILE_FILE)]

    profile.write_text("version two", encoding="utf-8")
    assert file_sync.status(owner_id).state == FileSyncState.RELOAD_AVAILABLE


def test_dismissing_a_version_no_longer_on_disk_is_refused(
    owner_id: int, file_sync: CandidateProfileFileSync, profile: Path
) -> None:
    profile.write_text("version one", encoding="utf-8")
    _configure(str(profile))
    reviewed = _offered(file_sync, owner_id)
    profile.write_text("version two", encoding="utf-8")

    with pytest.raises(FileChangedError):
        file_sync.dismiss(owner_id, reviewed)
    with get_repos() as repos:
        assert get_setting(DISMISSED_HASH_SETTING, None, repos) == ""


# ─── loading ────────────────────────────────────────────────────────────────────


def test_loading_the_reviewed_version_replaces_the_stored_profile(
    owner_id: int,
    file_sync: CandidateProfileFileSync,
    profile: Path,
    isolated_user_events: RecordingUserEvents,
) -> None:
    profile.write_text("from file", encoding="utf-8")
    _configure(str(profile))

    result = file_sync.load(owner_id, _offered(file_sync, owner_id))

    assert _stored(owner_id) == "from file"
    assert result.backup_path == ""  # nothing was stored before
    assert file_sync.status(owner_id).state == FileSyncState.UP_TO_DATE
    assert isolated_user_events.published == [(owner_id, _PROFILE_FILE)]


def test_loading_a_version_no_longer_on_disk_is_refused(
    owner_id: int, file_sync: CandidateProfileFileSync, profile: Path
) -> None:
    profile.write_text("version one", encoding="utf-8")
    _store(owner_id, "stored")
    _configure(str(profile))
    reviewed = _offered(file_sync, owner_id)
    profile.write_text("version two, never reviewed", encoding="utf-8")

    with pytest.raises(FileChangedError):
        file_sync.load(owner_id, reviewed)
    assert _stored(owner_id) == "stored"


def test_stored_text_never_loaded_from_the_file_is_backed_up_before_replacement(
    owner_id: int, file_sync: CandidateProfileFileSync, profile: Path
) -> None:
    profile.write_text("from file", encoding="utf-8")
    _store(owner_id, "stored-only text")
    _configure(str(profile))

    result = file_sync.load(owner_id, _offered(file_sync, owner_id))

    [backup] = _backups(profile)
    assert result.backup_path == str(backup)
    assert backup.read_text(encoding="utf-8") == "stored-only text\n"


class _FrozenDatetime(datetime):
    """Every now() is the same instant, down to the microsecond."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> _FrozenDatetime:
        return cls(2026, 11, 3, 12, 0, 0, 123456, tzinfo=tz)


# ai: claude-opus-5-5 | 2026-11-03 — code-review-2026-11-02 #2
def test_two_backups_at_the_same_instant_both_survive(
    owner_id: int, file_sync: CandidateProfileFileSync, profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backup name used to be second-precision and written with a
    truncating write, so the second load in the same second replaced the
    first backup. Two loads, each over stored-only text, at one frozen instant."""
    monkeypatch.setattr(file_sync_module, "datetime", _FrozenDatetime)
    _configure(str(profile))

    profile.write_text("file version one", encoding="utf-8")
    _store(owner_id, "first stored-only text")
    first = file_sync.load(owner_id, _offered(file_sync, owner_id))
    profile.write_text("file version two", encoding="utf-8")
    _store(owner_id, "second stored-only text")
    second = file_sync.load(owner_id, _offered(file_sync, owner_id))

    assert first.backup_path != second.backup_path
    assert second.backup_path.endswith(f"-1{profile.suffix}")
    assert {path.read_text(encoding="utf-8") for path in _backups(profile)} == {
        "first stored-only text\n",
        "second stored-only text\n",
    }
    assert _stored(owner_id) == "file version two"


def test_reloading_after_a_plain_file_edit_makes_no_backup(
    owner_id: int, file_sync: CandidateProfileFileSync, profile: Path
) -> None:
    profile.write_text("base", encoding="utf-8")
    _configure(str(profile))
    file_sync.load(owner_id, _offered(file_sync, owner_id))
    _store(owner_id, "base")  # back-date updated_at so the next edit is newer

    profile.write_text("edited file", encoding="utf-8")
    file_sync.load(owner_id, _offered(file_sync, owner_id))

    assert _stored(owner_id) == "edited file"
    assert _backups(profile) == []


def test_a_learned_rule_added_since_the_last_load_is_backed_up(
    owner_id: int, file_sync: CandidateProfileFileSync, profile: Path
) -> None:
    profile.write_text("base", encoding="utf-8")
    _configure(str(profile))
    file_sync.load(owner_id, _offered(file_sync, owner_id))
    _store(owner_id, "base\n- learned rule")  # the learner, older than the next file save

    profile.write_text("base, edited", encoding="utf-8")
    file_sync.load(owner_id, _offered(file_sync, owner_id))

    [backup] = _backups(profile)
    assert "- learned rule" in backup.read_text(encoding="utf-8")
    with get_repos() as repos:
        assert get_setting(LAST_SYNCED_HASH_SETTING, None, repos) != ""


# ─── who hears about a change ───────────────────────────────────────────────────


def test_the_learner_appending_a_rule_tells_the_accounts_pages(
    owner_id: int, isolated_user_events: RecordingUserEvents
) -> None:
    _store(owner_id, "profile")
    learner = CoverLetterStyleLearner.__new__(CoverLetterStyleLearner)  # skips the AI client
    learner._user_id = owner_id

    assert learner._append_rule("Never say synergy.") is True
    assert isolated_user_events.published == [(owner_id, _PROFILE_FILE)]


def test_watcher_wakes_only_for_the_profile_file(tmp_path: Path) -> None:
    target = tmp_path / "candidate_profile.md"
    wake = threading.Event()
    handler = _ProfileFileEventHandler(target, wake)

    handler.on_any_event(FileModifiedEvent(str(tmp_path / "Ross_Resume.pdf")))
    assert not wake.is_set()

    # Editors that save via write-temp-then-rename report a move onto the file.
    handler.on_any_event(FileMovedEvent(str(tmp_path / "profile.tmp"), str(target)))
    assert wake.is_set()


def test_watcher_signals_the_account_and_the_one_it_moved_from(
    owner_id: int,
    pg_dsn: str,
    file_sync: CandidateProfileFileSync,
    profile: Path,
    isolated_user_events: RecordingUserEvents,
) -> None:
    other_id = _create_account(pg_dsn, "other@example.com")
    watcher = CandidateProfileFileWatcher(file_sync)
    _configure(str(profile))

    watcher._refresh()
    assert isolated_user_events.published == [(owner_id, _PROFILE_FILE)]

    _configure(str(profile), email="other@example.com")
    watcher._refresh()
    assert sorted(isolated_user_events.published[1:]) == sorted(
        [(owner_id, _PROFILE_FILE), (other_id, _PROFILE_FILE)]
    )


# ─── routes ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(owner_id: int, monkeypatch: pytest.MonkeyPatch) -> FlaskClient:
    """Logged in as the owner, with re-scoring (an AI call per job) recorded."""
    flask_app = Flask(__name__)
    flask_app.secret_key = "test"
    flask_app.register_blueprint(settings_bp, url_prefix="/api")
    test_client = flask_app.test_client()
    with test_client.session_transaction() as flask_session:
        flask_session["user_id"] = owner_id
    rescored: list[int] = []
    monkeypatch.setattr(
        settings_routes,
        "rescore_active_queue_jobs",
        lambda user_id: rescored.append(user_id) or {"rescored": 0, "failed": 0},
    )
    test_client.rescored = rescored  # type: ignore[attr-defined]
    return test_client


def test_status_review_and_load_routes(client: FlaskClient, owner_id: int, profile: Path) -> None:
    profile.write_text("from file", encoding="utf-8")
    _store(owner_id, "stored")
    _configure(str(profile))

    status = client.get("/api/candidate-profile/file-sync").get_json()
    assert status["state"] == "reload_available"
    review = client.get("/api/candidate-profile/file-sync/review").get_json()
    assert "+from file" in review["diff"]

    stale = client.post("/api/candidate-profile/file-sync/load", json={"file_sha256": "0" * 64})
    assert stale.status_code == 409
    assert _stored(owner_id) == "stored"

    loaded = client.post(
        "/api/candidate-profile/file-sync/load", json={"file_sha256": status["file_sha256"]}
    )
    assert loaded.status_code == 200
    assert loaded.get_json()["backup_path"].endswith(".md")
    assert _stored(owner_id) == "from file"
    assert client.rescored == [owner_id]  # type: ignore[attr-defined]


def test_dismiss_route(client: FlaskClient, owner_id: int, profile: Path) -> None:
    profile.write_text("from file", encoding="utf-8")
    _configure(str(profile))
    file_sha256 = client.get("/api/candidate-profile/file-sync").get_json()["file_sha256"]

    response = client.post("/api/candidate-profile/file-sync/dismiss", json={"file_sha256": file_sha256})

    assert response.status_code == 200
    assert client.get("/api/candidate-profile/file-sync").get_json()["state"] == "dismissed"


@pytest.mark.parametrize("body", [{}, {"file_sha256": "not-a-hash"}, {"file_sha256": "0" * 64, "x": 1}])
def test_file_actions_reject_a_malformed_body(client: FlaskClient, body: dict[str, Any]) -> None:
    for action in ("load", "dismiss"):
        response = client.post(f"/api/candidate-profile/file-sync/{action}", json=body)
        assert response.status_code == 400


def test_saving_the_profile_in_settings_tells_the_accounts_pages(
    client: FlaskClient, owner_id: int, isolated_user_events: RecordingUserEvents
) -> None:
    response = client.put("/api/candidate-profile", json={"content": "edited in Settings"})

    assert response.status_code == 200
    assert isolated_user_events.published == [(owner_id, _PROFILE_FILE)]


def test_saving_sync_settings_wakes_the_watcher(
    owner_id: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    woken: list[bool] = []
    monkeypatch.setattr(settings_routes, "notify_sync_settings_changed", lambda: woken.append(True))
    flask_app = Flask(__name__)
    flask_app.register_blueprint(settings_bp, url_prefix="/api")
    test_client = flask_app.test_client()

    response = test_client.put("/api/settings/system", json={"smtp_host": "mail.example.com"})
    assert response.status_code == 200
    assert woken == []

    response = test_client.put("/api/settings/system", json={PATH_SETTING: str(tmp_path / "p.md")})
    assert response.status_code == 200
    assert response.get_json()[PATH_SETTING] == str(tmp_path / "p.md")
    assert woken == [True]


@pytest.mark.parametrize("hidden_key", [LAST_SYNCED_HASH_SETTING, DISMISSED_HASH_SETTING])
def test_sync_bookkeeping_is_not_writable_through_the_settings_route(
    owner_id: int, hidden_key: str
) -> None:
    flask_app = Flask(__name__)
    flask_app.register_blueprint(settings_bp, url_prefix="/api")

    response = flask_app.test_client().put("/api/settings/system", json={hidden_key: "forged"})

    assert response.status_code == 400


# ─── re-scoring opens its own connections ───────────────────────────────────────


class _FakeFitScorer:
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id

    def score(self, **_kwargs: Any) -> dict[str, Any]:
        return {"score": 77, "reasoning": "fake", "salary_min": None, "salary_max": None}


def test_rescore_writes_scores_without_a_caller_connection(
    owner_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jd_enrichment, "FitScorer", _FakeFitScorer)
    with get_repos(owner_id) as repos:
        job_id = repos["jobs"].insert(
            owner_id,
            {
                "dedup_key": "key-rescore",
                "company": "Acme",
                "job_title": "Engineer",
                "url": "https://example.com/j",
                "source": "email",
                "status": "pending_review",
                "date_found": "2026-10-28",
            },
        )
        repos["conn"].commit()

    assert jd_enrichment.rescore_active_queue_jobs(owner_id) == {"rescored": 1, "failed": 0}
    with get_repos(owner_id) as repos:
        assert repos["jobs"].get_by_id(owner_id, job_id)["fit_score"] == 77
