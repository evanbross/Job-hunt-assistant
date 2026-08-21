from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.db import IScannedEmailRepository
from backend.email.gmail_client import GmailClient


@dataclass(frozen=True)
class EmailCleanupResult:
    checked_count: int
    hidden_count: int
    hidden_email_ids: list[str]


class EmailCleanupService:
    """Hide scanned emails that are no longer unread messages in the inbox."""

    def __init__(
        self,
        scanned_repo: IScannedEmailRepository,
        gmail_client: GmailClient,
    ) -> None:
        self._scanned_repo = scanned_repo
        self._gmail_client = gmail_client

    def hide_stale_or_read_emails(
        self,
        user_id: int,
        displayed_emails: list[dict[str, Any]],
    ) -> EmailCleanupResult:
        email_ids_to_hide: list[str] = []

        for email_row in displayed_emails:
            email_id_value = str(email_row.get("email_id") or "").strip()
            if not email_id_value:
                continue

            base_email_id = email_id_value.split(":")[0]
            message_state = self._gmail_client.get_message_state(base_email_id)
            should_hide = (
                message_state is None
                or not message_state.in_inbox
                or not message_state.is_unread
            )
            if should_hide:
                email_ids_to_hide.append(email_id_value)

        unique_email_ids_to_hide = list(dict.fromkeys(email_ids_to_hide))
        self._scanned_repo.set_hidden_many(user_id, unique_email_ids_to_hide, True)
        return EmailCleanupResult(
            checked_count=len(displayed_emails),
            hidden_count=len(unique_email_ids_to_hide),
            hidden_email_ids=unique_email_ids_to_hide,
        )
