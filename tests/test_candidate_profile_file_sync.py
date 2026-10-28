# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-10-28
"""backend/candidate_profile_file_sync.py against the real test database: the
file-is-newer decision, the backup of stored-only edits, and the settings route
that re-points the watcher."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import pytest
from flask import Flask
from watchdog.events import FileModifiedEvent, FileMovedEvent

import config
from backend import candidate_profile_file_sync as sync_module
from backend.candidate_profile_file_sync import (
    ACCOUNT_EMAIL_SETTING,
    LAST_SYNCED_HASH_SETTING,
    PATH_SETTING,
    CandidateProfileFileSync,
    SyncOutcome,
    _ProfileFileEventHandler,
)
from backend.db import PostgresUserAccountRepository, get_connection
from backend.scoring import jd_enrichment
from backend.settings_service import get_setting, set_setting
from backend.utils.utils import get_repos
from routes.settings import settings_bp

OLD_TIMESTAMP = "2026-01-01T00:00:00+00:00"
# Well before OLD_TIMESTAMP, for a file the stored profile is newer than.
OLDER_MTIME = 1_700_000_000  # 2023-11-14


@pytest.fixture
def owner_id(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(config, "DATABASE_URL", pg_dsn)
    conn = get_connection(pg_dsn)
    try:
        account = PostgresUserAccountRepository(conn).create_pending(
            "owner@example.com", "seed-owner", "2099-01-01"
        )
        conn.commit()
    finally:
        conn.close()
    return int(account["id"])


@pytest.fixture
def rescored(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Re-scoring makes an AI call per job; record that it was asked for instead."""
    calls: list[int] = []

    def fake_rescore(user_id: int) -> dict[str, int]:
        calls.append(user_id)
        return {"rescored": 0, "failed": 0}

    monkeypatch.setattr(sync_module, "rescore_active_queue_jobs", fake_rescore)
    return calls


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


def _run_sync() -> SyncOutcome:
    file_sync = CandidateProfileFileSync()
    return file_sync.sync(file_sync.load_settings())


def _backups(profile: Path) -> list[Path]:
    return sorted(profile.parent.glob(f"{profile.stem}.db-backup-*{profile.suffix}"))


# ─── idle states ────────────────────────────────────────────────────────────────


def test_does_nothing_until_both_settings_are_set(owner_id: int, rescored: list[int]) -> None:
    assert _run_sync() == SyncOutcome.NOT_CONFIGURED


def test_rejects_a_relative_path(owner_id: int, rescored: list[int]) -> None:
    _configure("candidate_profile.md")
    assert _run_sync() == SyncOutcome.INVALID_PATH


def test_missing_file_leaves_the_stored_profile_alone(
    owner_id: int, rescored: list[int], tmp_path: Path
) -> None:
    _store(owner_id, "stored")
    _configure(str(tmp_path / "absent.md"))

    assert _run_sync() == SyncOutcome.FILE_MISSING
    assert _stored(owner_id) == "stored"


def test_empty_file_never_replaces_a_real_profile(
    owner_id: int, rescored: list[int], tmp_path: Path
) -> None:
    profile = tmp_path / "candidate_profile.md"
    profile.write_text("   \n", encoding="utf-8")
    _store(owner_id, "stored")
    _configure(str(profile))

    assert _run_sync() == SyncOutcome.INVALID_CONTENT
    assert _stored(owner_id) == "stored"


def test_email_with_no_account_is_reported(owner_id: int, rescored: list[int], tmp_path: Path) -> None:
    profile = tmp_path / "candidate_profile.md"
    profile.write_text("from file", encoding="utf-8")
    _configure(str(profile), email="nobody@example.com")

    assert _run_sync() == SyncOutcome.UNKNOWN_ACCOUNT


# ─── the newer-wins decision ────────────────────────────────────────────────────


def test_newer_file_replaces_the_stored_profile_and_rescores(
    owner_id: int, rescored: list[int], tmp_path: Path
) -> None:
    profile = tmp_path / "candidate_profile.md"
    profile.write_text("  from file\n", encoding="utf-8")
    _store(owner_id, "stored")
    # Mixed case: accounts are looked up casefolded, the same as at login.
    _configure(str(profile), email="Owner@Example.com")

    assert _run_sync() == SyncOutcome.SYNCED
    assert _stored(owner_id) == "from file"
    assert rescored == [owner_id]


def test_stored_profile_newer_than_the_file_is_kept(
    owner_id: int, rescored: list[int], tmp_path: Path
) -> None:
    """A learned rule appended after the file was last saved must survive."""
    profile = tmp_path / "candidate_profile.md"
    profile.write_text("older file", encoding="utf-8")
    os.utime(profile, (OLDER_MTIME, OLDER_MTIME))
    _store(owner_id, "stored with a learned rule")
    _configure(str(profile))

    assert _run_sync() == SyncOutcome.STORED_IS_NEWER
    assert _stored(owner_id) == "stored with a learned rule"
    assert rescored == []


def test_matching_content_is_up_to_date(owner_id: int, rescored: list[int], tmp_path: Path) -> None:
    profile = tmp_path / "candidate_profile.md"
    profile.write_text("same\n", encoding="utf-8")
    _store(owner_id, "same")
    _configure(str(profile))

    assert _run_sync() == SyncOutcome.UP_TO_DATE
    assert rescored == []


def test_first_sync_with_no_stored_profile_creates_it_without_a_backup(
    owner_id: int, rescored: list[int], tmp_path: Path
) -> None:
    profile = tmp_path / "candidate_profile.md"
    profile.write_text("from file", encoding="utf-8")
    _configure(str(profile))

    assert _run_sync() == SyncOutcome.SYNCED
    assert _stored(owner_id) == "from file"
    assert _backups(profile) == []


# ─── backups of stored-only edits ───────────────────────────────────────────────


def test_stored_text_never_synced_from_the_file_is_backed_up_before_replacement(
    owner_id: int, rescored: list[int], tmp_path: Path
) -> None:
    profile = tmp_path / "candidate_profile.md"
    profile.write_text("from file", encoding="utf-8")
    _store(owner_id, "stored-only text")
    _configure(str(profile))

    assert _run_sync() == SyncOutcome.SYNCED
    [backup] = _backups(profile)
    assert backup.read_text(encoding="utf-8") == "stored-only text\n"


def test_a_plain_file_edit_after_a_sync_makes_no_backup(
    owner_id: int, rescored: list[int], tmp_path: Path
) -> None:
    profile = tmp_path / "candidate_profile.md"
    profile.write_text("same", encoding="utf-8")
    _store(owner_id, "same")
    _configure(str(profile))
    assert _run_sync() == SyncOutcome.UP_TO_DATE  # records what's in sync
    _store(owner_id, "same")  # back-date updated_at so the next edit is newer

    profile.write_text("edited file", encoding="utf-8")

    assert _run_sync() == SyncOutcome.SYNCED
    assert _stored(owner_id) == "edited file"
    assert _backups(profile) == []


def test_a_learned_rule_added_since_the_last_sync_is_backed_up(
    owner_id: int, rescored: list[int], tmp_path: Path
) -> None:
    profile = tmp_path / "candidate_profile.md"
    profile.write_text("base", encoding="utf-8")
    _configure(str(profile))
    assert _run_sync() == SyncOutcome.SYNCED
    _store(owner_id, "base\n- learned rule")  # the learner, older than the next file save

    profile.write_text("base, edited", encoding="utf-8")

    assert _run_sync() == SyncOutcome.SYNCED
    [backup] = _backups(profile)
    assert "- learned rule" in backup.read_text(encoding="utf-8")
    with get_repos() as repos:
        assert get_setting(LAST_SYNCED_HASH_SETTING, None, repos) != ""


# ─── watcher event filtering ────────────────────────────────────────────────────


def test_watcher_wakes_only_for_the_profile_file(tmp_path: Path) -> None:
    profile = tmp_path / "candidate_profile.md"
    wake = threading.Event()
    handler = _ProfileFileEventHandler(profile, wake)

    handler.on_any_event(FileModifiedEvent(str(tmp_path / "Ross_Resume.pdf")))
    assert not wake.is_set()

    # Editors that save via write-temp-then-rename report a move onto the file.
    handler.on_any_event(FileMovedEvent(str(tmp_path / "profile.tmp"), str(profile)))
    assert wake.is_set()


# ─── settings route ─────────────────────────────────────────────────────────────


def test_saving_sync_settings_wakes_the_watcher(
    owner_id: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    woken: list[bool] = []
    monkeypatch.setattr("routes.settings.notify_sync_settings_changed", lambda: woken.append(True))
    flask_app = Flask(__name__)
    flask_app.register_blueprint(settings_bp, url_prefix="/api")
    client = flask_app.test_client()

    response = client.put("/api/settings/system", json={"smtp_host": "mail.example.com"})
    assert response.status_code == 200
    assert woken == []

    response = client.put("/api/settings/system", json={PATH_SETTING: str(tmp_path / "p.md")})
    assert response.status_code == 200
    assert response.get_json()[PATH_SETTING] == str(tmp_path / "p.md")
    assert woken == [True]


def test_last_synced_hash_is_not_writable_through_the_settings_route(owner_id: int) -> None:
    flask_app = Flask(__name__)
    flask_app.register_blueprint(settings_bp, url_prefix="/api")

    response = flask_app.test_client().put(
        "/api/settings/system", json={LAST_SYNCED_HASH_SETTING: "forged"}
    )

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
