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

from __future__ import annotations

from typing import Any

from backend.contacts.contacts_import_service import ContactImportService

# ai: claude-sonnet-5 | 2026-07-25
_PREAMBLE = (
    "Notes:\n"
    '"When exporting your connection data, you may notice"\n'
    '"some of the information is incomplete."\n'
    "\n"
)

_HEADER = "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"


class FakeContactsRepo:
    def __init__(self) -> None:
        self.replaced_with: list[dict[str, Any]] | None = None

    def replace_all(self, rows: list[dict[str, Any]]) -> None:
        self.replaced_with = rows


def test_import_csv_skips_preamble_and_imports_valid_rows() -> None:
    csv_text = (
        _PREAMBLE + _HEADER + "Jane,Doe,https://linkedin.com/in/janedoe,,"
        "Acme Inc,Engineer,15 Jan 2024\n"
    )
    repo = FakeContactsRepo()
    result = ContactImportService(repo).import_csv(csv_text.encode("utf-8"))

    assert result.imported == 1
    assert result.skipped == 0
    assert result.total_rows == 1
    assert repo.replaced_with is not None
    row = repo.replaced_with[0]
    assert row["full_name"] == "Jane Doe"
    assert row["company_raw"] == "Acme Inc"
    assert row["company_normalized"] == "acme"
    assert row["profile_url"] == "https://linkedin.com/in/janedoe"
    assert row["connected_on"] == "2024-01-15"


def test_import_csv_handles_utf8_bom() -> None:
    csv_text = _PREAMBLE + _HEADER + "Jane,Doe,,,\n"
    repo = FakeContactsRepo()
    result = ContactImportService(repo).import_csv(
        b"\xef\xbb\xbf" + csv_text.encode("utf-8")
    )
    assert result.imported == 1


def test_import_csv_skips_row_with_no_name() -> None:
    csv_text = _PREAMBLE + _HEADER + ",,https://linkedin.com/in/noname,,Acme,,\n"
    repo = FakeContactsRepo()
    result = ContactImportService(repo).import_csv(csv_text.encode("utf-8"))

    assert result.imported == 0
    assert result.skipped == 1
    assert result.skipped_reasons == {"no_name": 1}


def test_import_csv_row_with_unparseable_date_still_imports() -> None:
    csv_text = (
        _PREAMBLE + _HEADER + "Jane,Doe,,,,Engineer,not-a-real-date\n"
    )
    repo = FakeContactsRepo()
    result = ContactImportService(repo).import_csv(csv_text.encode("utf-8"))

    assert result.imported == 1
    assert repo.replaced_with[0]["connected_on"] is None


def test_import_csv_dedupes_duplicate_profile_url_within_file() -> None:
    csv_text = (
        _PREAMBLE
        + _HEADER
        + "Jane,Doe,https://linkedin.com/in/janedoe,,Acme,Engineer,\n"
        + "Jane,Doe2,https://linkedin.com/in/janedoe,,Acme,Manager,\n"
    )
    repo = FakeContactsRepo()
    result = ContactImportService(repo).import_csv(csv_text.encode("utf-8"))

    assert result.imported == 1
    assert result.skipped == 1
    assert result.skipped_reasons == {"duplicate_in_file": 1}
    assert repo.replaced_with[0]["position"] == "Engineer"


def test_import_csv_row_with_no_profile_url_is_imported() -> None:
    csv_text = _PREAMBLE + _HEADER + "Jane,Doe,,,Acme,Engineer,\n"
    repo = FakeContactsRepo()
    result = ContactImportService(repo).import_csv(csv_text.encode("utf-8"))

    assert result.imported == 1
    assert repo.replaced_with[0]["profile_url"] is None


def test_import_csv_no_header_row_found_returns_empty() -> None:
    csv_text = "just some garbage,not a real export\n"
    repo = FakeContactsRepo()
    result = ContactImportService(repo).import_csv(csv_text.encode("utf-8"))

    assert result.imported == 0
    assert result.total_rows == 0
    assert repo.replaced_with == []
