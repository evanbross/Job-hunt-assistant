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
from backend.db.repositories.application_answers import (
    AnswerNotFoundError,
    QuestionTakenError,
)
from backend.db.row_level_security import set_current_user
from web.extension_listener import (
    learn_application_answers,
    record_application_answer_use,
)

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


# ai: claude-opus-5-5 | 2026-11-17
# Usage counts, merging and aliases -- run against the real tables, since the
# upsert's alias routing and the merge are multi-statement SQL a fake can't check.


def _other_user_id(conn) -> int:
    account = PostgresUserAccountRepository(conn).create_pending(
        "second@example.com", "seed-second@example.com", "2099-01-01"
    )
    conn.commit()
    return account["id"]


def test_answers_start_unused_with_no_aliases(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationAnswerRepository(conn)
        saved = repo.upsert(user_id, question_text="Notice period?", answer_text="2 weeks")
        conn.commit()

        assert saved["use_count"] == 0
        assert saved["last_used_at"] is None
        assert saved["aliases"] == []
    finally:
        conn.close()


def test_record_answer_use_counts_each_answer_once_per_fill(pg_dsn: str) -> None:
    """Through the listener's callback: duplicates in one report count once,
    another user's id is ignored, and updated_at (the last edit) is left alone."""
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        other_user = _other_user_id(conn)
        repo = PostgresApplicationAnswerRepository(conn)
        set_current_user(conn.cursor(), other_user)
        theirs = repo.upsert(other_user, question_text="Theirs?", answer_text="B")
        conn.commit()
        set_current_user(conn.cursor(), user_id)
        first = repo.upsert(user_id, question_text="Notice period?", answer_text="2 weeks")
        second = repo.upsert(user_id, question_text="Relocate?", answer_text="No")
        conn.commit()

        ids = [first["id"], first["id"], second["id"], theirs["id"]]
        assert record_application_answer_use(user_id, ids) == 2
        assert record_application_answer_use(user_id, [first["id"]]) == 1

        notice = repo.get(user_id, first["id"])
        assert notice["use_count"] == 2
        assert notice["last_used_at"] is not None
        assert notice["updated_at"] == first["updated_at"]
        assert repo.get(user_id, second["id"])["use_count"] == 1
        set_current_user(conn.cursor(), other_user)
        assert repo.get(other_user, theirs["id"])["use_count"] == 0
    finally:
        conn.close()


def test_merge_moves_questions_and_aliases_and_adds_usage(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationAnswerRepository(conn)
        keep = repo.upsert(user_id, question_text="Do you need sponsorship?", answer_text="No")
        other = repo.upsert(user_id, question_text="Will you require a visa?", answer_text="no")
        third = repo.upsert(user_id, question_text="Need H-1B sponsorship?", answer_text="Nope")
        absorbed = repo.upsert(user_id, question_text="Sponsorship needed?", answer_text="No")
        repo.record_use(user_id, [keep["id"], other["id"], third["id"]])
        repo.record_use(user_id, [other["id"]])
        # third already carries an alias, which has to follow it into keep.
        repo.merge(user_id, third["id"], [absorbed["id"]])
        unrelated = repo.upsert(user_id, question_text="Relocate?", answer_text="No")
        conn.commit()

        merged = repo.merge(
            user_id, keep["id"], [other["id"], third["id"], keep["id"]], answer_text="No, not now or later."
        )
        conn.commit()

        assert merged["id"] == keep["id"]
        assert merged["question_text"] == "Do you need sponsorship?"
        assert merged["answer_text"] == "No, not now or later."
        assert merged["use_count"] == 4
        assert merged["last_used_at"] is not None
        assert sorted(a["question_text"] for a in merged["aliases"]) == [
            "Need H-1B sponsorship?",
            "Sponsorship needed?",
            "Will you require a visa?",
        ]
        assert {row["id"] for row in repo.get_all(user_id)} == {keep["id"], unrelated["id"]}
    finally:
        conn.close()


def test_merge_without_answer_text_keeps_the_kept_answer(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationAnswerRepository(conn)
        keep = repo.upsert(user_id, question_text="Q one?", answer_text="Kept")
        other = repo.upsert(user_id, question_text="Q two?", answer_text="Dropped")
        merged = repo.merge(user_id, keep["id"], [other["id"]])
        conn.commit()

        assert merged["answer_text"] == "Kept"
        assert merged["use_count"] == 0
        assert merged["last_used_at"] is None
    finally:
        conn.close()


def test_merge_rejects_another_users_answer_and_changes_nothing(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        other_user = _other_user_id(conn)
        repo = PostgresApplicationAnswerRepository(conn)
        set_current_user(conn.cursor(), other_user)
        theirs = repo.upsert(other_user, question_text="Theirs?", answer_text="B")
        conn.commit()
        set_current_user(conn.cursor(), user_id)
        mine = repo.upsert(user_id, question_text="Mine?", answer_text="A")
        conn.commit()

        with pytest.raises(AnswerNotFoundError):
            repo.merge(user_id, mine["id"], [theirs["id"]])
        conn.rollback()

        assert repo.get(user_id, mine["id"])["aliases"] == []
        set_current_user(conn.cursor(), other_user)
        assert [row["id"] for row in repo.get_all(other_user)] == [theirs["id"]]
    finally:
        conn.close()


def test_upsert_of_an_alias_wording_updates_the_merged_answer(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationAnswerRepository(conn)
        keep = repo.upsert(user_id, question_text="Do you need sponsorship?", answer_text="No")
        other = repo.upsert(user_id, question_text="Will you require a visa?", answer_text="No")
        repo.merge(user_id, keep["id"], [other["id"]])
        conn.commit()

        # The extension saves a correction under the alias wording.
        saved = repo.upsert(
            user_id, question_text="  will you require a VISA?", answer_text="Yes", platform="Lever"
        )
        conn.commit()

        assert saved["id"] == keep["id"]
        assert saved["question_text"] == "Do you need sponsorship?"
        assert saved["answer_text"] == "Yes"
        assert saved["platform"] == "Lever"
        assert len(repo.get_all(user_id)) == 1
    finally:
        conn.close()


def test_update_edits_in_place_and_refuses_another_answers_wording(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationAnswerRepository(conn)
        target = repo.upsert(user_id, question_text="Clearance?", answer_text="None")
        keep = repo.upsert(user_id, question_text="Do you need sponsorship?", answer_text="No")
        other = repo.upsert(user_id, question_text="Will you require a visa?", answer_text="No")
        repo.merge(user_id, keep["id"], [other["id"]])
        conn.commit()

        edited = repo.update(
            user_id, target["id"], question_text="Security clearance?", answer_text="Secret", platform=None
        )
        conn.commit()
        assert edited["id"] == target["id"]
        assert edited["question_normalized"] == "security clearance?"
        assert edited["answer_text"] == "Secret"
        assert len(repo.get_all(user_id)) == 2

        for taken in ("do you need sponsorship?", "Will you require a visa?"):
            with pytest.raises(QuestionTakenError):
                repo.update(user_id, target["id"], question_text=taken, answer_text="x", platform=None)
            conn.rollback()

        with pytest.raises(AnswerNotFoundError):
            repo.update(user_id, 999_999, question_text="New?", answer_text="x", platform=None)
        conn.rollback()
    finally:
        conn.close()


def test_update_to_its_own_alias_wording_promotes_it(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationAnswerRepository(conn)
        keep = repo.upsert(user_id, question_text="Sponsorship?", answer_text="No")
        other = repo.upsert(user_id, question_text="Will you require a visa?", answer_text="No")
        repo.merge(user_id, keep["id"], [other["id"]])
        conn.commit()

        edited = repo.update(
            user_id, keep["id"], question_text="Will you require a visa?", answer_text="No", platform=None
        )
        conn.commit()

        assert edited["question_text"] == "Will you require a visa?"
        assert edited["aliases"] == []
    finally:
        conn.close()


def test_delete_alias_stops_matching_that_wording(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationAnswerRepository(conn)
        keep = repo.upsert(user_id, question_text="Sponsorship?", answer_text="No")
        other = repo.upsert(user_id, question_text="Visa?", answer_text="No")
        [alias] = repo.merge(user_id, keep["id"], [other["id"]])["aliases"]
        conn.commit()

        repo.delete_alias(user_id, alias["id"])
        conn.commit()
        assert repo.get(user_id, keep["id"])["aliases"] == []

        with pytest.raises(AnswerNotFoundError):
            repo.delete_alias(user_id, alias["id"])
        conn.rollback()

        # The freed wording starts its own answer again.
        fresh = repo.upsert(user_id, question_text="Visa?", answer_text="Maybe")
        conn.commit()
        assert fresh["id"] != keep["id"]
    finally:
        conn.close()


def test_deleting_an_answer_deletes_its_aliases(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _make_user_id(conn)
        repo = PostgresApplicationAnswerRepository(conn)
        keep = repo.upsert(user_id, question_text="Sponsorship?", answer_text="No")
        other = repo.upsert(user_id, question_text="Visa?", answer_text="No")
        repo.merge(user_id, keep["id"], [other["id"]])
        repo.delete(user_id, keep["id"])
        conn.commit()

        fresh = repo.upsert(user_id, question_text="Visa?", answer_text="Yes")
        conn.commit()
        assert fresh["aliases"] == []
        assert len(repo.get_all(user_id)) == 1
    finally:
        conn.close()
