# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Saving a user's candidate profile: one validated request shape and one
write path, shared by the Settings tab and the setup wizard."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints

from backend.user_events import UserEventTopic, get_user_events

MAX_PROFILE_CHARS = 100_000


# ai: claude-sonnet-5 | 2026-09-26
class CandidateProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_PROFILE_CHARS)
    ]


# ai: claude-sonnet-5 | 2026-09-26
def save_candidate_profile(repos: dict[str, Any], user_id: int, content: str) -> bool:
    """Store *content* as the user's profile. Returns True if it differs from
    what was saved before (so the caller knows whether a rescore is worthwhile).
    The caller commits."""
    profiles = repos["candidate_profiles"]
    previous = profiles.get(user_id)
    if previous is not None and previous["content"] == content:
        return False
    profiles.upsert(user_id, content, datetime.now(UTC).isoformat(timespec="seconds"))
    return True


# ai: claude-opus-5-5 | 2026-10-28
def announce_candidate_profile_changed(user_id: int) -> None:
    """Tell *user_id*'s open pages to re-check whether a profile file is
    waiting to be loaded (backend/candidate_profile_file_sync.py), since that
    depends on the stored profile as much as on the file. Call it after the
    commit, never before: a page re-reading in between would see the old text
    and get no second signal."""
    get_user_events().publish(user_id, UserEventTopic.CANDIDATE_PROFILE_FILE)
