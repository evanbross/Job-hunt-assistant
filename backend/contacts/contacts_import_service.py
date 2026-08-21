# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# LICENSE: Personal Use Only
# This source code is made available for personal, non-commercial use only.
# You may read, study, and run this code for personal purposes.
# You may NOT:
#   - Use this code, in whole or in part, in any commercial product or service
#   - Sell, sublicense, or otherwise monetize this code or derivatives of it
#   - Redistribute this code without explicit written permission from ZombieTech LLC
#
# No warranty is provided, express or implied. Use at your own risk.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Parses a LinkedIn "Connections.csv" data export (Settings > Data privacy >
Get a copy of your data) and loads it into the contacts table so job postings
can be cross-referenced against people the user already knows.

LinkedIn's export has a few "Notes:" preamble lines before the real header
row, and its exact preamble length has changed across LinkedIn's own export
format revisions — so the header row is located by scanning for it rather
than assumed to start at a fixed line."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError, model_validator

from backend.scoring.company_matching import normalize_company_name

logger = logging.getLogger(__name__)


# ai: claude-sonnet-5 | 2026-07-25
# ai: claude-sonnet-5 | 2026-08-21 — replace_all gained a leading user_id
@runtime_checkable
class IContactRepository(Protocol):
    def replace_all(self, user_id: int, rows: list[dict[str, Any]]) -> None: ...


class LinkedInConnectionRow(BaseModel):
    first_name: str = ""
    last_name: str = ""
    url: str = ""
    company: str = ""
    position: str = ""
    connected_on_raw: str = ""

    @model_validator(mode="after")
    def _require_a_name(self) -> "LinkedInConnectionRow":
        if not (self.first_name.strip() or self.last_name.strip()):
            raise ValueError("row has no name")
        return self


@dataclass(frozen=True)
class ContactImportResult:
    imported: int
    skipped: int
    skipped_reasons: dict[str, int]
    total_rows: int


class ContactImportService:
    def __init__(self, contacts_repo: IContactRepository) -> None:
        self._contacts_repo = contacts_repo

    def import_csv(self, user_id: int, raw_bytes: bytes) -> ContactImportResult:
        rows = self._parse_rows(raw_bytes)
        skipped_reasons: dict[str, int] = {}
        seen_keys: set[str] = set()
        to_insert: list[dict[str, Any]] = []
        total_rows = 0

        for raw_row in rows:
            total_rows += 1
            try:
                parsed = LinkedInConnectionRow(
                    first_name=raw_row.get("First Name") or "",
                    last_name=raw_row.get("Last Name") or "",
                    url=raw_row.get("URL") or "",
                    company=raw_row.get("Company") or "",
                    position=raw_row.get("Position") or "",
                    connected_on_raw=raw_row.get("Connected On") or "",
                )
            except ValidationError:
                skipped_reasons["no_name"] = skipped_reasons.get("no_name", 0) + 1
                continue

            profile_url = parsed.url.strip()
            company_normalized = normalize_company_name(parsed.company)
            name_key = f"{parsed.first_name.strip().lower()} {parsed.last_name.strip().lower()}"
            dedup_key = profile_url or f"{name_key}|{company_normalized}"
            if dedup_key in seen_keys:
                skipped_reasons["duplicate_in_file"] = (
                    skipped_reasons.get("duplicate_in_file", 0) + 1
                )
                continue
            seen_keys.add(dedup_key)

            to_insert.append(
                {
                    "full_name": f"{parsed.first_name.strip()} {parsed.last_name.strip()}".strip(),
                    "company_raw": parsed.company.strip(),
                    "company_normalized": company_normalized,
                    "position": parsed.position.strip(),
                    "profile_url": profile_url or None,
                    "connected_on": self._parse_connected_on(parsed.connected_on_raw),
                    "imported_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
            )

        self._contacts_repo.replace_all(user_id, to_insert)

        skipped = sum(skipped_reasons.values())
        return ContactImportResult(
            imported=len(to_insert),
            skipped=skipped,
            skipped_reasons=skipped_reasons,
            total_rows=total_rows,
        )

    @staticmethod
    def _parse_rows(raw_bytes: bytes) -> list[dict[str, str]]:
        text = raw_bytes.decode("utf-8-sig")
        lines = text.splitlines()

        header_index = None
        for index, line in enumerate(lines):
            first_cell = line.split(",", 1)[0].strip().strip('"').lower()
            if first_cell == "first name":
                header_index = index
                break

        if header_index is None:
            logger.warning(
                "LinkedIn CSV import: no 'First Name' header row found; treating file as empty (contacts_import_service.py)"
            )
            return []

        reader = csv.DictReader(lines[header_index:])
        return list(reader)

    @staticmethod
    def _parse_connected_on(value: str) -> str | None:
        value = value.strip()
        if not value:
            return None
        try:
            parsed_date = datetime.strptime(value, "%d %b %Y")
        except ValueError:
            return None
        return parsed_date.date().isoformat()
