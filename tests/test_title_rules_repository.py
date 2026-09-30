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

from backend.db import (
    PostgresContactRepository,
    PostgresTitleRuleRepository,
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
        "email_address": None,
    }
    row.update(overrides)
    row["dedup_key"] = row["profile_url"] or f"{row['full_name'].lower()}|{row['company_normalized']}"
    return row


def _new_user(conn: object, email: str = "owner@example.com") -> int:
    account = PostgresUserAccountRepository(conn).create_pending(email, f"seed-{email}", "2099-01-01")
    set_current_user(conn.cursor(), account["id"])
    return account["id"]


def test_titles_report_groups_by_normalized_title_and_counts_flagged(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        contacts = PostgresContactRepository(conn)
        titles = PostgresTitleRuleRepository(conn)
        contacts.sync_import(
            user_id,
            [
                _import_row(full_name="A", position="Technical Recruiter", normalized_title="technical recruiter"),
                _import_row(full_name="B", position="technical  recruiter", normalized_title="technical recruiter"),
            ],
        )
        a_id = next(c["id"] for c in contacts.get_all(user_id) if c["full_name"] == "A")
        assert contacts.update(user_id, a_id, {"recruiter_type": "unrated", "decided_by": "manual"})
        conn.commit()

        report = titles.titles_report(user_id)

        assert len(report) == 1
        entry = report[0]
        assert entry["normalized_title"] == "technical recruiter"
        assert entry["title"] == "Technical Recruiter"
        assert entry["contact_count"] == 2
        assert entry["flagged_count"] == 1
        assert entry["unflagged_count"] == 1
        assert entry["rule_exists"] is False
        assert entry["rule_recruiter_type"] is None
    finally:
        conn.close()


def test_titles_report_groups_blank_titles_as_no_title(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        contacts = PostgresContactRepository(conn)
        titles = PostgresTitleRuleRepository(conn)
        contacts.sync_import(user_id, [_import_row(position="", normalized_title="")])
        conn.commit()

        report = titles.titles_report(user_id)

        assert report[0]["normalized_title"] == ""
        assert report[0]["title"] == "(no title)"
    finally:
        conn.close()


def test_set_rule_applies_only_to_undecided_contacts(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        contacts = PostgresContactRepository(conn)
        titles = PostgresTitleRuleRepository(conn)
        contacts.sync_import(
            user_id,
            [
                _import_row(full_name="Decided", profile_url="https://linkedin.com/in/decided"),
                _import_row(full_name="Undecided", profile_url="https://linkedin.com/in/undecided"),
            ],
        )
        decided_id = next(c["id"] for c in contacts.get_all(user_id) if c["full_name"] == "Decided")
        # ai: claude-sonnet-5 | 2026-09-30 -- was: recruiter_type "high" (renamed to "high_reliability")
        assert contacts.update(
            user_id, decided_id, {"recruiter_type": "high_reliability", "decided_by": "manual"}
        )
        conn.commit()

        decided_count = titles.set_rule(user_id, "recruiter", "unrated")
        conn.commit()

        assert decided_count == 1  # only the previously-undecided contact
        by_name = {c["full_name"]: c for c in contacts.list_with_emails(user_id)}
        assert by_name["Decided"]["recruiter_type"] == "high_reliability"
        assert by_name["Decided"]["decided_by"] == "manual"
        assert by_name["Undecided"]["recruiter_type"] == "unrated"
        assert by_name["Undecided"]["decided_by"] == "title_rule"

        report = {r["normalized_title"]: r for r in titles.titles_report(user_id)}
        assert report["recruiter"]["rule_exists"] is True
        assert report["recruiter"]["rule_recruiter_type"] == "unrated"
    finally:
        conn.close()


def test_set_rule_with_none_dismisses_the_title(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        contacts = PostgresContactRepository(conn)
        titles = PostgresTitleRuleRepository(conn)
        contacts.sync_import(user_id, [_import_row(position="Talent Manager", normalized_title="talent manager")])
        conn.commit()

        titles.set_rule(user_id, "talent manager", None)
        conn.commit()

        contact = contacts.list_with_emails(user_id)[0]
        assert contact["recruiter_type"] is None
        assert contact["decided_by"] == "title_rule"
        report = titles.titles_report(user_id)[0]
        assert report["unflagged_count"] == 0, "a dismissed title must no longer count as unflagged"
        assert report["flagged_count"] == 1
    finally:
        conn.close()


def test_undo_rule_resets_only_the_contacts_it_decided(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        contacts = PostgresContactRepository(conn)
        titles = PostgresTitleRuleRepository(conn)
        contacts.sync_import(
            user_id,
            [
                _import_row(full_name="ByRule", profile_url="https://linkedin.com/in/byrule"),
                _import_row(full_name="Manual", profile_url="https://linkedin.com/in/manual"),
            ],
        )
        manual_id = next(c["id"] for c in contacts.get_all(user_id) if c["full_name"] == "Manual")
        # ai: claude-sonnet-5 | 2026-09-30 -- was: recruiter_type "moderate" (renamed to "low_reliability")
        assert contacts.update(
            user_id, manual_id, {"recruiter_type": "low_reliability", "decided_by": "manual"}
        )
        conn.commit()
        titles.set_rule(user_id, "recruiter", "unrated")
        conn.commit()

        reset_count = titles.undo_rule(user_id, "recruiter")
        conn.commit()

        assert reset_count == 1
        by_name = {c["full_name"]: c for c in contacts.list_with_emails(user_id)}
        assert by_name["ByRule"]["recruiter_type"] is None
        assert by_name["ByRule"]["decided_by"] is None
        assert by_name["Manual"]["recruiter_type"] == "low_reliability", "manual decision must survive an undo"
        assert by_name["Manual"]["decided_by"] == "manual"
        assert titles.titles_report(user_id)[0]["rule_exists"] is False
    finally:
        conn.close()


def test_apply_all_rules_decides_new_undecided_contacts_matching_existing_rules(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        contacts = PostgresContactRepository(conn)
        titles = PostgresTitleRuleRepository(conn)
        contacts.sync_import(user_id, [_import_row(profile_url="https://linkedin.com/in/first")])
        conn.commit()
        # ai: claude-sonnet-5 | 2026-09-30 -- was: recruiter_type "high" (renamed to "high_reliability")
        titles.set_rule(user_id, "recruiter", "high_reliability")
        conn.commit()

        # A second contact with the same title arrives later (e.g. a re-import).
        contacts.sync_import(user_id, [_import_row(profile_url="https://linkedin.com/in/second")])
        conn.commit()

        applied = titles.apply_all_rules(user_id)
        conn.commit()

        assert applied == 1
        second = next(
            c for c in contacts.list_with_emails(user_id) if c["profile_url"].endswith("second")
        )
        assert second["recruiter_type"] == "high_reliability"
        assert second["decided_by"] == "title_rule"
    finally:
        conn.close()


def test_title_rules_are_scoped_to_the_owning_user(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        owner_id = _new_user(conn)
        other_id = _new_user(conn, "other@example.com")
        contacts = PostgresContactRepository(conn)
        titles = PostgresTitleRuleRepository(conn)

        set_current_user(conn.cursor(), owner_id)
        contacts.sync_import(owner_id, [_import_row()])
        conn.commit()
        # ai: claude-sonnet-5 | 2026-09-30 -- was: recruiter_type "high" (renamed to "high_reliability")
        titles.set_rule(owner_id, "recruiter", "high_reliability")
        conn.commit()

        set_current_user(conn.cursor(), other_id)
        contacts.sync_import(other_id, [_import_row()])
        conn.commit()

        other_contact = contacts.list_with_emails(other_id)[0]
        assert other_contact["recruiter_type"] is None, (
            "other account's matching-title contact must not be decided by the owner's rule"
        )
        assert titles.titles_report(other_id)[0]["rule_exists"] is False

        set_current_user(conn.cursor(), owner_id)
        assert titles.titles_report(owner_id)[0]["rule_exists"] is True
    finally:
        conn.close()
