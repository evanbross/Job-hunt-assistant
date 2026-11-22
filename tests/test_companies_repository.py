# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.db import (
    PostgresCompanyRepository,
    PostgresContactRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user

# ai: claude-sonnet-5 | 2026-09-28


def _import_row(**overrides: object) -> dict[str, object]:
    row = {
        "full_name": "Jane Doe",
        "company_raw": "Acme Inc",
        "company_normalized": "acme",
        "position": "Recruiter",
        "normalized_title": "recruiter",
        "profile_url": None,
        "connected_on": "2024-01-15",
        "imported_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    row.update(overrides)
    row["dedup_key"] = row["profile_url"] or f"{row['full_name'].lower()}|{row['company_normalized']}"
    return row


def _new_user(conn: object, email: str = "owner@example.com") -> int:
    account = PostgresUserAccountRepository(conn).create_pending(email, f"seed-{email}", "2099-01-01")
    set_current_user(conn.cursor(), account["id"])
    return account["id"]


def test_report_only_includes_companies_with_a_recruiter_contact(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        contacts = PostgresContactRepository(conn)
        companies = PostgresCompanyRepository(conn)
        contacts.sync_import(
            user_id,
            [
                _import_row(full_name="Recruiter Rita", company_raw="Acme Inc", company_normalized="acme"),
                _import_row(full_name="Plain Pat", company_raw="Beta LLC", company_normalized="beta"),
            ],
        )
        rita_id = next(c["id"] for c in contacts.get_all(user_id) if c["full_name"] == "Recruiter Rita")
        assert contacts.update(user_id, rita_id, {"recruiter_type": "unrated"})
        conn.commit()

        report = companies.report(user_id)

        assert [c["company_normalized"] for c in report] == ["acme"]
        assert report[0]["company_raw"] == "Acme Inc"
        assert report[0]["contact_count"] == 1
        assert report[0]["recruiter_count"] == 1
        assert report[0]["is_agency"] is False
        assert report[0]["workforce"] == "unknown"
        assert report[0]["notes"] is None
    finally:
        conn.close()


def test_report_sorts_by_recruiter_count_then_contact_count(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        contacts = PostgresContactRepository(conn)
        companies = PostgresCompanyRepository(conn)
        contacts.sync_import(
            user_id,
            [
                _import_row(full_name="A1", company_raw="Small Co", company_normalized="small"),
                _import_row(full_name="B1", company_raw="Big Co", company_normalized="big"),
                _import_row(full_name="B2", company_raw="Big Co", company_normalized="big"),
            ],
        )
        by_name = {c["full_name"]: c["id"] for c in contacts.get_all(user_id)}
        for name in ("A1", "B1", "B2"):
            assert contacts.update(user_id, by_name[name], {"recruiter_type": "unrated"})
        conn.commit()

        report = companies.report(user_id)

        assert [c["company_normalized"] for c in report] == ["big", "small"]
        assert report[0]["recruiter_count"] == 2
    finally:
        conn.close()


def test_upsert_sets_attributes_reflected_in_report(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        contacts = PostgresContactRepository(conn)
        companies = PostgresCompanyRepository(conn)
        contacts.sync_import(user_id, [_import_row()])
        contact_id = contacts.get_all(user_id)[0]["id"]
        assert contacts.update(user_id, contact_id, {"recruiter_type": "unrated"})
        conn.commit()

        companies.upsert(
            user_id, "acme", {"is_agency": True, "workforce": "offshore_hybrid", "notes": "big shop"}
        )
        conn.commit()

        report = companies.report(user_id)
        assert report[0]["is_agency"] is True
        assert report[0]["workforce"] == "offshore_hybrid"
        assert report[0]["notes"] == "big shop"

        # Second upsert overwrites in place rather than erroring or duplicating.
        companies.upsert(user_id, "acme", {"is_agency": False})
        conn.commit()
        report_after = companies.report(user_id)
        assert report_after[0]["is_agency"] is False
        assert report_after[0]["workforce"] == "offshore_hybrid", "unset fields must be left alone"
    finally:
        conn.close()


def test_upsert_rejects_unknown_columns(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        companies = PostgresCompanyRepository(conn)
        with pytest.raises(ValueError, match="unknown columns"):
            companies.upsert(user_id, "acme", {"company_normalized": "hacked"})
    finally:
        conn.close()


def test_companies_are_scoped_to_the_owning_user(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        owner_id = _new_user(conn)
        other_id = _new_user(conn, "other@example.com")
        contacts = PostgresContactRepository(conn)
        companies = PostgresCompanyRepository(conn)

        set_current_user(conn.cursor(), owner_id)
        contacts.sync_import(owner_id, [_import_row(company_raw="Acme Inc", company_normalized="acme")])
        owner_contact_id = contacts.get_all(owner_id)[0]["id"]
        assert contacts.update(owner_id, owner_contact_id, {"recruiter_type": "unrated"})
        companies.upsert(owner_id, "acme", {"is_agency": True})
        conn.commit()

        set_current_user(conn.cursor(), other_id)
        contacts.sync_import(other_id, [_import_row(company_raw="Acme Inc", company_normalized="acme")])
        other_contact_id = contacts.get_all(other_id)[0]["id"]
        assert contacts.update(other_id, other_contact_id, {"recruiter_type": "unrated"})
        conn.commit()

        other_report = companies.report(other_id)
        assert other_report[0]["is_agency"] is False, (
            "other account must not see the owner's is_agency flag for the same company name"
        )

        set_current_user(conn.cursor(), owner_id)
        owner_report = companies.report(owner_id)
        assert owner_report[0]["is_agency"] is True
    finally:
        conn.close()
