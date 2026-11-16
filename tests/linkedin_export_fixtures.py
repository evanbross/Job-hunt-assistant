# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-11
"""Builders for a fake LinkedIn messages.csv / export zip. Columns and value
formats follow a real export (checked 2026-11-11); every name and message used
with them is invented."""

from __future__ import annotations

import csv
import io
import zipfile

MESSAGES_COLUMNS = [
    "CONVERSATION ID", "CONVERSATION TITLE", "FROM", "SENDER PROFILE URL", "TO",
    "RECIPIENT PROFILE URLS", "DATE", "SUBJECT", "CONTENT", "FOLDER", "ATTACHMENTS",
]
ME = "https://www.linkedin.com/in/pat-seeker"


def profile_url(slug: str) -> str:
    return f"https://www.linkedin.com/in/{slug}"


def message_row(
    conversation: str, sender: str, sender_url: str, to_urls: str, date: str, content: str,
    folder: str = "INBOX", subject: str = "", title: str = "",
) -> dict[str, str]:
    """One messages.csv row; *date* is "YYYY-MM-DD HH:MM:SS" (UTC is appended)."""
    return {
        "CONVERSATION ID": conversation, "CONVERSATION TITLE": title, "FROM": sender,
        "SENDER PROFILE URL": sender_url, "TO": "", "RECIPIENT PROFILE URLS": to_urls,
        "DATE": f"{date} UTC", "SUBJECT": subject, "CONTENT": content, "FOLDER": folder, "ATTACHMENTS": "",
    }


def messages_csv(rows: list[dict[str, str]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=MESSAGES_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def zip_of(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return buffer.getvalue()
