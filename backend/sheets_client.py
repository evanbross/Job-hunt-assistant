"""
Google Sheets API client.

Appends job application rows to the "Job Hunt 2026" spreadsheet.
Column order: Company, Status, Source, Date of Application, Contact/Notes, URL
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import GMAIL_SCOPES, GOOGLE_SHEETS_ID, GOOGLE_TOKEN_FILE, READ_ONLY_MODE
from backend.gmail_client import NotAuthenticatedError

logger = logging.getLogger(__name__)

_SHEET_NAME = "Sheet1"
_COLUMNS = ["Company", "Status", "Source", "Date of Application", "Contact/Notes", "URL"]


class SheetsClient:
    """Thin wrapper around the Google Sheets API for appending job rows."""

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
                    GOOGLE_TOKEN_FILE.write_text(creds.to_json())
                except RefreshError as exc:
                    GOOGLE_TOKEN_FILE.unlink(missing_ok=True)
                    raise NotAuthenticatedError(
                        f"Token rejected by Google ({exc}). "
                        "Re-authenticate via /api/auth/google."
                    ) from exc
                except TransportError as exc:
                    raise NotAuthenticatedError(
                        f"Token refresh failed: {exc}. Re-authenticate via /api/auth/google."
                    ) from exc
            else:
                raise NotAuthenticatedError(
                    "Token invalid and cannot be refreshed. Re-authenticate via /api/auth/google."
                )

        return build("sheets", "v4", credentials=creds)

    def append_job(
        self,
        company: str,
        source: str,
        job_title: str,
        url: str,
    ) -> None:
        """
        Append one row to the first tab of the configured spreadsheet.
        Column layout: Company | Status | Source | Date | Contact/Notes | URL
        """
        if READ_ONLY_MODE:
            raise PermissionError(
                "Sheets writes are disabled in READ_ONLY_MODE. "
                "Set READ_ONLY_MODE = False in config.py when ready."
            )
        if not GOOGLE_SHEETS_ID:
            raise ValueError(
                "GOOGLE_SHEETS_ID is not set. Add it to your .env file."
            )

        today = date.today().strftime("%m/%d/%Y")
        row = [company, "Applied", source, today, job_title, url]

        try:
            self._service.spreadsheets().values().append(
                spreadsheetId=GOOGLE_SHEETS_ID,
                range=f"{_SHEET_NAME}!A:F",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            ).execute()
        except HttpError as exc:
            logger.error("Sheets append error: %s", exc)
            raise

    def read_jobs(self) -> list[dict[str, str]]:
        """
        Read all tracked jobs from the spreadsheet.
        Returns a list of dicts with 'company' and 'job_title' keys.
        Column layout: Company | Status | Source | Date | job_title | URL
        """
        if not GOOGLE_SHEETS_ID:
            return []

        try:
            result = (
                self._service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=GOOGLE_SHEETS_ID,
                    range=f"{_SHEET_NAME}!A2:F",  # skip header row
                )
                .execute()
            )
            rows = result.get("values", [])
            jobs = []
            for row in rows:
                # Pad short rows so index access is safe
                padded = row + [""] * (6 - len(row))
                company = padded[0].strip()
                job_title = padded[4].strip()  # column E
                if company or job_title:
                    jobs.append({"company": company, "job_title": job_title})
            return jobs
        except HttpError as exc:
            logger.warning("Could not read jobs from sheet: %s", exc)
            return []

    def ensure_header_row(self) -> None:
        """Write column headers to A1:F1 if the sheet is empty. No-op in READ_ONLY_MODE."""
        if READ_ONLY_MODE:
            return
        if not GOOGLE_SHEETS_ID:
            return

        try:
            result = (
                self._service.spreadsheets()
                .values()
                .get(spreadsheetId=GOOGLE_SHEETS_ID, range=f"{_SHEET_NAME}!A1")
                .execute()
            )
            existing = result.get("values", [])
            if existing:
                return  # Headers already present

            self._service.spreadsheets().values().update(
                spreadsheetId=GOOGLE_SHEETS_ID,
                range=f"{_SHEET_NAME}!A1:F1",
                valueInputOption="USER_ENTERED",
                body={"values": [_COLUMNS]},
            ).execute()
        except HttpError as exc:
            logger.warning("Could not ensure header row: %s", exc)
