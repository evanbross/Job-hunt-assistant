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
