# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""The explicit set of actions the voice-query feature (routes/voice.py) is
allowed to trigger. Deliberately a plain dict, not a plugin framework -- each
entry is a thin wrapper around existing, already-tested backend logic, and
adding another action later is one new entry, nothing more."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from routes.applications import run_mark_ghosts
from routes.career_sites import start_career_scan_now


@dataclass(frozen=True)
class VoiceAction:
    description: str  # shown to the LLM in backend/voice/voice_query.py's system prompt
    handler: Callable[[], tuple[dict, int]]


VOICE_ACTIONS: dict[str, VoiceAction] = {
    "start_career_scan": VoiceAction(
        description="Start scanning all configured job-board/career sites for new listings.",
        handler=start_career_scan_now,
    ),
    "mark_ghosts": VoiceAction(
        description=(
            "Mark applications that have been in the Applied stage for 30+ days "
            "with no response as ghosted."
        ),
        handler=run_mark_ghosts,
    ),
}
