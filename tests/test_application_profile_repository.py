# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

import pytest

from backend.db import (
    PostgresApplicationAnswerRepository,
    PostgresApplicationProfileRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user
from web.extension_listener import learn_application_answers

# ai: claude-sonnet-5 | 2026-08-13
# ai: claude-sonnet-5 | 2026-08-20 — every repo call below now takes a
# leading user_id (multi-tenancy migration); each test creates its own
# account rather than relying on a fixed id.


def _make_user_id(conn) -> int:
    account = PostgresUserAccountRepository(conn).create_pending("owner@example.com", "seed-owner@example.com", "2099-01-01")
    conn.commit()
    set_current_user(conn.cursor(), account["id"])
    return account["id"]


def test_profile_get_returns_none_before_first_upsert(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationProfileRepository(conn)
        assert repo.get(user_id) is None
    finally:
        conn.close()


def test_profile_upsert_creates_then_updates_single_row(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationProfileRepository(conn)

        created = repo.upsert(
            user_id,
            {
                "legal_name": "Evan Ross",
                "city": "Mebane, NC",
                "work_authorized": True,
                "requires_visa_sponsorship": False,
            },
        )
        conn.commit()
        assert created["legal_name"] == "Evan Ross"
        assert created["city"] == "Mebane, NC"
        assert created["work_authorized"] is True
        assert created["requires_visa_sponsorship"] is False

        updated = repo.upsert(user_id, {"city": "Durham, NC"})
        conn.commit()
        assert updated["id"] == created["id"]
        assert updated["city"] == "Durham, NC"
        # Fields not touched by the second upsert are preserved, not cleared.
        assert updated["legal_name"] == "Evan Ross"

        assert repo.get(user_id)["city"] == "Durham, NC"
    finally:
        conn.close()


# ai: claude-sonnet-5 | 2026-10-14
def test_profile_upsert_stores_previous_employers_against_real_schema(pg_dsn: str) -> None:
    """previous_employers is a new column (backend/db/schema.py's migration) --
    execute the insert/read against the real schema per TESTING_STANDARDS.md."""
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationProfileRepository(conn)

        created = repo.upsert(user_id, {"previous_employers": '["Acme", "Widget Co"]'})
        conn.commit()
        assert created["previous_employers"] == '["Acme", "Widget Co"]'
        assert repo.get(user_id)["previous_employers"] == '["Acme", "Widget Co"]'
    finally:
        conn.close()


def test_profile_upsert_rejects_unknown_column(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationProfileRepository(conn)
        with pytest.raises(ValueError, match="unknown columns"):
            repo.upsert(user_id, {"not_a_real_column": "x"})
    finally:
        conn.close()


def test_profile_upsert_is_scoped_per_user(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_a = _make_user_id(conn)
        account_b = PostgresUserAccountRepository(conn).create_pending("second@example.com", "seed-second@example.com", "2099-01-01")
        conn.commit()
        user_b = account_b["id"]

        repo = PostgresApplicationProfileRepository(conn)
        set_current_user(conn.cursor(), user_a)
        repo.upsert(user_a, {"legal_name": "User A"})
        set_current_user(conn.cursor(), user_b)
        repo.upsert(user_b, {"legal_name": "User B"})
        conn.commit()

        set_current_user(conn.cursor(), user_a)
        assert repo.get(user_a)["legal_name"] == "User A"
        set_current_user(conn.cursor(), user_b)
        assert repo.get(user_b)["legal_name"] == "User B"
    finally:
        conn.close()


def test_answers_upsert_merges_on_normalized_question(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationAnswerRepository(conn)

        first = repo.upsert(
            user_id,
            question_text="Do you have a security clearance?",
            answer_text="No, but willing to obtain one.",
            platform="Ashby",
        )
        conn.commit()

        # Re-asking the same question with different whitespace/casing (as it might
        # appear on a different posting) must upsert the same row, not create a
        # second one — this is the whole point of question_normalized.
        second = repo.upsert(
            user_id,
            question_text="  do you have a security clearance?  ",
            answer_text="Updated answer.",
            platform="Greenhouse",
        )
        conn.commit()

        assert second["id"] == first["id"]
        assert second["answer_text"] == "Updated answer."
        assert second["platform"] == "Greenhouse"

        all_answers = repo.get_all(user_id)
        assert len(all_answers) == 1
    finally:
        conn.close()


def test_answers_get_all_orders_most_recently_updated_first(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationAnswerRepository(conn)
        repo.upsert(user_id, question_text="Question A?", answer_text="A")
        conn.commit()
        second = repo.upsert(user_id, question_text="Question B?", answer_text="B")
        conn.commit()

        all_answers = repo.get_all(user_id)
        assert all_answers[0]["id"] == second["id"]
    finally:
        conn.close()


def test_answers_delete_removes_row(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationAnswerRepository(conn)
        answer = repo.upsert(user_id, question_text="Delete me?", answer_text="yes")
        conn.commit()

        repo.delete(user_id, answer["id"])
        conn.commit()

        assert repo.get_all(user_id) == []
    finally:
        conn.close()


def test_answers_upsert_is_scoped_per_user(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_a = _make_user_id(conn)
        account_b = PostgresUserAccountRepository(conn).create_pending("second@example.com", "seed-second@example.com", "2099-01-01")
        conn.commit()
        user_b = account_b["id"]

        repo = PostgresApplicationAnswerRepository(conn)
        # Same normalized question text for two different users must not collide.
        set_current_user(conn.cursor(), user_a)
        repo.upsert(user_a, question_text="Do you need sponsorship?", answer_text="No")
        set_current_user(conn.cursor(), user_b)
        repo.upsert(user_b, question_text="Do you need sponsorship?", answer_text="Yes")
        conn.commit()

        set_current_user(conn.cursor(), user_a)
        assert len(repo.get_all(user_a)) == 1
        assert repo.get_all(user_a)[0]["answer_text"] == "No"
        set_current_user(conn.cursor(), user_b)
        assert len(repo.get_all(user_b)) == 1
        assert repo.get_all(user_b)[0]["answer_text"] == "Yes"
    finally:
        conn.close()


# ai: claude-opus-5-5 | 2026-11-17
def test_learn_application_answers_saves_the_batch_through_the_real_schema(pg_dsn: str) -> None:
    """The extension listener's batch save (one transaction, all rows) against the
    real tables, including an upsert over an existing normalized question."""
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationAnswerRepository(conn)
        repo.upsert(user_id, question_text="What is your notice period?", answer_text="4 weeks")
        conn.commit()

        saved = learn_application_answers(
            user_id,
            [("What is your notice period?", "2 weeks"), ("Willing to relocate?", "No")],
            "Greenhouse",
        )

        assert [row["answer_text"] for row in saved] == ["2 weeks", "No"]
        rows = {row["question_text"]: row for row in repo.get_all(user_id)}
        assert set(rows) == {"What is your notice period?", "Willing to relocate?"}
        assert rows["What is your notice period?"]["answer_text"] == "2 weeks"
        assert rows["Willing to relocate?"]["platform"] == "Greenhouse"
    finally:
        conn.close()
