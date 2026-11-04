# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-10-28
"""Offer a candidate profile file on the server's disk to one account, to load
into its stored profile -- the candidate_profiles row that fit scoring, cover
letters, and pitches actually read. Nothing is loaded without the account
reviewing the change and saying so.

Whether a reload is waiting is worked out from the file and the stored profile
each time it's asked, never remembered from the moment the file changed, so a
page opened later (or a server restarted since) still sees it: the file must
differ from the stored profile, be newer than it -- so a rule the cover-letter
learner appended after the file's last save isn't offered for replacement --
and not be the version the account dismissed.

A filesystem watcher on the file's folder only tells that account's open pages
to ask again (backend/user_events.py); pages also ask when they open, when
their stream reconnects, and on a slow timer. Loading backs up a stored profile
that changed since the last load to a file beside the profile before replacing
it. Which file and which account are admin-only system settings (see
backend/settings_registry.py for why they aren't per-user)."""

from __future__ import annotations

import difflib
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

from backend.candidate_profile_service import (
    CandidateProfileUpdate,
    announce_candidate_profile_changed,
    save_candidate_profile,
)
from backend.settings_service import get_setting, set_setting
from backend.user_events import UserEventTopic, get_user_events
from backend.utils.utils import get_repos

logger = logging.getLogger(__name__)

PATH_SETTING = "candidate_profile_sync_path"
ACCOUNT_EMAIL_SETTING = "candidate_profile_sync_account_email"
LAST_SYNCED_HASH_SETTING = "candidate_profile_sync_last_synced_sha256"
DISMISSED_HASH_SETTING = "candidate_profile_sync_dismissed_sha256"
SETTING_KEYS: frozenset[str] = frozenset({PATH_SETTING, ACCOUNT_EMAIL_SETTING})

# Editors often save as write-temp-then-rename, which arrives as a burst of
# events; waiting this long after the first one sends one signal per save.
_DEBOUNCE_SECONDS = 1.0
# watchdog event types that mean the file's content may have changed.
_CONTENT_EVENT_TYPES: frozenset[str] = frozenset({"created", "modified", "moved", "closed"})
_DIFF_CONTEXT_LINES = 2
# Collision suffixes _write_backup tries before giving up (the load then fails
# with BackupFailedError rather than overwrite a backup).
_MAX_BACKUP_NAME_ATTEMPTS = 100


class FileSyncState(StrEnum):
    NOT_CONFIGURED = "not_configured"
    # The file is configured for a different account than the caller's.
    OTHER_ACCOUNT = "other_account"
    INVALID_PATH = "invalid_path"
    FILE_MISSING = "file_missing"
    UNREADABLE_FILE = "unreadable_file"
    INVALID_CONTENT = "invalid_content"
    UP_TO_DATE = "up_to_date"
    STORED_IS_NEWER = "stored_is_newer"
    DISMISSED = "dismissed"
    RELOAD_AVAILABLE = "reload_available"


# States in which the file differs from the stored profile, so loading it
# would change something. Only RELOAD_AVAILABLE is offered unprompted.
_LOADABLE_STATES: frozenset[FileSyncState] = frozenset(
    {FileSyncState.RELOAD_AVAILABLE, FileSyncState.DISMISSED, FileSyncState.STORED_IS_NEWER}
)


class FileChangedError(Exception):
    """The file is no longer the version the caller reviewed (or no longer
    differs from the stored profile), so acting on it was refused."""


class BackupFailedError(Exception):
    """The stored profile couldn't be backed up, so it was not replaced."""


@dataclass(frozen=True)
class SyncSettings:
    path_text: str
    account_email: str
    last_synced_hash: str
    dismissed_hash: str

    @property
    def path(self) -> Path | None:
        """The configured file, or None if unset or not an absolute path. A
        relative path would resolve against whatever the server's working
        directory happens to be, so it's rejected rather than guessed at."""
        if not self.path_text:
            return None
        candidate = Path(self.path_text)
        return candidate if candidate.is_absolute() else None


@dataclass(frozen=True)
class FileSyncStatus:
    state: FileSyncState
    path: str = ""
    file_sha256: str = ""
    file_modified_at: str = ""
    stored_updated_at: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "state": self.state.value,
            "path": self.path,
            "file_sha256": self.file_sha256,
            "file_modified_at": self.file_modified_at,
            "stored_updated_at": self.stored_updated_at,
        }


@dataclass(frozen=True)
class _Comparison:
    """A status plus the two texts it was decided from."""

    status: FileSyncStatus
    file_content: str = ""
    stored_content: str = ""


@dataclass(frozen=True)
class LoadResult:
    backup_path: str


class CandidateProfileFileSync:
    """The decisions themselves -- no threads, so tests can drive them directly."""

    def __init__(self) -> None:
        # Serializes two loads (two tabs, a double click) of the same file.
        self._lock = threading.Lock()

    @staticmethod
    def load_settings() -> SyncSettings:
        with get_repos() as repos:
            return SyncSettings(
                path_text=get_setting(PATH_SETTING, None, repos).strip(),
                account_email=get_setting(ACCOUNT_EMAIL_SETTING, None, repos).strip().casefold(),
                last_synced_hash=get_setting(LAST_SYNCED_HASH_SETTING, None, repos),
                dismissed_hash=get_setting(DISMISSED_HASH_SETTING, None, repos),
            )

    @staticmethod
    def account_user_id(settings: SyncSettings) -> int | None:
        """The configured account's id, or None if unset or no such account."""
        if not settings.account_email:
            return None
        with get_repos() as repos:
            account = repos["user_accounts"].get_by_email(settings.account_email)
        return None if account is None else int(account["id"])

    def status(self, user_id: int) -> FileSyncStatus:
        return self._compare(self.load_settings(), user_id).status

    def review(self, user_id: int) -> tuple[FileSyncStatus, list[str]]:
        """The status plus a unified diff from the stored profile to the file,
        empty when they don't differ."""
        comparison = self._compare(self.load_settings(), user_id)
        if comparison.status.state not in _LOADABLE_STATES:
            return comparison.status, []
        diff = difflib.unified_diff(
            comparison.stored_content.splitlines(),
            comparison.file_content.splitlines(),
            fromfile="Stored profile",
            tofile="File",
            n=_DIFF_CONTEXT_LINES,
            lineterm="",
        )
        return comparison.status, list(diff)

    def dismiss(self, user_id: int, reviewed_sha256: str) -> None:
        """Stop offering this version of the file. Raises FileChangedError if
        the file is no longer *reviewed_sha256*."""
        comparison = self._compare(self.load_settings(), user_id)
        if (
            comparison.status.state not in _LOADABLE_STATES
            or comparison.status.file_sha256 != reviewed_sha256
        ):
            raise FileChangedError
        with get_repos(user_id) as repos:
            set_setting(DISMISSED_HASH_SETTING, reviewed_sha256, None, repos)
            repos["conn"].commit()
        get_user_events().publish(user_id, UserEventTopic.CANDIDATE_PROFILE_FILE)

    def load(self, user_id: int, reviewed_sha256: str) -> LoadResult:
        """Replace the stored profile with the file. Raises FileChangedError if
        the file is no longer *reviewed_sha256* or no longer differs from the
        stored profile, and BackupFailedError if text only the stored profile
        has couldn't be saved first. The caller re-scores the queue."""
        with self._lock:
            settings = self.load_settings()
            comparison = self._compare(settings, user_id)
            if (
                comparison.status.state not in _LOADABLE_STATES
                or comparison.status.file_sha256 != reviewed_sha256
            ):
                raise FileChangedError
            backup_path = ""
            with get_repos(user_id) as repos:
                # Row-locked so a learned rule being appended at this moment
                # either lands first (and is then seen and backed up below) or waits.
                row = repos["candidate_profiles"].get_for_update(user_id)
                stored = "" if row is None else str(row["content"])
                if row is not None and _sha256(stored) != settings.last_synced_hash:
                    try:
                        backup_path = str(_write_backup(Path(comparison.status.path), stored))
                    except OSError as exc:
                        logger.warning(
                            "Not loading %s: could not back up the stored profile it would replace: %s",
                            comparison.status.path,
                            exc,
                        )
                        raise BackupFailedError from exc
                save_candidate_profile(repos, user_id, comparison.file_content)
                set_setting(LAST_SYNCED_HASH_SETTING, reviewed_sha256, None, repos)
                repos["conn"].commit()
        logger.info("Loaded candidate profile file %s into account %s.", comparison.status.path, user_id)
        announce_candidate_profile_changed(user_id)
        return LoadResult(backup_path=backup_path)

    def _compare(self, settings: SyncSettings, user_id: int) -> _Comparison:
        if not settings.path_text or not settings.account_email:
            return _Comparison(FileSyncStatus(FileSyncState.NOT_CONFIGURED))
        if self.account_user_id(settings) != user_id:
            return _Comparison(FileSyncStatus(FileSyncState.OTHER_ACCOUNT))
        path = settings.path
        if path is None:
            return _Comparison(FileSyncStatus(FileSyncState.INVALID_PATH, path=settings.path_text))
        try:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            raw_text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _Comparison(FileSyncStatus(FileSyncState.FILE_MISSING, path=str(path)))
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Could not read candidate profile file %s: %s", path, exc)
            return _Comparison(FileSyncStatus(FileSyncState.UNREADABLE_FILE, path=str(path)))
        try:
            # The same validation the Settings page applies (trimmed, non-empty,
            # size-capped) -- an emptied or truncated-mid-save file is never offered.
            content = CandidateProfileUpdate.model_validate({"content": raw_text}).content
        except ValidationError:
            return _Comparison(FileSyncStatus(FileSyncState.INVALID_CONTENT, path=str(path)))

        with get_repos(user_id) as repos:
            row = repos["candidate_profiles"].get(user_id)
        stored = "" if row is None else str(row["content"])
        stored_updated_at = "" if row is None else str(row["updated_at"])
        file_sha256 = _sha256(content)

        if row is not None and stored == content:
            state = FileSyncState.UP_TO_DATE
        elif row is not None and _parse_updated_at(stored_updated_at) >= modified_at:
            state = FileSyncState.STORED_IS_NEWER
        elif file_sha256 == settings.dismissed_hash:
            state = FileSyncState.DISMISSED
        else:
            state = FileSyncState.RELOAD_AVAILABLE
        return _Comparison(
            FileSyncStatus(
                state,
                path=str(path),
                file_sha256=file_sha256,
                file_modified_at=modified_at.isoformat(timespec="seconds"),
                stored_updated_at=stored_updated_at,
            ),
            file_content=content,
            stored_content=stored,
        )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_updated_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    # Every writer stores UTC; treat a legacy offset-less value as UTC too.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


# ai: claude-opus-5-5 | 2026-11-03 — never overwrites another backup (code-review-2026-11-02 #2)
def _write_backup(profile_path: Path, stored: str) -> Path:
    """Save *stored* beside the profile file under a name no other backup has.

    The name was once second-precision and written with write_text(), which
    truncates an existing file, so two loads in the same second destroyed the
    first backup. Now the stamp carries microseconds and the file is created
    exclusively ("x"): if the name is somehow taken anyway (same microsecond,
    or another app process), a -1, -2, ... suffix is tried instead."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    for attempt in range(_MAX_BACKUP_NAME_ATTEMPTS):
        counter = f"-{attempt}" if attempt else ""
        backup_path = profile_path.with_name(
            f"{profile_path.stem}.db-backup-{stamp}{counter}{profile_path.suffix}"
        )
        try:
            with backup_path.open("x", encoding="utf-8") as handle:
                handle.write(stored + "\n")
        except FileExistsError:
            continue
        return backup_path
    raise FileExistsError(
        f"every backup name for {stamp} is taken ({_MAX_BACKUP_NAME_ATTEMPTS} tried) beside {profile_path}"
    )


def _same_path(left: str, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


class _ProfileFileEventHandler(FileSystemEventHandler):
    """Wakes the watcher loop when the watched file itself changes; the folder
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


class CandidateProfileFileWatcher:
    """Owns the filesystem watcher and the loop thread that turns a change to
    the file (or to the sync settings) into a signal to the account's pages."""

    def __init__(self, file_sync: CandidateProfileFileSync) -> None:
        self._file_sync = file_sync
        self._wake = threading.Event()
        self._observer: BaseObserver = Observer()
        self._watch: ObservedWatch | None = None
        self._watched_path: Path | None = None
        self._account_user_id: int | None = None

    def wake(self) -> None:
        self._wake.set()

    def run_forever(self) -> None:
        self._observer.start()
        while True:
            try:
                self._refresh()
            except Exception:
                logger.exception("Candidate profile file watcher failed; retrying on the next change")
            self._wake.wait()
            time.sleep(_DEBOUNCE_SECONDS)
            self._wake.clear()

    def _refresh(self) -> None:
        settings = self._file_sync.load_settings()
        self._watch_file(settings.path)
        user_id = self._file_sync.account_user_id(settings)
        # When the settings moved the file to another account, the old
        # account's pages need to hear it too, so their banner goes away.
        affected = {account for account in (self._account_user_id, user_id) if account is not None}
        for account_id in affected:
            get_user_events().publish(account_id, UserEventTopic.CANDIDATE_PROFILE_FILE)
        self._account_user_id = user_id

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
            # Pages still notice the change on their own fallback re-check.
            logger.warning("Cannot watch %s for changes: %s", path.parent, exc)


# The one instance routes and the watcher share, so two loads arriving at once
# (two tabs, a double click) take the same lock.
candidate_profile_file_sync = CandidateProfileFileSync()

_watcher_lock = threading.Lock()
_watcher: CandidateProfileFileWatcher | None = None


def start_candidate_profile_file_watcher() -> None:
    """Start the watcher thread. Mirrors backend/scanning/schedulers.py's
    reloader guard so only the serving process runs it."""
    global _watcher

    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    if debug_mode and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    with _watcher_lock:
        if _watcher is not None:
            return
        _watcher = CandidateProfileFileWatcher(candidate_profile_file_sync)
        threading.Thread(target=_watcher.run_forever, daemon=True).start()
        logger.info("Candidate profile file watcher started.")


def notify_sync_settings_changed() -> None:
    """Re-read the sync settings now, so a newly configured path starts being
    watched and the affected accounts' pages re-check immediately."""
    if _watcher is not None:
        _watcher.wake()
