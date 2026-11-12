# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-11
"""backend/utils/rich_text.py: what reaches a rich-text column, and the plain-text
conversions the call-notes and LinkedIn-message imports use."""

from __future__ import annotations

from backend.utils.rich_text import (
    html_to_plain_text,
    plain_text_to_html,
    sanitize_rich_text,
)


def test_plain_text_becomes_escaped_paragraphs() -> None:
    assert plain_text_to_html("Rate <90>\r\nremote\n\nNext: send resume") == (
        "<p>Rate &lt;90&gt;<br>remote</p><p>Next: send resume</p>"
    )


def test_html_to_plain_text_keeps_line_structure_and_decodes_entities() -> None:
    markup = "<p>Hi there,</p><p>A <b>Staff</b> role &amp; more<br/>Call me?</p><script>x()</script>"
    assert html_to_plain_text(markup) == "Hi there,\nA Staff role & more\nCall me?"


def test_sanitize_drops_scripts_and_unsafe_links() -> None:
    cleaned = sanitize_rich_text('<p onclick="x()">Hi <a href="javascript:alert(1)">link</a></p><script>x()</script>')
    assert cleaned == "<p>Hi <a rel=\"noopener noreferrer\">link</a></p>"
    assert sanitize_rich_text("<p></p>") is None
