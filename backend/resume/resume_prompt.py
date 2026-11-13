# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""The single length bound for resume text placed into any AI prompt.

Every generator that sends the resume to the model (fit scoring, fit chat,
cover letters, pitches, resume tailoring) goes through this one bound. They
used to truncate independently at different lengths (4,000 / 8,000 / none),
so a resume that fit fine in a cover-letter prompt silently lost its older
roles in a pitch prompt. Leaf module (no project imports) so the scoring and
cover-letter layers can both depend on it without an import cycle."""

from __future__ import annotations

# ai: claude-opus-5-5 | 2026-10-30
# ~3k tokens: room for a dense three-page resume, while still bounding the
# prompt if an uploaded PDF extracts to something unexpectedly huge.
MAX_RESUME_PROMPT_CHARS = 12_000


# ai: claude-opus-5-5 | 2026-10-30
def resume_prompt_text(resume_text: str | None) -> str:
    """Return the stripped resume text, bounded to MAX_RESUME_PROMPT_CHARS ("" when absent)."""
    return (resume_text or "").strip()[:MAX_RESUME_PROMPT_CHARS]
