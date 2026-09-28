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

from datetime import UTC, datetime

import pytest

from backend.db import (
    PostgresContactEmailRepository,
    PostgresContactRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user

# ai: claude-sonnet-5 | 2026-07-25


def _contact_row(**overrides: object) -> dict[str, object]:
    row = {
        "full_name": "Jane Doe",
        "company_raw": "Acme Inc",
        "company_normalized": "acme",
        "position": "Engineer",
        "profile_url": "https://linkedin.com/in/janedoe",
        "connected_on": "2024-01-15",
        "imported_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    row.update(overrides)
    return row


def _import_row(**overrides: object) -> dict[str, object]:
    row = _contact_row(**overrides)
    row["email_address"] = row.get("email_address")
    row["dedup_key"] = row["profile_url"] or f"{str(row['full_name']).lower()}|{row['company_normalized']}"
    return row


def _new_user(conn: object, email: str = "owner@example.com") -> int:
    account = PostgresUserAccountRepository(conn).create_pending(
        email, f"seed-{email}", "2099-01-01"
    )
    set_current_user(conn.cursor(), account["id"])
    return account["id"]


def test_sync_import_adds_updates_and_reports_counts(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)

        first = repo.sync_import(
            user_id, [_import_row(), _import_row(full_name="John Smith", profile_url=None)]
        )
        conn.commit()
        assert (first.added, first.updated, first.removed) == (2, 0, 0)
        assert repo.count(user_id) == 2

        second = repo.sync_import(user_id, [_import_row(position="Staff Engineer")])
        conn.commit()
        assert (second.added, second.updated, second.removed) == (0, 1, 1)
        remaining = repo.get_all(user_id)
        assert [c["full_name"] for c in remaining] == ["Jane Doe"]
        assert remaining[0]["position"] == "Staff Engineer"
    finally:
        conn.close()


def test_sync_import_preserves_user_flags_and_keeps_acted_on_contacts(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        email_repo = PostgresContactEmailRepository(conn)
        repo.sync_import(
            user_id,
            [
                _import_row(),
                _import_row(full_name="Recruiter Rita", profile_url="https://linkedin.com/in/rita"),
                _import_row(full_name="Emailed Ed", profile_url="https://linkedin.com/in/ed"),
                _import_row(full_name="Untouched Ursula", profile_url="https://linkedin.com/in/u"),
            ],
        )
        by_name = {c["full_name"]: c["id"] for c in repo.get_all(user_id)}
        assert repo.update(user_id, by_name["Jane Doe"], {"interested": True})
        assert repo.update(user_id, by_name["Recruiter Rita"], {"recruiter_type": "unrated"})
        assert email_repo.add(user_id, by_name["Emailed Ed"], "2026-09-01T12:00:00+00:00") is not None
        conn.commit()

        # Re-import with none of the four people in the file except Jane.
        result = repo.sync_import(user_id, [_import_row(position="Changed")])
        conn.commit()

        assert result.removed == 1
        names = {c["full_name"] for c in repo.get_all(user_id)}
        assert names == {"Jane Doe", "Recruiter Rita", "Emailed Ed"}
        jane = next(c for c in repo.get_all(user_id) if c["full_name"] == "Jane Doe")
        assert jane["interested"] is True
        assert jane["position"] == "Changed"
    finally:
        conn.close()


def test_sync_import_blank_email_does_not_erase_a_typed_email(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        repo.sync_import(user_id, [_import_row()])
        contact_id = repo.get_all(user_id)[0]["id"]
        assert repo.update(user_id, contact_id, {"email_address": "jane@example.com"})

        repo.sync_import(user_id, [_import_row(email_address=None)])
        assert repo.get_all(user_id)[0]["email_address"] == "jane@example.com"

        repo.sync_import(user_id, [_import_row(email_address="new@example.com")])
        assert repo.get_all(user_id)[0]["email_address"] == "new@example.com"
    finally:
        conn.close()


def test_sync_import_with_empty_list_clears_untouched_contacts(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        repo.sync_import(user_id, [_import_row()])
        conn.commit()
        assert repo.count(user_id) == 1

        repo.sync_import(user_id, [])
        conn.commit()
        assert repo.count(user_id) == 0
    finally:
        conn.close()


def test_sync_import_rejects_unknown_columns(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        with pytest.raises(ValueError, match="unknown columns"):
            repo.sync_import(user_id, [_import_row(interested=True)])
    finally:
        conn.close()


def test_update_rejects_unknown_columns_and_scopes_to_user(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        owner_id = _new_user(conn)
        other_id = _new_user(conn, "other@example.com")
        repo = PostgresContactRepository(conn)
        set_current_user(conn.cursor(), owner_id)
        repo.sync_import(owner_id, [_import_row()])
        contact_id = repo.get_all(owner_id)[0]["id"]

        with pytest.raises(ValueError, match="unknown columns"):
            repo.update(owner_id, contact_id, {"full_name": "Hacked"})
        set_current_user(conn.cursor(), other_id)
        assert repo.update(other_id, contact_id, {"interested": True}) is False
        set_current_user(conn.cursor(), owner_id)
        assert repo.get_all(owner_id)[0]["interested"] is False
    finally:
        conn.close()


def test_list_with_emails_aggregates_email_log(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        email_repo = PostgresContactEmailRepository(conn)
        repo.sync_import(
            user_id,
            [_import_row(), _import_row(full_name="Bob Quiet", profile_url="https://linkedin.com/in/bob")],
        )
        jane_id = next(c["id"] for c in repo.get_all(user_id) if c["full_name"] == "Jane Doe")
        first = email_repo.add(user_id, jane_id, "2026-09-01T12:00:00+00:00")
        email_repo.add(user_id, jane_id, "2026-09-10T12:00:00+00:00")
        assert first is not None
        assert email_repo.set_response(user_id, first["id"], "2026-09-03T08:00:00+00:00")
        conn.commit()

        listed = {c["full_name"]: c for c in repo.list_with_emails(user_id)}
        assert [e["sent_at"] for e in listed["Jane Doe"]["emails"]] == [
            "2026-09-01T12:00:00+00:00",
            "2026-09-10T12:00:00+00:00",
        ]
        assert (
            listed["Jane Doe"]["emails"][0]["response_received_at"] == "2026-09-03T08:00:00+00:00"
        )
        assert listed["Jane Doe"]["emails"][1]["response_received_at"] is None
        assert listed["Bob Quiet"]["emails"] == []
    finally:
        conn.close()


def test_contact_email_repo_is_scoped_to_owning_user(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        owner_id = _new_user(conn)
        other_id = _new_user(conn, "other@example.com")
        repo = PostgresContactRepository(conn)
        email_repo = PostgresContactEmailRepository(conn)
        set_current_user(conn.cursor(), owner_id)
        repo.sync_import(owner_id, [_import_row()])
        contact_id = repo.get_all(owner_id)[0]["id"]

        set_current_user(conn.cursor(), other_id)
        assert email_repo.add(other_id, contact_id, "2026-09-01") is None

        set_current_user(conn.cursor(), owner_id)
        created = email_repo.add(owner_id, contact_id, "2026-09-01")
        assert created is not None
        set_current_user(conn.cursor(), other_id)
        assert email_repo.set_response(other_id, created["id"], "2026-09-02") is False
        assert email_repo.delete(other_id, created["id"]) is False
        set_current_user(conn.cursor(), owner_id)
        assert email_repo.delete(owner_id, created["id"]) is True
        assert repo.list_with_emails(owner_id)[0]["emails"] == []
    finally:
        conn.close()


def test_deleting_a_contact_cascades_to_its_email_log(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        email_repo = PostgresContactEmailRepository(conn)
        repo.sync_import(user_id, [_import_row()])
        contact_id = repo.get_all(user_id)[0]["id"]
        created = email_repo.add(user_id, contact_id, "2026-09-01")
        assert created is not None

        conn.cursor().execute("DELETE FROM contacts WHERE id = %s", (contact_id,))
        assert email_repo.set_response(user_id, created["id"], "2026-09-02") is False
    finally:
        conn.close()


# ai: claude-sonnet-5 | 2026-09-25
def test_bulk_set_recruiter_updates_only_listed_contacts_owned_by_user(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        owner_id = _new_user(conn)
        other_id = _new_user(conn, "other@example.com")
        repo = PostgresContactRepository(conn)
        set_current_user(conn.cursor(), owner_id)
        repo.sync_import(
            owner_id,
            [
                _import_row(),
                _import_row(full_name="Bob", profile_url="https://linkedin.com/in/bob"),
                _import_row(full_name="Cy", profile_url="https://linkedin.com/in/cy"),
            ],
        )
        set_current_user(conn.cursor(), other_id)
        repo.sync_import(other_id, [_import_row(full_name="Theirs", profile_url="https://linkedin.com/in/t")])
        set_current_user(conn.cursor(), owner_id)
        owner_ids = {c["full_name"]: c["id"] for c in repo.get_all(owner_id)}
        set_current_user(conn.cursor(), other_id)
        other_contact_id = repo.get_all(other_id)[0]["id"]

        set_current_user(conn.cursor(), owner_id)
        updated = repo.bulk_set_recruiter(
            owner_id, [owner_ids["Jane Doe"], owner_ids["Bob"], other_contact_id], "unrated"
        )
        conn.commit()

        assert updated == 2
        types = {c["full_name"]: c["recruiter_type"] for c in repo.list_with_emails(owner_id)}
        assert types == {"Jane Doe": "unrated", "Bob": "unrated", "Cy": None}
        set_current_user(conn.cursor(), other_id)
        assert repo.list_with_emails(other_id)[0]["recruiter_type"] is None

        set_current_user(conn.cursor(), owner_id)
        assert repo.bulk_set_recruiter(owner_id, [owner_ids["Bob"]], None) == 1
        assert {
            c["full_name"]: c["recruiter_type"] for c in repo.list_with_emails(owner_id)
        }["Bob"] is None
    finally:
        conn.close()


# ai: claude-sonnet-5 | 2026-09-25
def test_notes_survive_reimport_and_protect_contact_from_pruning(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        repo.sync_import(
            user_id,
            [
                _import_row(),
                _import_row(full_name="Noted Nora", profile_url="https://linkedin.com/in/nora"),
            ],
        )
        by_name = {c["full_name"]: c["id"] for c in repo.get_all(user_id)}
        assert repo.update(user_id, by_name["Jane Doe"], {"notes": "Hiring manager at Acme"})
        assert repo.update(user_id, by_name["Noted Nora"], {"notes": "Ask about referral"})
        conn.commit()

        result = repo.sync_import(user_id, [_import_row(position="Director")])
        conn.commit()

        assert result.removed == 0
        listed = {c["full_name"]: c for c in repo.list_with_emails(user_id)}
        assert set(listed) == {"Jane Doe", "Noted Nora"}
        assert listed["Jane Doe"]["notes"] == "Hiring manager at Acme"
        assert listed["Jane Doe"]["position"] == "Director"
    finally:
        conn.close()
