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

import pytest

from backend.db import (
    PostgresApplicationAnswerRepository,
    PostgresApplicationProfileRepository,
    get_connection,
)

# ai: claude-sonnet-5 | 2026-08-13


def test_profile_get_returns_none_before_first_upsert(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        repo = PostgresApplicationProfileRepository(conn)
        assert repo.get() is None
    finally:
        conn.close()


def test_profile_upsert_creates_then_updates_single_row(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        repo = PostgresApplicationProfileRepository(conn)

        created = repo.upsert(
            {
                "legal_name": "Evan Ross",
                "city": "Mebane, NC",
                "work_authorized": True,
                "requires_visa_sponsorship": False,
            }
        )
        conn.commit()
        assert created["legal_name"] == "Evan Ross"
        assert created["city"] == "Mebane, NC"
        assert created["work_authorized"] is True
        assert created["requires_visa_sponsorship"] is False

        updated = repo.upsert({"city": "Durham, NC"})
        conn.commit()
        assert updated["id"] == created["id"]
        assert updated["city"] == "Durham, NC"
        # Fields not touched by the second upsert are preserved, not cleared.
        assert updated["legal_name"] == "Evan Ross"

        assert repo.get()["city"] == "Durham, NC"
    finally:
        conn.close()


def test_profile_upsert_rejects_unknown_column(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        repo = PostgresApplicationProfileRepository(conn)
        with pytest.raises(ValueError, match="unknown columns"):
            repo.upsert({"not_a_real_column": "x"})
    finally:
        conn.close()


def test_answers_upsert_merges_on_normalized_question(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        repo = PostgresApplicationAnswerRepository(conn)

        first = repo.upsert(
            question_text="Do you have a security clearance?",
            answer_text="No, but willing to obtain one.",
            platform="Ashby",
        )
        conn.commit()

        # Re-asking the same question with different whitespace/casing (as it might
        # appear on a different posting) must upsert the same row, not create a
        # second one — this is the whole point of question_normalized.
        second = repo.upsert(
            question_text="  do you have a security clearance?  ",
            answer_text="Updated answer.",
            platform="Greenhouse",
        )
        conn.commit()

        assert second["id"] == first["id"]
        assert second["answer_text"] == "Updated answer."
        assert second["platform"] == "Greenhouse"

        all_answers = repo.get_all()
        assert len(all_answers) == 1
    finally:
        conn.close()


def test_answers_get_all_orders_most_recently_updated_first(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        repo = PostgresApplicationAnswerRepository(conn)
        repo.upsert(question_text="Question A?", answer_text="A")
        conn.commit()
        second = repo.upsert(question_text="Question B?", answer_text="B")
        conn.commit()

        all_answers = repo.get_all()
        assert all_answers[0]["id"] == second["id"]
    finally:
        conn.close()


def test_answers_delete_removes_row(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        repo = PostgresApplicationAnswerRepository(conn)
        answer = repo.upsert(question_text="Delete me?", answer_text="yes")
        conn.commit()

        repo.delete(answer["id"])
        conn.commit()

        assert repo.get_all() == []
    finally:
        conn.close()
