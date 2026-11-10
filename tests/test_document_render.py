# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-10
"""The downloadable cover letter / pitch files keep every character and every line
(backend/cover_letter/document_render.py). Both PDF failures were silent: the file
opened fine, it just said something else."""

from __future__ import annotations

import io

import docx
import fitz

from backend.cover_letter.document_render import render_document

# Characters a generated letter routinely contains, outside the Latin-1 range the
# old PDF font covered.
_LETTER = "Dear Hiring Manager,\n\nI’m excited — truly — about the “Staff” role at Café Zürich.\n\nRegards,\nEvan"


def _pdf_text(pdf_bytes: bytes) -> tuple[int, str]:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        return pdf.page_count, "".join(page.get_text() for page in pdf)


def test_pdf_keeps_curly_quotes_dashes_and_accents() -> None:
    _, text = _pdf_text(render_document(_LETTER, "pdf"))
    for phrase in ("I’m excited — truly —", "“Staff”", "Café Zürich"):
        assert phrase in text
    assert "?" not in text


def test_a_long_pdf_continues_onto_more_pages_instead_of_losing_the_end() -> None:
    letter = "\n\n".join(f"Paragraph {n}: " + "more detail " * 60 for n in range(15))

    pages, text = _pdf_text(render_document(letter, "pdf"))

    assert pages > 1
    assert "Paragraph 14:" in text


def test_docx_has_one_paragraph_per_line() -> None:
    document = docx.Document(io.BytesIO(render_document(_LETTER, "docx")))
    assert [p.text for p in document.paragraphs] == _LETTER.split("\n")


def test_text_formats_are_utf8() -> None:
    assert render_document(_LETTER, "txt").decode("utf-8") == _LETTER
    assert render_document(_LETTER, "md").decode("utf-8") == _LETTER
