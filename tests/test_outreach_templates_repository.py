# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""PostgresOutreachTemplateRepository against a real database, plus the
one-time copy of the retired linkedin_outreach_template setting."""

from __future__ import annotations

import pytest

from backend.db import (
    DuplicateOutreachTemplateTitleError,
    PostgresOutreachTemplateRepository,
    PostgresUserAccountRepository,
    PostgresUserSettingsRepository,
    get_connection,
    init_db,
)
from backend.db.row_level_security import set_current_user

_NOW = "2026-10-21T12:00:00+00:00"
_LATER = "2026-10-21T13:00:00+00:00"


# ai: claude-opus-5-5 | 2026-10-21
def _new_user(conn: object, email: str = "owner@example.com") -> int:
    account = PostgresUserAccountRepository(conn).create_pending(email, f"seed-{email}", "2099-01-01")
    set_current_user(conn.cursor(), account["id"])
    return account["id"]


# ai: claude-opus-5-5 | 2026-10-21
def test_insert_list_update_delete(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresOutreachTemplateRepository(conn)

        recruiters = repo.insert(user_id, "Recruiters", "Hi {first_name}", _NOW)
        repo.insert(user_id, "close coworkers", "Hey {first_name}", _NOW)
        conn.commit()
        assert [t["title"] for t in repo.list_for_user(user_id)] == ["close coworkers", "Recruiters"]
        assert repo.get_by_id(user_id, recruiters["id"]) == recruiters

        updated = repo.update(user_id, recruiters["id"], "Good recruiters", "Hello", _LATER)
        assert updated is not None
        assert (updated["title"], updated["body"]) == ("Good recruiters", "Hello")
        assert (updated["created_at"], updated["updated_at"]) == (_NOW, _LATER)

        assert repo.delete(user_id, recruiters["id"]) is True
        assert repo.delete(user_id, recruiters["id"]) is False
        assert repo.update(user_id, recruiters["id"], "x", "y", _LATER) is None
        assert [t["title"] for t in repo.list_for_user(user_id)] == ["close coworkers"]
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-10-21
# A title clash must raise the domain error without poisoning the caller's
# transaction: the insert uses ON CONFLICT and the update a savepoint, so
# earlier uncommitted writes in the same transaction still commit.
def test_duplicate_title_raises_and_keeps_the_transaction_usable(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresOutreachTemplateRepository(conn)
        repo.insert(user_id, "Recruiters", "a", _NOW)
        coworkers = repo.insert(user_id, "Coworkers", "b", _NOW)

        with pytest.raises(DuplicateOutreachTemplateTitleError):
            repo.insert(user_id, "Recruiters", "c", _NOW)
        with pytest.raises(DuplicateOutreachTemplateTitleError):
            repo.update(user_id, coworkers["id"], "Recruiters", "b", _LATER)
        conn.commit()

        assert [(t["title"], t["body"]) for t in repo.list_for_user(user_id)] == [
            ("Coworkers", "b"),
            ("Recruiters", "a"),
        ]
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-10-21
def test_templates_are_scoped_to_their_owner(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        owner_id = _new_user(conn)
        other_id = _new_user(conn, "other@example.com")
        repo = PostgresOutreachTemplateRepository(conn)

        set_current_user(conn.cursor(), owner_id)
        template = repo.insert(owner_id, "Recruiters", "Hi", _NOW)
        conn.commit()

        set_current_user(conn.cursor(), other_id)
        assert repo.list_for_user(other_id) == []
        assert repo.get_by_id(other_id, template["id"]) is None
        assert repo.update(other_id, template["id"], "Mine now", "x", _LATER) is None
        assert repo.delete(other_id, template["id"]) is False
        # Same title is fine for a different account.
        repo.insert(other_id, "Recruiters", "Hello", _NOW)
        conn.commit()

        set_current_user(conn.cursor(), owner_id)
        assert repo.get_by_id(owner_id, template["id"]) == template
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-10-21
def test_migration_copies_the_legacy_single_template_setting(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        PostgresUserSettingsRepository(conn).set(user_id, "linkedin_outreach_template", "Hi {first_name}!")
        conn.commit()

        init_db(pg_dsn)
        init_db(pg_dsn)  # idempotent: a second startup must not duplicate or fail

        set_current_user(conn.cursor(), user_id)
        templates = PostgresOutreachTemplateRepository(conn).list_for_user(user_id)
        assert [(t["title"], t["body"]) for t in templates] == [("General", "Hi {first_name}!")]
        assert PostgresUserSettingsRepository(conn).get(user_id, "linkedin_outreach_template") is None
    finally:
        conn.close()
