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

import psycopg2
import pytest

from backend.db import (
    PostgresContactEmploymentHistoryRepository,
    PostgresContactOutreachRepository,
    PostgresContactRepository,
    PostgresUserAccountRepository,
    get_connection,
    init_db,
)
from backend.db.row_level_security import set_current_user

# ai: claude-sonnet-5 | 2026-07-25


def _contact_row(**overrides: object) -> dict[str, object]:
    row = {
        "full_name": "Jane Doe",
        "company_raw": "Acme Inc",
        "company_normalized": "acme",
        "position": "Engineer",
        "normalized_title": "engineer",
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
        outreach_repo = PostgresContactOutreachRepository(conn)
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
        assert outreach_repo.add(user_id, by_name["Emailed Ed"], "2026-09-01T12:00:00+00:00", "email") is not None
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


# ai: claude-sonnet-5 | 2026-09-29
def test_sync_import_keeps_a_manual_not_a_recruiter_decision(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        repo.sync_import(
            user_id,
            [
                _import_row(),
                _import_row(full_name="Dismissed Dan", profile_url="https://linkedin.com/in/dan"),
                _import_row(full_name="Untouched Ursula", profile_url="https://linkedin.com/in/u"),
            ],
        )
        by_name = {c["full_name"]: c["id"] for c in repo.get_all(user_id)}
        # recruiter_type stays NULL -- the manual decision was "not a recruiter",
        # so decided_by is the only thing distinguishing it from an untouched row.
        assert repo.update(
            user_id, by_name["Dismissed Dan"], {"recruiter_type": None, "decided_by": "manual"}
        )
        conn.commit()

        # Re-import with none of the three people in the file except Jane.
        result = repo.sync_import(user_id, [_import_row(position="Changed")])
        conn.commit()

        assert result.removed == 1
        names = {c["full_name"] for c in repo.get_all(user_id)}
        assert names == {"Jane Doe", "Dismissed Dan"}
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
        # ai: claude-sonnet-5 | 2026-10-13 -- interested now defaults to NULL
        # (undecided), not FALSE; the rejected update above must have left it
        # untouched either way.
        assert repo.get_all(owner_id)[0]["interested"] is None
    finally:
        conn.close()


def test_list_with_outreach_aggregates_outreach_log(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        outreach_repo = PostgresContactOutreachRepository(conn)
        repo.sync_import(
            user_id,
            [_import_row(), _import_row(full_name="Bob Quiet", profile_url="https://linkedin.com/in/bob")],
        )
        jane_id = next(c["id"] for c in repo.get_all(user_id) if c["full_name"] == "Jane Doe")
        first = outreach_repo.add(user_id, jane_id, "2026-09-01T12:00:00+00:00", "email")
        outreach_repo.add(user_id, jane_id, "2026-09-10T12:00:00+00:00", "linkedin")
        assert first is not None
        assert outreach_repo.set_response(user_id, first["id"], "2026-09-03T08:00:00+00:00")
        conn.commit()

        listed = {c["full_name"]: c for c in repo.list_with_outreach(user_id)}
        assert [e["sent_at"] for e in listed["Jane Doe"]["outreach"]] == [
            "2026-09-01T12:00:00+00:00",
            "2026-09-10T12:00:00+00:00",
        ]
        assert (
            listed["Jane Doe"]["outreach"][0]["response_received_at"] == "2026-09-03T08:00:00+00:00"
        )
        assert listed["Jane Doe"]["outreach"][1]["response_received_at"] is None
        assert [e["channel"] for e in listed["Jane Doe"]["outreach"]] == ["email", "linkedin"]
        assert listed["Bob Quiet"]["outreach"] == []
    finally:
        conn.close()


def test_contact_outreach_repo_is_scoped_to_owning_user(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        owner_id = _new_user(conn)
        other_id = _new_user(conn, "other@example.com")
        repo = PostgresContactRepository(conn)
        outreach_repo = PostgresContactOutreachRepository(conn)
        set_current_user(conn.cursor(), owner_id)
        repo.sync_import(owner_id, [_import_row()])
        contact_id = repo.get_all(owner_id)[0]["id"]

        set_current_user(conn.cursor(), other_id)
        assert outreach_repo.add(other_id, contact_id, "2026-09-01", "email") is None

        set_current_user(conn.cursor(), owner_id)
        created = outreach_repo.add(owner_id, contact_id, "2026-09-01", "email")
        assert created is not None
        set_current_user(conn.cursor(), other_id)
        assert outreach_repo.set_response(other_id, created["id"], "2026-09-02") is False
        assert outreach_repo.delete(other_id, created["id"]) is False
        set_current_user(conn.cursor(), owner_id)
        assert outreach_repo.delete(owner_id, created["id"]) is True
        assert repo.list_with_outreach(owner_id)[0]["outreach"] == []
    finally:
        conn.close()


def test_deleting_a_contact_cascades_to_its_outreach_log(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        outreach_repo = PostgresContactOutreachRepository(conn)
        repo.sync_import(user_id, [_import_row()])
        contact_id = repo.get_all(user_id)[0]["id"]
        created = outreach_repo.add(user_id, contact_id, "2026-09-01", "email")
        assert created is not None

        conn.cursor().execute("DELETE FROM contacts WHERE id = %s", (contact_id,))
        assert outreach_repo.set_response(user_id, created["id"], "2026-09-02") is False
    finally:
        conn.close()


# ai: claude-sonnet-5 | 2026-10-15
def test_employment_history_add_list_update_delete(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        history_repo = PostgresContactEmploymentHistoryRepository(conn)
        repo.sync_import(user_id, [_import_row()])
        contact_id = repo.get_all(user_id)[0]["id"]

        created = history_repo.add(
            user_id,
            contact_id,
            company="Watchfire",
            title="Engineer",
            start_date="2015",
            end_date="2018",
            source="manual",
            captured_at="2026-10-15T00:00:00+00:00",
        )
        assert created is not None
        assert created["company"] == "Watchfire"

        rows = history_repo.list_for_contact(user_id, contact_id)
        assert len(rows) == 1
        assert rows[0]["id"] == created["id"]

        updated = history_repo.update(user_id, created["id"], {"end_date": "2019"})
        assert updated is not None
        assert updated["end_date"] == "2019"

        assert history_repo.delete(user_id, created["id"]) is True
        assert history_repo.list_for_contact(user_id, contact_id) == []
    finally:
        conn.close()


def test_employment_history_repo_is_scoped_to_owning_user(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        owner_id = _new_user(conn)
        other_id = _new_user(conn, "other@example.com")
        repo = PostgresContactRepository(conn)
        history_repo = PostgresContactEmploymentHistoryRepository(conn)
        set_current_user(conn.cursor(), owner_id)
        repo.sync_import(owner_id, [_import_row()])
        contact_id = repo.get_all(owner_id)[0]["id"]

        set_current_user(conn.cursor(), other_id)
        assert (
            history_repo.add(
                other_id,
                contact_id,
                company="Watchfire",
                title="",
                start_date=None,
                end_date=None,
                source="manual",
                captured_at="2026-10-15T00:00:00+00:00",
            )
            is None
        )

        set_current_user(conn.cursor(), owner_id)
        created = history_repo.add(
            owner_id,
            contact_id,
            company="Watchfire",
            title="",
            start_date=None,
            end_date=None,
            source="manual",
            captured_at="2026-10-15T00:00:00+00:00",
        )
        assert created is not None

        set_current_user(conn.cursor(), other_id)
        assert history_repo.update(other_id, created["id"], {"company": "x"}) is None
        assert history_repo.delete(other_id, created["id"]) is False
        assert history_repo.list_for_contact(other_id, contact_id) == []

        set_current_user(conn.cursor(), owner_id)
        assert history_repo.delete(owner_id, created["id"]) is True
    finally:
        conn.close()


def test_deleting_a_contact_cascades_to_its_employment_history(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        history_repo = PostgresContactEmploymentHistoryRepository(conn)
        repo.sync_import(user_id, [_import_row()])
        contact_id = repo.get_all(user_id)[0]["id"]
        created = history_repo.add(
            user_id,
            contact_id,
            company="Watchfire",
            title="",
            start_date=None,
            end_date=None,
            source="manual",
            captured_at="2026-10-15T00:00:00+00:00",
        )
        assert created is not None

        conn.cursor().execute("DELETE FROM contacts WHERE id = %s", (contact_id,))
        assert history_repo.update(user_id, created["id"], {"company": "x"}) is None
    finally:
        conn.close()


def test_employment_history_update_rejects_unknown_columns(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        history_repo = PostgresContactEmploymentHistoryRepository(conn)
        repo.sync_import(user_id, [_import_row()])
        contact_id = repo.get_all(user_id)[0]["id"]
        created = history_repo.add(
            user_id,
            contact_id,
            company="Watchfire",
            title="",
            start_date=None,
            end_date=None,
            source="manual",
            captured_at="2026-10-15T00:00:00+00:00",
        )
        assert created is not None
        with pytest.raises(ValueError, match="unknown columns"):
            history_repo.update(user_id, created["id"], {"not_a_real_column": "x"})
    finally:
        conn.close()


# ai: claude-sonnet-5 | 2026-10-18
def test_employment_history_company_and_title_are_not_null_at_the_db(pg_dsn: str) -> None:
    """2026-10-18 full review, Medium finding: routes/contacts.py's
    ContactEmploymentHistoryUpdate now rejects an explicit null for
    company/title before it ever reaches this repository (see
    tests/test_contacts_routes.py's rejects_null_company/rejects_null_title).
    This is the real boundary that validation exists to protect -- proves the
    column really is NOT NULL (backend/db/schema.py), so a future validator
    regression would resurface as this IntegrityError, not a silent write."""
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        history_repo = PostgresContactEmploymentHistoryRepository(conn)
        repo.sync_import(user_id, [_import_row()])
        contact_id = repo.get_all(user_id)[0]["id"]
        created = history_repo.add(
            user_id,
            contact_id,
            company="Watchfire",
            title="Engineer",
            start_date=None,
            end_date=None,
            source="manual",
            captured_at="2026-10-15T00:00:00+00:00",
        )
        assert created is not None
        conn.commit()  # persist the setup so the rollbacks below only undo the failed UPDATE
        with pytest.raises(psycopg2.errors.NotNullViolation):
            history_repo.update(user_id, created["id"], {"company": None})
        conn.rollback()
        with pytest.raises(psycopg2.errors.NotNullViolation):
            history_repo.update(user_id, created["id"], {"title": None})
    finally:
        conn.rollback()
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
        types = {c["full_name"]: c["recruiter_type"] for c in repo.list_with_outreach(owner_id)}
        assert types == {"Jane Doe": "unrated", "Bob": "unrated", "Cy": None}
        set_current_user(conn.cursor(), other_id)
        assert repo.list_with_outreach(other_id)[0]["recruiter_type"] is None

        set_current_user(conn.cursor(), owner_id)
        assert repo.bulk_set_recruiter(owner_id, [owner_ids["Bob"]], None) == 1
        assert {
            c["full_name"]: c["recruiter_type"] for c in repo.list_with_outreach(owner_id)
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
        listed = {c["full_name"]: c for c in repo.list_with_outreach(user_id)}
        assert set(listed) == {"Jane Doe", "Noted Nora"}
        assert listed["Jane Doe"]["notes"] == "Hiring manager at Acme"
        assert listed["Jane Doe"]["position"] == "Director"
    finally:
        conn.close()


# ai: claude-sonnet-5 | 2026-10-13
# Exercises the real one-time migration path in backend/db/schema.py's
# _migrate(): a row written back when interested was NOT NULL DEFAULT FALSE
# (simulated here by reverting the column, since pg_dsn's own init_db() has
# already migrated it) must get backfilled to NULL (undecided) the next time
# init_db() runs -- and a second init_db() run afterward must leave an
# explicit FALSE set post-migration alone (the gate must not re-trigger).
def test_migration_backfills_legacy_interested_false_to_undecided(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        repo.sync_import(user_id, [_import_row(full_name="Legacy Larry")])
        legacy_id = repo.get_all(user_id)[0]["id"]

        cur = conn.cursor()
        cur.execute("ALTER TABLE contacts ALTER COLUMN interested SET DEFAULT FALSE")
        cur.execute("UPDATE contacts SET interested = FALSE WHERE id = %s", (legacy_id,))
        cur.execute("ALTER TABLE contacts ALTER COLUMN interested SET NOT NULL")
        conn.commit()

        init_db(pg_dsn)

        cur.execute("SELECT interested FROM contacts WHERE id = %s", (legacy_id,))
        assert cur.fetchone()["interested"] is None

        assert repo.update(user_id, legacy_id, {"interested": False})
        conn.commit()

        init_db(pg_dsn)

        cur.execute("SELECT interested FROM contacts WHERE id = %s", (legacy_id,))
        assert cur.fetchone()["interested"] is False
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-10-14
# An install from before the LinkedIn outreach queue still has contact_emails
# (no channel column, old constraint/sequence/index names). init_db() must
# rename it in place -- keeping the logged rows, backfilled as 'email' -- not
# create an empty contact_outreach next to it.
def test_migration_renames_legacy_contact_emails_and_backfills_channel(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        repo.sync_import(user_id, [_import_row()])
        contact_id = repo.get_all(user_id)[0]["id"]
        created = PostgresContactOutreachRepository(conn).add(
            user_id, contact_id, "2026-09-01T12:00:00+00:00", "email"
        )
        assert created is not None

        cur = conn.cursor()
        cur.execute("ALTER TABLE contact_outreach DROP CONSTRAINT contact_outreach_channel_check")
        cur.execute("ALTER TABLE contact_outreach DROP COLUMN channel")
        cur.execute("ALTER TABLE contact_outreach RENAME TO contact_emails")
        # Every constraint, including PostgreSQL 18+'s named NOT NULL ones.
        cur.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'contact_emails'::regclass"
        )
        for name in [row["conname"] for row in cur.fetchall()]:
            legacy_name = name.replace("contact_outreach_", "contact_emails_", 1)
            cur.execute(f"ALTER TABLE contact_emails RENAME CONSTRAINT {name} TO {legacy_name}")
        cur.execute("ALTER SEQUENCE contact_outreach_id_seq RENAME TO contact_emails_id_seq")
        cur.execute(
            "ALTER INDEX idx_contact_outreach_contact_id RENAME TO idx_contact_emails_contact_id"
        )
        cur.execute("ALTER INDEX idx_contact_outreach_user_id RENAME TO idx_contact_emails_user_id")
        conn.commit()

        init_db(pg_dsn)

        cur.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name IN ('contact_emails', 'contact_outreach')"
        )
        assert [row["table_name"] for row in cur.fetchall()] == ["contact_outreach"]
        cur.execute("SELECT id, contact_id, channel FROM contact_outreach")
        assert [dict(row) for row in cur.fetchall()] == [
            {"id": created["id"], "contact_id": contact_id, "channel": "email"}
        ]
        cur.execute("SELECT conname FROM pg_constraint WHERE conrelid = 'contact_outreach'::regclass")
        constraint_names = {row["conname"] for row in cur.fetchall()}
        assert not {name for name in constraint_names if name.startswith("contact_emails_")}
        assert {
            "contact_outreach_channel_check",
            "contact_outreach_contact_id_fkey",
            "contact_outreach_pkey",
            "contact_outreach_user_id_fkey",
        } <= constraint_names
        # The renamed sequence still drives new ids.
        added = PostgresContactOutreachRepository(conn).add(
            user_id, contact_id, "2026-09-02T12:00:00+00:00", "linkedin"
        )
        assert added is not None and added["id"] > created["id"]
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-10-14
def test_outreach_channel_rejects_unknown_values(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        repo.sync_import(user_id, [_import_row()])
        contact_id = repo.get_all(user_id)[0]["id"]
        with pytest.raises(psycopg2.errors.CheckViolation):
            PostgresContactOutreachRepository(conn).add(
                user_id, contact_id, "2026-09-01T12:00:00+00:00", "carrier_pigeon"
            )
    finally:
        conn.rollback()
        conn.close()


# ai: claude-opus-5-5 | 2026-10-14
def test_staging_a_draft_unstages_any_other_contact(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        repo.sync_import(
            user_id,
            [_import_row(), _import_row(full_name="Bob Next", profile_url="https://linkedin.com/in/bob")],
        )
        ids = {c["full_name"]: c["id"] for c in repo.get_all(user_id)}

        assert repo.stage_outreach_draft(user_id, ids["Jane Doe"], "Hi Jane", "2026-10-14T12:00:00+00:00")
        assert repo.stage_outreach_draft(user_id, ids["Bob Next"], "Hi Bob", "2026-10-14T12:05:00+00:00")
        conn.commit()

        staged = repo.get_staged_outreach_draft(user_id, "2026-10-14T11:00:00+00:00")
        assert staged == {
            "id": ids["Bob Next"],
            "full_name": "Bob Next",
            "profile_url": "https://linkedin.com/in/bob",
            "outreach_draft": "Hi Bob",
        }
        jane = repo.get_by_id(user_id, ids["Jane Doe"])
        assert jane is not None
        # Jane's draft text is kept for the queue to show; only the stage moved.
        assert jane["outreach_draft"] == "Hi Jane"
        assert jane["outreach_draft_staged_at"] is None
        listed = {c["full_name"]: c for c in repo.list_with_outreach(user_id)}
        assert listed["Jane Doe"]["outreach_draft"] == "Hi Jane"
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-10-14
def test_staged_draft_is_scoped_to_user_and_expires(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        owner_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        repo.sync_import(owner_id, [_import_row()])
        contact_id = repo.get_all(owner_id)[0]["id"]
        other_id = _new_user(conn, "other@example.com")

        assert repo.stage_outreach_draft(other_id, contact_id, "Hijack", "2026-10-14T12:00:00+00:00") is False
        set_current_user(conn.cursor(), owner_id)
        assert repo.get_by_id(owner_id, contact_id)["outreach_draft"] is None

        assert repo.stage_outreach_draft(owner_id, contact_id, "Hi Jane", "2026-10-14T12:00:00+00:00")
        conn.commit()
        assert repo.get_staged_outreach_draft(owner_id, "2026-10-14T12:00:00+00:00") is None
        assert repo.get_staged_outreach_draft(owner_id, "2026-10-14T11:59:59+00:00") is not None
        assert repo.get_staged_outreach_draft(other_id, "2026-10-14T00:00:00+00:00") is None
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-10-14
def test_claim_unstages_once_and_clear_removes_the_draft(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresContactRepository(conn)
        repo.sync_import(user_id, [_import_row()])
        contact_id = repo.get_all(user_id)[0]["id"]
        assert repo.stage_outreach_draft(user_id, contact_id, "Hi Jane", "2026-10-14T12:00:00+00:00")

        assert repo.claim_staged_outreach_draft(user_id, contact_id) is True
        assert repo.claim_staged_outreach_draft(user_id, contact_id) is False
        assert repo.get_staged_outreach_draft(user_id, "2026-10-14T00:00:00+00:00") is None
        assert repo.get_by_id(user_id, contact_id)["outreach_draft"] == "Hi Jane"

        repo.clear_outreach_draft(user_id, contact_id)
        assert repo.get_by_id(user_id, contact_id)["outreach_draft"] is None
    finally:
        conn.close()
