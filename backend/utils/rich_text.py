# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Server-side sanitizing for the rich-text notes the lead tracker stores
(interactions.body_html, jobs.summary_html).

The browser editor (TipTap) emits a small, known set of tags; anything else in a
stored note came from somewhere it shouldn't have -- a crafted request, or markup
pasted from an email. Every write runs through sanitize_rich_text(), so the
database only ever holds this allowlist, and the frontend sanitizes again with
DOMPurify before rendering (defense in depth: neither layer trusts the other).

A leaf module (no project imports), like the other helpers in backend/utils."""

from __future__ import annotations

import html
import re

import nh3

# ai: claude-opus-5-5 | 2026-11-11
# What the notes editor can produce: paragraphs, inline emphasis, links, lists,
# headings, quotes and code. No images, tables, inline styles or classes.
_ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "p", "br", "strong", "b", "em", "i", "u", "s", "code", "pre", "blockquote",
        "ul", "ol", "li", "h1", "h2", "h3", "hr", "a",
    }
)
_ALLOWED_ATTRIBUTES: dict[str, set[str]] = {"a": {"href"}}
_ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})

# A long call transcript pasted into a note is a few tens of KB; this bounds a
# single field well above that without letting one request store megabytes.
MAX_RICH_TEXT_LENGTH = 200_000

# Where html_to_plain_text() starts a new line.
_BLOCK_END_RE = re.compile(r"<br\s*/?>|</(?:p|div|li|h[1-6]|blockquote|pre|tr)>", re.IGNORECASE)


# ai: claude-opus-5-5 | 2026-11-11
def sanitize_rich_text(html: str | None) -> str | None:
    """Return *html* reduced to the notes allowlist, or None when nothing visible
    is left (an empty editor sends "<p></p>"). Links get rel="noopener noreferrer";
    any URL scheme other than http/https/mailto is dropped with its attribute.
    Raises ValueError when the input exceeds MAX_RICH_TEXT_LENGTH."""
    if html is None:
        return None
    if len(html) > MAX_RICH_TEXT_LENGTH:
        raise ValueError(f"rich text is limited to {MAX_RICH_TEXT_LENGTH} characters")
    cleaned = nh3.clean(
        html,
        tags=set(_ALLOWED_TAGS),
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=set(_ALLOWED_URL_SCHEMES),
        link_rel="noopener noreferrer",
    ).strip()
    if not nh3.clean(cleaned, tags=set()).strip() and "<hr" not in cleaned:
        return None
    return cleaned


# ai: claude-opus-5-5 | 2026-11-11 -- moved from backend/leads/call_notes.py (was
# notes_to_html) now that LinkedIn message import stores plain text the same way.
def plain_text_to_html(text: str) -> str:
    """Plain text as escaped HTML paragraphs (blank line = new paragraph, single
    line break kept), for storing verbatim text in a rich-text column."""
    paragraphs = [p for p in text.replace("\r\n", "\n").split("\n\n") if p.strip()]
    return "".join(
        "<p>" + "<br>".join(html.escape(line) for line in p.split("\n")) + "</p>" for p in paragraphs
    )


# ai: claude-opus-5-5 | 2026-11-11
def html_to_plain_text(markup: str) -> str:
    """The visible text of an HTML fragment: block ends become line breaks, every
    tag is dropped and entities are decoded. For previews and AI input, never for
    rendering."""
    with_breaks = _BLOCK_END_RE.sub("\n", markup)
    text = html.unescape(nh3.clean(with_breaks, tags=set()))
    return re.sub(r"\n{3,}", "\n\n", text).strip()
