# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-10-28
"""One-way sync from a candidate profile file on the server's disk into one
account's stored profile -- the candidate_profiles row that fit scoring, cover
letters, and pitches actually read.

The file wins only when its modification time is later than the stored
profile's updated_at, so a rule the cover-letter learner appends to the stored
copy isn't wiped by an older file that predates it. When the file does win but
the stored copy has changed since the last sync (it holds edits the file never
saw), the stored copy is written to a backup file beside the profile before
it's replaced: the sync is one-way by choice, but never silently discards text.

A filesystem watcher on the file's folder is the trigger. There is also a check
at startup (edits made while the app was closed) and a slow fallback re-check
for missed events. Which file and which account are admin-only system settings
(see backend/settings_registry.py for why they aren't per-user)."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import ValidationError
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver, ObservedWatch

import config
from backend.candidate_profile_service import CandidateProfileUpdate, save_candidate_profile
from backend.scoring.jd_enrichment import rescore_active_queue_jobs
from backend.settings_service import get_setting, set_setting
from backend.utils.utils import get_repos

logger = logging.getLogger(__name__)

PATH_SETTING = "candidate_profile_sync_path"
ACCOUNT_EMAIL_SETTING = "candidate_profile_sync_account_email"
LAST_SYNCED_HASH_SETTING = "candidate_profile_sync_last_synced_sha256"
SETTING_KEYS: frozenset[str] = frozenset({PATH_SETTING, ACCOUNT_EMAIL_SETTING})

# Editors often save as write-temp-then-rename, which arrives as a burst of
# events; waiting this long after the first one lets the burst settle so the
# sync reads the finished file once instead of a half-written one.
_DEBOUNCE_SECONDS = 1.0
# watchdog event types that mean the file's content may have changed.
_CONTENT_EVENT_TYPES: frozenset[str] = frozenset({"created", "modified", "moved", "closed"})


class SyncOutcome(StrEnum):
    NOT_CONFIGURED = "not_configured"
    INVALID_PATH = "invalid_path"
    FILE_MISSING = "file_missing"
    UNREADABLE_FILE = "unreadable_file"
    INVALID_CONTENT = "invalid_content"
    UNKNOWN_ACCOUNT = "unknown_account"
    UP_TO_DATE = "up_to_date"
    STORED_IS_NEWER = "stored_is_newer"
    BACKUP_FAILED = "backup_failed"
    SYNCED = "synced"


@dataclass(frozen=True)
class SyncSettings:
    path_text: str
    account_email: str
    last_synced_hash: str

    @property
    def path(self) -> Path | None:
        """The configured file, or None if unset or not an absolute path. A
        relative path would resolve against whatever the server's working
        directory happens to be, so it's rejected rather than guessed at."""
        if not self.path_text:
            return None
        candidate = Path(self.path_text)
        return candidate if candidate.is_absolute() else None


class CandidateProfileFileSync:
    """The sync decision itself -- no threads, so tests can drive it directly."""

    def __init__(self) -> None:
        # Serializes a watcher-triggered sync against a fallback-tick one.
        self._lock = threading.Lock()

    @staticmethod
    def load_settings() -> SyncSettings:
        with get_repos() as repos:
            return SyncSettings(
                path_text=get_setting(PATH_SETTING, None, repos).strip(),
                account_email=get_setting(ACCOUNT_EMAIL_SETTING, None, repos).strip().casefold(),
                last_synced_hash=get_setting(LAST_SYNCED_HASH_SETTING, None, repos),
            )

    def sync(self, settings: SyncSettings) -> SyncOutcome:
        with self._lock:
            return self._sync(settings)

    def _sync(self, settings: SyncSettings) -> SyncOutcome:
        if not settings.path_text or not settings.account_email:
            return SyncOutcome.NOT_CONFIGURED
        path = settings.path
        if path is None:
            return SyncOutcome.INVALID_PATH
        try:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            raw_text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return SyncOutcome.FILE_MISSING
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Could not read candidate profile file %s: %s", path, exc)
            return SyncOutcome.UNREADABLE_FILE
        try:
            # The same validation the Settings page applies (trimmed, non-empty,
            # size-capped) -- an emptied or truncated-mid-save file must never
            # replace a real profile.
            content = CandidateProfileUpdate.model_validate({"content": raw_text}).content
        except ValidationError:
            return SyncOutcome.INVALID_CONTENT

        with get_repos() as repos:
            account = repos["user_accounts"].get_by_email(settings.account_email)
        if account is None:
            return SyncOutcome.UNKNOWN_ACCOUNT
        user_id = int(account["id"])

        with get_repos(user_id) as repos:
            # Row-locked so a learned rule being appended at this moment either
            # lands first (and is then seen and backed up below) or waits.
            row = repos["candidate_profiles"].get_for_update(user_id)
            if row is not None:
                stored = str(row["content"])
                stored_hash = _sha256(stored)
                if stored == content:
                    if stored_hash != settings.last_synced_hash:
                        set_setting(LAST_SYNCED_HASH_SETTING, stored_hash, None, repos)
                        repos["conn"].commit()
                    return SyncOutcome.UP_TO_DATE
                if _parse_updated_at(str(row["updated_at"])) >= modified_at:
                    return SyncOutcome.STORED_IS_NEWER
                if stored_hash != settings.last_synced_hash:
                    try:
                        backup_path = _write_backup(path, stored)
                    except OSError as exc:
                        logger.warning(
                            "Not syncing %s: could not back up the stored profile it would replace: %s",
                            path,
                            exc,
                        )
                        return SyncOutcome.BACKUP_FAILED
                    logger.warning(
                        "The stored candidate profile had changes the file %s doesn't; "
                        "saved them to %s before replacing it.",
                        path,
                        backup_path,
                    )
            save_candidate_profile(repos, user_id, content)
            set_setting(LAST_SYNCED_HASH_SETTING, _sha256(content), None, repos)
            # Committed before re-scoring: the scorer reads the profile through
            # its own connection, so it must see the new text.
            repos["conn"].commit()

        logger.info("Synced candidate profile file %s into account %s.", path, user_id)
        rescore_active_queue_jobs(user_id)
        return SyncOutcome.SYNCED


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_updated_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    # Every writer stores UTC; treat a legacy offset-less value as UTC too.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _write_backup(profile_path: Path, stored: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = profile_path.with_name(f"{profile_path.stem}.db-backup-{stamp}{profile_path.suffix}")
    backup_path.write_text(stored + "\n", encoding="utf-8")
    return backup_path


def _same_path(left: str, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


class _ProfileFileEventHandler(FileSystemEventHandler):
    """Wakes the sync loop when the watched file itself changes; the folder
    watch also reports every sibling file, which is ignored."""

    def __init__(self, target: Path, wake: threading.Event) -> None:
        super().__init__()
        self._target = target
        self._wake = wake

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type not in _CONTENT_EVENT_TYPES:
            return
        for event_path in (event.src_path, event.dest_path):
            if event_path and _same_path(os.fsdecode(event_path), self._target):
                self._wake.set()
                return


class CandidateProfileFileSyncRunner:
    """Owns the watcher and the loop thread that calls the sync."""

    def __init__(self, file_sync: CandidateProfileFileSync, fallback_seconds: float) -> None:
        self._file_sync = file_sync
        self._fallback_seconds = fallback_seconds
        self._wake = threading.Event()
        self._observer: BaseObserver = Observer()
        self._watch: ObservedWatch | None = None
        self._watched_path: Path | None = None
        self._last_outcome: SyncOutcome | None = None

    def wake(self) -> None:
        self._wake.set()

    def run_forever(self) -> None:
        self._observer.start()
        while True:
            try:
                settings = self._file_sync.load_settings()
                self._watch_file(settings.path)
                self._report(self._file_sync.sync(settings))
            except Exception:
                logger.exception("Candidate profile file sync failed; retrying on the next change")
            self._wake.wait(self._fallback_seconds)
            if self._wake.is_set():
                time.sleep(_DEBOUNCE_SECONDS)
            self._wake.clear()

    def _watch_file(self, path: Path | None) -> None:
        if path == self._watched_path:
            return
        if self._watch is not None:
            self._observer.unschedule(self._watch)
            self._watch = None
        self._watched_path = path
        if path is None:
            return
        try:
            self._watch = self._observer.schedule(
                _ProfileFileEventHandler(path, self._wake), str(path.parent), recursive=False
            )
        except OSError as exc:
            # The fallback re-check still syncs; it just won't be immediate.
            logger.warning("Cannot watch %s for changes: %s", path.parent, exc)

    def _report(self, outcome: SyncOutcome) -> None:
        """Log problems once per change of state, not on every fallback tick."""
        if outcome == self._last_outcome:
            return
        self._last_outcome = outcome
        problems = {
            SyncOutcome.INVALID_PATH: "is not an absolute path",
            SyncOutcome.FILE_MISSING: "does not exist",
            SyncOutcome.INVALID_CONTENT: "is empty or too large to be a profile",
            SyncOutcome.UNKNOWN_ACCOUNT: "is configured for an email with no account",
        }
        if outcome in problems:
            logger.warning(
                "Candidate profile file sync is idle: the configured file %s.", problems[outcome]
            )


_runner_lock = threading.Lock()
_runner: CandidateProfileFileSyncRunner | None = None


def start_candidate_profile_file_sync() -> None:
    """Start the watcher thread. Mirrors backend/scanning/schedulers.py's
    reloader guard so only the serving process runs it."""
    global _runner

    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    if debug_mode and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    with _runner_lock:
        if _runner is not None:
            return
        _runner = CandidateProfileFileSyncRunner(
            CandidateProfileFileSync(),
            fallback_seconds=max(1.0, config.CANDIDATE_PROFILE_SYNC_FALLBACK_MINUTES) * 60,
        )
        threading.Thread(target=_runner.run_forever, daemon=True).start()
        logger.info("Candidate profile file sync started.")


def notify_sync_settings_changed() -> None:
    """Re-read the sync settings now instead of at the next fallback check, so
    a newly configured path starts being watched immediately."""
    if _runner is not None:
        _runner.wake()
