# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-10 -- moved out of routes/cover_letter.py, with the
# PDF renderer rewritten (see render_pdf).
"""Turn a cover letter's or pitch's text into the bytes of a downloadable file."""

from __future__ import annotations

import io

import docx
import fitz

DOCUMENT_MIMETYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
}

_PAGE = fitz.paper_rect("letter")
_MARGIN = 72  # one inch, in points
_CONTENT_AREA = _PAGE + (_MARGIN, _MARGIN, -_MARGIN, -_MARGIN)
_FONT = fitz.Font("helv")
_FONT_SIZE = 11


def render_document(text: str, extension: str) -> bytes:
    """*text* as a .docx, .pdf, or UTF-8 .txt/.md file's bytes."""
    if extension == "docx":
        return render_docx(text)
    if extension == "pdf":
        return render_pdf(text)
    return text.encode("utf-8")


def render_docx(text: str) -> bytes:
    """One Word paragraph per line of *text*."""
    document = docx.Document()
    for para in document.paragraphs:
        p = para._element
        p.getparent().remove(p)
    for block in text.split("\n"):
        document.add_paragraph(block)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def render_pdf(text: str) -> bytes:
    """*text* on Letter pages with one-inch margins, as many pages as it needs.

    This replaced a single insert_textbox() call with the "helv" base font, found
    in the hosted rehearsal, which had two silent failures: that font covers only
    Latin-1, so curly apostrophes and dashes printed as "?", and it drew one page,
    so the rest of a longer letter was dropped. fitz.Font("helv") is the same
    Helvetica design as a full-Unicode embedded font. TextWriter is used rather
    than PyMuPDF's HTML layout (Story), which joins "ff"/"fi" into ligature
    characters that text extraction (an applicant-tracking system reading the
    PDF) returns as a single U+FB00 character instead of the letters.
    """
    with fitz.open() as pdf:
        remaining = text
        while True:
            page = pdf.new_page(width=_PAGE.width, height=_PAGE.height)
            writer = fitz.TextWriter(page.rect)
            overflow = writer.fill_textbox(_CONTENT_AREA, remaining, font=_FONT, fontsize=_FONT_SIZE)
            writer.write_text(page)
            if not overflow:
                return pdf.tobytes()
            # fill_textbox returns the lines that didn't fit, already wrapped: start
            # the next page with them.
            remaining = "\n".join(line for line, _ in overflow)
