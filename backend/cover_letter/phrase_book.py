# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-08-13
"""Cover-letter phrase-book formatting."""

from __future__ import annotations


# ai: claude-sonnet-5 | 2026-08-11
def format_phrase_book_section(
    phrases: list[dict], company: str = "", job_title: str = ""
) -> str:
    """Format saved cover-letter phrase-book entries into a prompt section the
    model can draw from for the "why this company" paragraph instead of
    writing generic motivation from scratch. Empty when there are no entries.

    Phrase text may contain {{COMPANY}} / {{JOB_TITLE}} placeholders (same
    token convention as cover_letter_prompt.md) — substituted deterministically
    here rather than left for the model to notice and swap out, since a saved
    phrase written for one company must never silently carry that company's
    name into a letter for a different one.
    """
    if not phrases:
        return ""
    entries = "\n\n".join(
        f"[{p.get('tag', '')}] {p.get('label', '')}:\n"
        f"{(p.get('phrase_text', '') or '').replace('{{COMPANY}}', company).replace('{{JOB_TITLE}}', job_title)}"
        for p in phrases
    )
    return (
        "\n\nREUSABLE MATERIAL FROM PAST COVER LETTERS (candidate's own words, tagged "
        "by theme):\n"
        f"{entries}\n"
        "If any entry above genuinely fits this company/role, adapt it rather than "
        "writing new personal-motivation content from scratch. Ignore entries that "
        "don't fit — never force an irrelevant one in. If an entry still names a "
        "specific company (from when it was originally written), that company is "
        "stale context, not the target — replace it with the current company or "
        "generalize the sentence so no other company's name ends up in this letter."
    )
