# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Validation, text extraction, and encrypted storage for a user's resume PDF.

The PDF lives in that user's own `resume` row, encrypted at rest, rather than
in a shared file on disk. That makes one user's upload unable to replace or
reveal another's by construction, keeps replacement atomic with the row it
belongs to, and leaves nothing on the filesystem to garbage-collect."""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import UTC, datetime
from typing import Any

import fitz

from backend.crypto import decrypt_bytes, encrypt_bytes
from backend.db.interfaces import IResumeRepository

logger = logging.getLogger(__name__)

MAX_RESUME_BYTES = 5 * 1024 * 1024
_PDF_MAGIC = b"%PDF-"
_MAX_FILENAME_LENGTH = 255


class InvalidResumeFileError(ValueError):
    """The uploaded bytes are not an acceptable resume PDF (message is safe to show)."""


# ai: claude-sonnet-5 | 2026-09-26
def validate_pdf_upload(data: bytes) -> None:
    if not data:
        raise InvalidResumeFileError("The uploaded file is empty.")
    if len(data) > MAX_RESUME_BYTES:
        limit_mb = MAX_RESUME_BYTES // (1024 * 1024)
        raise InvalidResumeFileError(f"The resume must be {limit_mb} MB or smaller.")
    if not data.startswith(_PDF_MAGIC):
        raise InvalidResumeFileError("That file is not a valid PDF.")


# ai: claude-sonnet-5 | 2026-09-26
def extract_pdf_text(data: bytes) -> str:
    """Extract text straight from memory (no temp file). Raises
    InvalidResumeFileError for a corrupt or unreadable PDF."""
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            return "\n".join(page.get_text() for page in doc)
    except (fitz.FileDataError, fitz.EmptyFileError, RuntimeError, ValueError) as exc:
        logger.warning("PDF text extraction failed: %s", exc)
        raise InvalidResumeFileError("That PDF could not be read.") from exc


def _clean_filename(filename: str) -> str:
    name = os.path.basename(filename.replace("\\", "/")).strip()
    return (name or "resume.pdf")[:_MAX_FILENAME_LENGTH]


# ai: claude-sonnet-5 | 2026-09-26
class ResumeFileService:
    def __init__(self, resumes: IResumeRepository) -> None:
        self._resumes = resumes

    def replace(self, user_id: int, filename: str, pdf_bytes: bytes, text: str) -> dict[str, Any]:
        """Make this upload the user's only resume. The caller commits, so the
        delete and insert are one transaction."""
        validate_pdf_upload(pdf_bytes)
        self._resumes.delete_all(user_id)
        self._resumes.insert(
            user_id,
            {
                "filename": _clean_filename(filename),
                "content": text,
                "uploaded_at": datetime.now(UTC).isoformat(),
                "file_encrypted": encrypt_bytes(pdf_bytes),
                "file_size": len(pdf_bytes),
                "file_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            },
        )
        resume = self._resumes.get_latest(user_id)
        if resume is None:
            raise RuntimeError("Resume row missing immediately after insert")
        return resume

    def load_file(self, user_id: int) -> tuple[bytes, str] | None:
        """The user's original PDF and filename, or None if they have none
        (including a resume uploaded before files were stored)."""
        stored = self._resumes.get_latest_file(user_id)
        if stored is None or not stored.get("file_encrypted"):
            return None
        return decrypt_bytes(stored["file_encrypted"]), str(stored.get("filename") or "resume.pdf")
