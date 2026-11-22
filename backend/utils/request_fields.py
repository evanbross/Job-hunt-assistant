# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Validated request-body field types shared by more than one blueprint's Pydantic
models (FLASK_STANDARDS.md: a helper two blueprints need lives in a shared module,
never imported from one blueprint into another). A leaf module: no project imports."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator

MAX_EMAIL_ADDRESS_LENGTH = 254


# ai: claude-sonnet-5 | 2026-09-25 -- moved here from routes/contacts.py 2026-11-11
# (was _normalize_utc_timestamp) so routes/leads.py can share it.
def normalize_utc_timestamp(value: str) -> str:
    """Accept an ISO 8601 instant with an explicit UTC offset ("Z" or +hh:mm)
    and return it as a UTC string. A value with no offset is rejected rather
    than guessed at -- a naive time is ambiguous, and a date-only string
    parses as a naive midnight."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


UtcTimestamp = Annotated[str, AfterValidator(normalize_utc_timestamp)]


# ai: claude-opus-5-5 | 2026-11-11 -- was ContactUpdate._normalize_email_address
# in routes/contacts.py.
# ai: claude-opus-5-5 | 2026-11-22 -- now used by backend/contacts/contact_emails.py
# (every typed address) and the LinkedIn import.
def normalize_email_address(value: str | None) -> str | None:
    """Blank becomes None; anything else must look like one address."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) > MAX_EMAIL_ADDRESS_LENGTH or not re.fullmatch(r"[^@\s]+@[^@\s]+", value):
        raise ValueError("not a valid email address")
    return value

