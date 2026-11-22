# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

from typing import Any

from backend.contacts.contact_emails import IMPORT_LINKEDIN_EMAIL_KEY
from backend.contacts.contacts_import_service import ContactImportService
from backend.db.interfaces import ContactSyncResult

# ai: claude-sonnet-5 | 2026-07-25
_PREAMBLE = (
    "Notes:\n"
    '"When exporting your connection data, you may notice"\n'
    '"some of the information is incomplete."\n'
    "\n"
)

_HEADER = "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"


_TEST_USER_ID = 1


class FakeContactsRepo:
    def __init__(self) -> None:
        self.synced_rows: list[dict[str, Any]] | None = None
        self.synced_for_user_id: int | None = None

    def sync_import(self, user_id: int, rows: list[dict[str, Any]]) -> ContactSyncResult:
        self.synced_for_user_id = user_id
        self.synced_rows = rows
        return ContactSyncResult(added=len(rows), updated=0, removed=0)


class FakeTitleRuleRepo:
    def __init__(self) -> None:
        self.applied_for_user_ids: list[int] = []

    def apply_all_rules(self, user_id: int) -> int:
        self.applied_for_user_ids.append(user_id)
        return 0


def _import_service(contacts_repo: FakeContactsRepo, title_rules_repo: FakeTitleRuleRepo | None = None):
    return ContactImportService(contacts_repo, title_rules_repo or FakeTitleRuleRepo())


def test_import_csv_skips_preamble_and_imports_valid_rows() -> None:
    csv_text = (
        _PREAMBLE + _HEADER + "Jane,Doe,https://linkedin.com/in/janedoe,,"
        "Acme Inc,Engineer,15 Jan 2024\n"
    )
    repo = FakeContactsRepo()
    result = _import_service(repo).import_csv(_TEST_USER_ID, csv_text.encode("utf-8"))

    assert result.imported == 1
    assert result.skipped == 0
    assert result.total_rows == 1
    assert repo.synced_rows is not None
    row = repo.synced_rows[0]
    assert row["full_name"] == "Jane Doe"
    assert row["company_raw"] == "Acme Inc"
    assert row["company_normalized"] == "acme"
    assert row["profile_url"] == "https://linkedin.com/in/janedoe"
    assert row["connected_on"] == "2024-01-15"


def test_import_csv_handles_utf8_bom() -> None:
    csv_text = _PREAMBLE + _HEADER + "Jane,Doe,,,\n"
    repo = FakeContactsRepo()
    result = _import_service(repo).import_csv(
        _TEST_USER_ID, b"\xef\xbb\xbf" + csv_text.encode("utf-8")
    )
    assert result.imported == 1


def test_import_csv_skips_row_with_no_name() -> None:
    csv_text = _PREAMBLE + _HEADER + ",,https://linkedin.com/in/noname,,Acme,,\n"
    repo = FakeContactsRepo()
    result = _import_service(repo).import_csv(_TEST_USER_ID, csv_text.encode("utf-8"))

    assert result.imported == 0
    assert result.skipped == 1
    assert result.skipped_reasons == {"no_name": 1}


def test_import_csv_row_with_unparseable_date_still_imports() -> None:
    csv_text = (
        _PREAMBLE + _HEADER + "Jane,Doe,,,,Engineer,not-a-real-date\n"
    )
    repo = FakeContactsRepo()
    result = _import_service(repo).import_csv(_TEST_USER_ID, csv_text.encode("utf-8"))

    assert result.imported == 1
    assert repo.synced_rows[0]["connected_on"] is None


def test_import_csv_dedupes_duplicate_profile_url_within_file() -> None:
    csv_text = (
        _PREAMBLE
        + _HEADER
        + "Jane,Doe,https://linkedin.com/in/janedoe,,Acme,Engineer,\n"
        + "Jane,Doe2,https://linkedin.com/in/janedoe,,Acme,Manager,\n"
    )
    repo = FakeContactsRepo()
    result = _import_service(repo).import_csv(_TEST_USER_ID, csv_text.encode("utf-8"))

    assert result.imported == 1
    assert result.skipped == 1
    assert result.skipped_reasons == {"duplicate_in_file": 1}
    assert repo.synced_rows[0]["position"] == "Engineer"


def test_import_csv_row_with_no_profile_url_is_imported() -> None:
    csv_text = _PREAMBLE + _HEADER + "Jane,Doe,,,Acme,Engineer,\n"
    repo = FakeContactsRepo()
    result = _import_service(repo).import_csv(_TEST_USER_ID, csv_text.encode("utf-8"))

    assert result.imported == 1
    assert repo.synced_rows[0]["profile_url"] is None


def test_import_csv_no_header_row_found_returns_empty() -> None:
    csv_text = "just some garbage,not a real export\n"
    repo = FakeContactsRepo()
    result = _import_service(repo).import_csv(_TEST_USER_ID, csv_text.encode("utf-8"))

    assert result.imported == 0
    assert result.total_rows == 0
    assert repo.synced_rows == []


# ai: claude-sonnet-5 | 2026-09-25
def test_import_csv_builds_dedup_key_from_url_else_name_and_company() -> None:
    csv_text = (
        _PREAMBLE
        + _HEADER
        + "Jane,Doe,https://linkedin.com/in/janedoe,,Acme Inc,Engineer,\n"
        + "John,Smith,,,Acme Inc,Manager,\n"
    )
    repo = FakeContactsRepo()
    _import_service(repo).import_csv(_TEST_USER_ID, csv_text.encode("utf-8"))

    assert repo.synced_rows is not None
    assert repo.synced_rows[0]["dedup_key"] == "https://linkedin.com/in/janedoe"
    assert repo.synced_rows[1]["dedup_key"] == "john smith|acme"


# ai: claude-opus-5-5 | 2026-11-22 -- carried as the LinkedIn-typed address; a
# malformed value is dropped like a blank one.
def test_import_csv_carries_linkedin_email_and_blank_or_malformed_becomes_none() -> None:
    csv_text = (
        _PREAMBLE
        + _HEADER
        + "Jane,Doe,https://linkedin.com/in/janedoe, jane@example.com ,Acme,Engineer,\n"
        + "John,Smith,https://linkedin.com/in/johnsmith,,Acme,Manager,\n"
        + "Ann,Lee,https://linkedin.com/in/annlee,not an email,Acme,Manager,\n"
    )
    repo = FakeContactsRepo()
    _import_service(repo).import_csv(_TEST_USER_ID, csv_text.encode("utf-8"))

    assert repo.synced_rows is not None
    assert [row[IMPORT_LINKEDIN_EMAIL_KEY] for row in repo.synced_rows] == ["jane@example.com", None, None]


# ai: claude-sonnet-5 | 2026-09-25
def test_import_csv_reports_sync_counts() -> None:
    csv_text = _PREAMBLE + _HEADER + "Jane,Doe,,,Acme,Engineer,\n"
    repo = FakeContactsRepo()
    result = _import_service(repo).import_csv(_TEST_USER_ID, csv_text.encode("utf-8"))

    assert (result.added, result.updated, result.removed) == (1, 0, 0)


# ai: claude-sonnet-5 | 2026-09-28
def test_import_csv_computes_normalized_title() -> None:
    csv_text = (
        _PREAMBLE + _HEADER + "Jane,Doe,,,Acme,  Technical  Recruiter ,\n"
    )
    repo = FakeContactsRepo()
    _import_service(repo).import_csv(_TEST_USER_ID, csv_text.encode("utf-8"))

    assert repo.synced_rows[0]["position"] == "Technical  Recruiter"
    assert repo.synced_rows[0]["normalized_title"] == "technical recruiter"


def test_import_csv_applies_title_rules_to_the_importing_user_after_sync() -> None:
    csv_text = _PREAMBLE + _HEADER + "Jane,Doe,,,Acme,Recruiter,\n"
    repo = FakeContactsRepo()
    title_rules = FakeTitleRuleRepo()
    _import_service(repo, title_rules).import_csv(_TEST_USER_ID, csv_text.encode("utf-8"))

    assert title_rules.applied_for_user_ids == [_TEST_USER_ID]
