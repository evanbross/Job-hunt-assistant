# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Typed contact emails: the request model's rules, the repository against the real
schema (TESTING_STANDARDS.md: no mocked databases), and the one-time move of the old
contacts.email_address / contacts.personal_email columns into contact_email_addresses."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.contacts.contact_emails import (
    EMAIL_TYPE_PREFERENCE,
    EMAIL_TYPES,
    ContactEmailInput,
    ContactEmailList,
)
from backend.contacts.contact_identity import contact_identity_columns
from backend.db import (
    DuplicateContactEmailError,
    PostgresContactEmailRepository,
    PostgresContactRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user
from backend.db.schema import migrate_schema

_TS = "2026-11-22T12:00:00+00:00"


def _new_user(conn: Any, email: str) -> int:
    account = PostgresUserAccountRepository(conn).create_pending(email, f"seed-{email}", "2099-01-01")
    set_current_user(conn.cursor(), account["id"])
    return int(account["id"])


def _contact(conn: Any, user_id: int, name: str) -> int:
    contact_id, created = PostgresContactRepository(conn).create_manual(
        user_id, contact_identity_columns(name, "Agency Co", "Recruiter", None), {}, _TS
    )
    assert created
    return contact_id


# ai: claude-opus-5-5 | 2026-11-22
def test_every_email_type_has_a_place_in_the_preference_order() -> None:
    assert sorted(EMAIL_TYPE_PREFERENCE) == sorted(EMAIL_TYPES)


# ai: claude-opus-5-5 | 2026-11-22
def test_input_model_trims_and_keeps_a_label_only_on_custom() -> None:
    custom = ContactEmailInput.model_validate(
        {"address": " a@b.example ", "email_type": "custom", "custom_label": "  Side gig "}
    )
    assert (custom.address, custom.custom_label) == ("a@b.example", "Side gig")
    home = ContactEmailInput.model_validate({"address": "a@b.example", "email_type": "home", "custom_label": "x"})
    assert home.custom_label is None
    for bad in (
        {"address": "a@b.example", "email_type": "custom", "custom_label": "   "},
        {"address": "a@b.example", "email_type": "pager"},
        {"address": "", "email_type": "home"},
        {"address": "two words@b.example", "email_type": "home"},
        {"address": "a@b.example", "email_type": "custom", "custom_label": "x" * 41},
    ):
        with pytest.raises(ValidationError):
            ContactEmailInput.model_validate(bad)


# ai: claude-opus-5-5 | 2026-11-22
def test_input_list_rejects_repeats_and_a_second_linkedin_address() -> None:
    adapter = TypeAdapter(ContactEmailList)
    assert len(adapter.validate_python([{"address": "a@b.example", "email_type": "linkedin"},
                                        {"address": "c@d.example", "email_type": "home"}])) == 2
    with pytest.raises(ValidationError):
        adapter.validate_python([{"address": "a@b.example", "email_type": "home"},
                                 {"address": "A@B.example", "email_type": "work"}])
    with pytest.raises(ValidationError):
        adapter.validate_python([{"address": "a@b.example", "email_type": "linkedin"},
                                 {"address": "c@d.example", "email_type": "linkedin"}])


# ai: claude-opus-5-5 | 2026-11-22
def test_repository_add_replace_delete_and_duplicates(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn, "emails-owner@example.com")
        contact_id = _contact(conn, user_id, "Rita Recruiter")
        repo = PostgresContactEmailRepository(conn)

        home = repo.add(user_id, contact_id, address="rita@home.example", email_type="home",
                        custom_label=None, created_at=_TS)
        assert home is not None
        assert (home["contact_id"], home["address"], home["email_type"], home["custom_label"]) == (
            contact_id, "rita@home.example", "home", None,
        )
        with pytest.raises(DuplicateContactEmailError) as dup:
            repo.add(user_id, contact_id, address="RITA@HOME.example", email_type="work",
                     custom_label=None, created_at=_TS)
        assert dup.value.reason == "address"
        repo.add(user_id, contact_id, address="rita@li.example", email_type="linkedin",
                 custom_label=None, created_at=_TS)
        with pytest.raises(DuplicateContactEmailError) as second:
            repo.add(user_id, contact_id, address="rita@li2.example", email_type="linkedin",
                     custom_label=None, created_at=_TS)
        assert second.value.reason == "linkedin"

        # The savepoint kept the transaction usable after both clashes.
        replaced = repo.replace(user_id, home["id"], address="rita@side.example",
                                email_type="custom", custom_label="Side gig")
        assert replaced is not None and (replaced["email_type"], replaced["custom_label"]) == ("custom", "Side gig")
        with pytest.raises(DuplicateContactEmailError):
            repo.replace(user_id, home["id"], address="rita@li.example", email_type="other", custom_label=None)
        conn.commit()

        # Another user can't add to, change or remove this contact's addresses.
        other = _new_user(conn, "emails-other@example.com")
        assert repo.add(other, contact_id, address="x@y.example", email_type="home",
                        custom_label=None, created_at=_TS) is None
        assert repo.replace(other, home["id"], address="x@y.example", email_type="home", custom_label=None) is None
        assert repo.delete(other, home["id"]) is False

        set_current_user(conn.cursor(), user_id)
        assert repo.delete(user_id, home["id"]) is True
        assert repo.delete(user_id, home["id"]) is False
        conn.commit()
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-22
def test_migrate_schema_moves_the_old_email_columns_into_contact_emails(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn, "emails-migrate@example.com")
        imported = _contact(conn, user_id, "Imported Ida")
        manual = _contact(conn, user_id, "Manual Max")
        both = _contact(conn, user_id, "Both Bea")
        cur = conn.cursor()
        cur.execute("UPDATE contacts SET source = 'linkedin_import' WHERE id IN (%s, %s)", (imported, both))
        # The columns as a database migrated before 2026-11-22 still has them.
        cur.execute("ALTER TABLE contacts ADD COLUMN email_address TEXT")
        cur.execute("ALTER TABLE contacts ADD COLUMN personal_email TEXT")
        cur.execute("UPDATE contacts SET email_address = 'ida@li.example' WHERE id = %s", (imported,))
        cur.execute("UPDATE contacts SET email_address = ' max@agency.example ' WHERE id = %s", (manual,))
        cur.execute(
            "UPDATE contacts SET email_address = 'Bea@Home.example', personal_email = 'bea@home.example'"
            " WHERE id = %s",
            (both,),
        )
        conn.commit()

        migrate_schema(conn)
        migrate_schema(conn)  # a no-op once the columns are gone

        cur.execute("SELECT contact_id, address, email_type FROM contact_email_addresses ORDER BY contact_id, id")
        assert [(r["contact_id"], r["address"], r["email_type"]) for r in cur.fetchall()] == [
            (imported, "ida@li.example", "linkedin"),
            (manual, "max@agency.example", "other"),
            (both, "bea@home.example", "home"),  # the same address in both columns is kept once
        ]
        cur.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'contacts' AND column_name IN ('email_address', 'personal_email')"
        )
        assert cur.fetchall() == []
    finally:
        conn.close()
