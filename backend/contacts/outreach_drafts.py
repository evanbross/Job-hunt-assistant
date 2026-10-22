# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""LinkedIn outreach drafts for the Contacts tab's outreach queue.

A draft is built from one of the user's named templates (placeholders filled
from the contact), optionally rewritten per contact by the AI provider, then "staged"
for the browser extension: when the user opens that contact's LinkedIn
profile, the extension claims the staged draft (claim_draft_for_page below)
and pastes it into the message box. The user always presses Send themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import unquote, urlparse

from backend.ai_provider import AIMessage, AIProviderClient

# A staged draft older than this is treated as abandoned, so it's never pasted
# into a profile the user happens to open much later.
STAGED_DRAFT_TTL = timedelta(minutes=15)

# LinkedIn caps a direct message at 8000 characters.
MAX_OUTREACH_MESSAGE_LENGTH = 8000

_AI_MAX_TOKENS = 500

_AI_SYSTEM_PROMPT = (
    "You write short, warm, professional LinkedIn direct messages for a job seeker "
    "reaching out to one of their existing LinkedIn connections. Use the job seeker's "
    "template as the voice, structure, and intent to follow, personalized for this "
    "contact's role and company. Keep it under 600 characters. Never invent facts "
    "about the job seeker or the contact beyond what you are given. Return only the "
    "message text: no subject line, no placeholders, no quotation marks, no commentary."
)


# ai: claude-opus-5-5 | 2026-10-14
def render_outreach_template(template: str, contact: dict[str, Any]) -> str:
    """Fill the template's {first_name}/{full_name}/{company}/{position}
    placeholders from the contact. Plain str.replace rather than str.format,
    so any other braces the user types are left alone instead of raising."""
    full_name = (contact.get("full_name") or "").strip()
    values = {
        "{first_name}": full_name.split()[0] if full_name else "there",
        "{full_name}": full_name,
        "{company}": (contact.get("company_raw") or "").strip() or "your company",
        "{position}": (contact.get("position") or "").strip(),
    }
    for placeholder, value in values.items():
        template = template.replace(placeholder, value)
    return template.strip()


# ai: claude-opus-5-5 | 2026-10-14
def linkedin_profile_slug(url: str | None) -> str | None:
    """The member identifier from a linkedin.com/in/<slug> URL, lowercased and
    percent-decoded, or None for anything else. Comparing slugs rather than
    whole URLs tolerates the trailing slashes, query strings, overlay paths
    (/in/<slug>/overlay/...) and http/www variants LinkedIn adds or drops."""
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].lower() != "in":
        return None
    return unquote(parts[1]).lower() or None


# ai: claude-opus-5-5 | 2026-10-14
@dataclass(frozen=True)
class ClaimedDraft:
    contact_id: int
    full_name: str
    message: str


# ai: claude-opus-5-5 | 2026-10-14
def claim_draft_for_page(
    user_id: int, page_url: str, repos: dict[str, Any], now: datetime
) -> ClaimedDraft | None:
    """Hand the user's staged draft to the extension if page_url is that
    contact's LinkedIn profile, unstaging it so it's pasted only once. Any
    other page (a different profile, a stale stage, nothing staged) returns
    None and leaves the stage untouched. The caller commits."""
    page_slug = linkedin_profile_slug(page_url)
    if page_slug is None:
        return None
    staged_after = (now - STAGED_DRAFT_TTL).isoformat(timespec="seconds")
    staged = repos["contacts"].get_staged_outreach_draft(user_id, staged_after)
    if staged is None or not staged.get("outreach_draft"):
        return None
    if linkedin_profile_slug(staged.get("profile_url")) != page_slug:
        return None
    if not repos["contacts"].claim_staged_outreach_draft(user_id, staged["id"]):
        return None  # a concurrent claim (same profile in another tab) won
    return ClaimedDraft(
        contact_id=staged["id"], full_name=staged["full_name"], message=staged["outreach_draft"]
    )


# ai: claude-opus-5-5 | 2026-10-14
class OutreachDraftWriter:
    """Rewrites a template-rendered draft for one contact with the user's
    configured AI provider. Takes only plain values -- callers load the
    contact and candidate profile, then release their DB connection before
    calling write(), which can take many seconds."""

    def __init__(self, user_id: int) -> None:
        self._user_id = user_id

    def write(self, contact: dict[str, Any], template_draft: str, candidate_profile: str) -> str:
        prompt = (
            f"JOB SEEKER'S TEMPLATE (already filled in for this contact):\n{template_draft}\n\n"
            f"CONTACT:\nName: {contact.get('full_name') or ''}\n"
            f"Title: {contact.get('position') or 'unknown'}\n"
            f"Company: {contact.get('company_raw') or 'unknown'}\n\n"
            f"JOB SEEKER'S BACKGROUND:\n{candidate_profile.strip() or '(not provided)'}"
        )
        with AIProviderClient(self._user_id) as client:
            message = client.complete(
                messages=[AIMessage(role="user", content=prompt)],
                system=_AI_SYSTEM_PROMPT,
                max_tokens=_AI_MAX_TOKENS,
            )
        return message.strip()[:MAX_OUTREACH_MESSAGE_LENGTH]
