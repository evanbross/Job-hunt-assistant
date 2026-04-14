"""
Gmail API client.

Design note (per project rules): OAuth / interactive auth flows are NEVER
triggered from within this client. If a valid token is not present,
NotAuthenticatedError is raised so the caller (a Flask route) can return an
error to the frontend. The interactive auth flow lives exclusively in the
dedicated /api/auth/* endpoints in app.py.
"""
from __future__ import annotations

import base64
import logging
from email import message_from_bytes
from typing import Any

from google.auth.exceptions import TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import GMAIL_SCOPES, GOOGLE_TOKEN_FILE, READ_ONLY_MODE

logger = logging.getLogger(__name__)


class NotAuthenticatedError(RuntimeError):
    """Raised when no valid Google token exists. Auth must happen via /api/auth/google."""


class GmailClient:
    """Thin wrapper around the Gmail API for reading and marking emails."""

    def __init__(self) -> None:
        self._service = self._build_service()

    def _build_service(self) -> Any:
        if not GOOGLE_TOKEN_FILE.exists():
            raise NotAuthenticatedError(
                "Google token not found. Complete OAuth via /api/auth/google first."
            )

        creds = Credentials.from_authorized_user_file(
            str(GOOGLE_TOKEN_FILE), GMAIL_SCOPES
        )

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    # Persist the refreshed token
                    GOOGLE_TOKEN_FILE.write_text(creds.to_json())
                except TransportError as exc:
                    raise NotAuthenticatedError(
                        f"Token refresh failed: {exc}. Re-authenticate via /api/auth/google."
                    ) from exc
            else:
                raise NotAuthenticatedError(
                    "Token invalid and cannot be refreshed. Re-authenticate via /api/auth/google."
                )

        return build("gmail", "v1", credentials=creds)

    # ── Message fetching ──────────────────────────────────────────────────────

    def list_unread_messages(
        self, lookback_days: int, sender_filters: list[dict]
    ) -> list[dict[str, Any]]:
        """
        Return unread messages from the last *lookback_days* days that match
        at least one enabled email source filter.

        sender_filters: list of dicts with keys 'match_type' and 'match_value'.
        Returns list of {id, threadId} dicts.
        """
        # Build a search query combining lookback + OR of sender filters
        filter_parts: list[str] = []
        for sf in sender_filters:
            if sf["match_type"] == "sender_domain":
                filter_parts.append(f"from:{sf['match_value']}")
            elif sf["match_type"] == "sender_email":
                filter_parts.append(f"from:{sf['match_value']}")
            elif sf["match_type"] == "subject_contains":
                filter_parts.append(f"subject:{sf['match_value']}")

        if filter_parts:
            sender_query = "(" + " OR ".join(filter_parts) + ")"
        else:
            sender_query = ""

        query = f"is:unread newer_than:{lookback_days}d {sender_query}".strip()
        logger.info("Gmail query: %s", query)

        messages: list[dict] = []
        page_token: str | None = None

        while True:
            kwargs: dict[str, Any] = {
                "userId": "me",
                "q": query,
                "maxResults": 100,
            }
            if page_token:
                kwargs["pageToken"] = page_token

            try:
                result = self._service.users().messages().list(**kwargs).execute()
            except HttpError as exc:
                logger.error("Gmail list error: %s", exc)
                raise

            messages.extend(result.get("messages", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break

        return messages

    def get_message(self, message_id: str) -> dict[str, Any]:
        """Fetch full message metadata + body."""
        try:
            return (
                self._service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except HttpError as exc:
            logger.error("Gmail get message %s error: %s", message_id, exc)
            raise

    def mark_as_read(self, message_id: str) -> None:
        """Remove the UNREAD label from a message. No-op in READ_ONLY_MODE."""
        if READ_ONLY_MODE:
            logger.debug("READ_ONLY_MODE: skipping mark_as_read for %s", message_id)
            return
        try:
            self._service.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
        except HttpError as exc:
            logger.warning("Could not mark message %s as read: %s", message_id, exc)

    # ── Message parsing helpers ───────────────────────────────────────────────

    @staticmethod
    def extract_text(message: dict[str, Any]) -> tuple[str, str, str]:
        """
        Extract (subject, sender, body_text) from a full Gmail message dict.
        Body is returned as plain text where possible.
        """
        headers = {
            h["name"].lower(): h["value"]
            for h in message.get("payload", {}).get("headers", [])
        }
        subject = headers.get("subject", "")
        sender = headers.get("from", "")
        body = _extract_body(message.get("payload", {}))
        return subject, sender, body


def _extract_body(payload: dict[str, Any]) -> str:
    """Recursively walk a Gmail payload to extract the plain-text body."""
    mime_type: str = payload.get("mimeType", "")

    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return _b64decode(data)

    if mime_type == "text/html":
        # Fall back to HTML only if no plain part is found
        data = payload.get("body", {}).get("data", "")
        return _b64decode(data)

    parts = payload.get("parts", [])
    # Prefer text/plain part
    for part in parts:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            return _b64decode(data)

    # Recurse into multipart/* containers
    for part in parts:
        text = _extract_body(part)
        if text.strip():
            return text

    return ""


def _b64decode(data: str) -> str:
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    except Exception:
        return ""
