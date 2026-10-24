# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from typing import Any

import pytest
from flask import Flask

from backend.ai_provider import AIProviderError
from backend.db import DuplicateOutreachTemplateTitleError
from backend.db.interfaces import ContactSyncResult
from routes.contacts import contacts_bp

# ai: claude-sonnet-5 | 2026-07-25


def make_fake_get_repos(repos: dict) -> Any:
    @contextmanager
    def fake() -> Iterator[dict[str, Any]]:
        try:
            yield repos
        finally:
            repos["conn"].close()

    return fake


class FakeConnection:
    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


_TEST_USER_ID = 1


class FakeContactsRepo:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.updates: list[tuple[int, dict[str, Any]]] = []
        self.bulk_calls: list[tuple[list[int], str | None]] = []
        self.staged: list[tuple[int, str, str]] = []
        self.cleared: list[int] = []

    def get_all(self, user_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]

    def sync_import(self, user_id: int, rows: list[dict[str, Any]]) -> ContactSyncResult:
        self.rows = rows
        return ContactSyncResult(added=len(rows), updated=0, removed=0)

    def list_with_outreach(self, user_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]

    # ai: claude-opus-5-5 | 2026-10-14
    def get_by_id(self, user_id: int, contact_id: int) -> dict[str, Any] | None:
        return next((dict(row) for row in self.rows if row.get("id") == contact_id), None)

    def stage_outreach_draft(
        self, user_id: int, contact_id: int, draft: str, staged_at: str
    ) -> bool:
        if not any(row.get("id") == contact_id for row in self.rows):
            return False
        self.staged.append((contact_id, draft, staged_at))
        return True

    def clear_outreach_draft(self, user_id: int, contact_id: int) -> None:
        self.cleared.append(contact_id)

    def update(self, user_id: int, contact_id: int, fields: dict[str, Any]) -> bool:
        self.updates.append((contact_id, fields))
        return any(row.get("id") == contact_id for row in self.rows)

    def bulk_set_recruiter(
        self, user_id: int, contact_ids: list[int], recruiter_type: str | None
    ) -> int:
        self.bulk_calls.append((contact_ids, recruiter_type))
        return len([row for row in self.rows if row.get("id") in contact_ids])

    def count(self, user_id: int) -> int:
        return len(self.rows)


# ai: gpt-5 | 2026-10-02
class FakeJobsRepo:
    def get_outreach_company_relevance(
        self, user_id: int, active_statuses: frozenset[str], terminal_statuses: set[str]
    ) -> dict[str, float]:
        return {}


# ai: gpt-5 | 2026-10-02
class FakeStageStatusesRepo:
    def get_all(self) -> list[dict[str, object]]:
        return [{"key": "rejected", "is_terminal": True}]


# ai: claude-opus-5-5 | 2026-10-14 -- was FakeContactEmailsRepo
class FakeContactOutreachRepo:
    def __init__(
        self, contact_ids: set[int] | None = None, outreach_ids: set[int] | None = None
    ) -> None:
        self.contact_ids = contact_ids or set()
        self.outreach_ids = outreach_ids or set()
        self.added: list[tuple[int, str, str]] = []
        self.responses: dict[int, str | None] = {}
        self.deleted: list[int] = []

    def add(
        self, user_id: int, contact_id: int, sent_at: str, channel: str
    ) -> dict[str, Any] | None:
        if contact_id not in self.contact_ids:
            return None
        self.added.append((contact_id, sent_at, channel))
        return {"id": 99, "sent_at": sent_at, "response_received_at": None, "channel": channel}

    def set_response(
        self, user_id: int, outreach_id: int, response_received_at: str | None
    ) -> bool:
        if outreach_id not in self.outreach_ids:
            return False
        self.responses[outreach_id] = response_received_at
        return True

    def delete(self, user_id: int, outreach_id: int) -> bool:
        if outreach_id not in self.outreach_ids:
            return False
        self.deleted.append(outreach_id)
        return True


# ai: claude-sonnet-5 | 2026-10-15
class FakeContactEmploymentHistoryRepo:
    def __init__(
        self, contact_ids: set[int] | None = None, entry_ids: set[int] | None = None
    ) -> None:
        self.contact_ids = contact_ids or set()
        self.entry_ids = entry_ids or set()
        self.rows: dict[int, dict[str, Any]] = {}
        self.updates: list[tuple[int, dict[str, Any]]] = []
        self.deleted: list[int] = []

    def list_for_contact(self, user_id: int, contact_id: int) -> list[dict[str, Any]]:
        return [row for row in self.rows.values() if row.get("contact_id") == contact_id]

    def add(
        self,
        user_id: int,
        contact_id: int,
        *,
        company: str,
        title: str,
        start_date: str | None,
        end_date: str | None,
        source: str,
        captured_at: str,
    ) -> dict[str, Any] | None:
        if contact_id not in self.contact_ids:
            return None
        row = {
            "id": 99,
            "contact_id": contact_id,
            "company": company,
            "title": title,
            "start_date": start_date,
            "end_date": end_date,
            "source": source,
            "captured_at": captured_at,
        }
        self.rows[99] = row
        return row

    def update(self, user_id: int, entry_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
        if entry_id not in self.entry_ids:
            return None
        self.updates.append((entry_id, fields))
        row = self.rows.setdefault(entry_id, {"id": entry_id})
        row.update(fields)
        return row

    def delete(self, user_id: int, entry_id: int) -> bool:
        if entry_id not in self.entry_ids:
            return False
        self.deleted.append(entry_id)
        return True


class FakeSettingsRepo:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, user_id: int, key: str, default: str | None = None) -> str | None:
        return self.values.get(key, default)

    def set(self, user_id: int, key: str, value: str) -> None:
        self.values[key] = value


# ai: claude-opus-5-5 | 2026-10-21
class FakeOutreachTemplatesRepo:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    def list_for_user(self, user_id: int) -> list[dict[str, Any]]:
        return sorted((dict(row) for row in self.rows), key=lambda row: row["title"].lower())

    def get_by_id(self, user_id: int, template_id: int) -> dict[str, Any] | None:
        return next((dict(row) for row in self.rows if row["id"] == template_id), None)

    def insert(self, user_id: int, title: str, body: str, now: str) -> dict[str, Any]:
        if any(row["title"] == title for row in self.rows):
            raise DuplicateOutreachTemplateTitleError(title)
        row = {"id": len(self.rows) + 1, "title": title, "body": body, "created_at": now, "updated_at": now}
        self.rows.append(row)
        return dict(row)

    def update(
        self, user_id: int, template_id: int, title: str, body: str, now: str
    ) -> dict[str, Any] | None:
        if any(row["title"] == title and row["id"] != template_id for row in self.rows):
            raise DuplicateOutreachTemplateTitleError(title)
        for row in self.rows:
            if row["id"] == template_id:
                row.update(title=title, body=body, updated_at=now)
                return dict(row)
        return None

    def delete(self, user_id: int, template_id: int) -> bool:
        before = len(self.rows)
        self.rows = [row for row in self.rows if row["id"] != template_id]
        return len(self.rows) < before


# ai: claude-sonnet-5 | 2026-09-28
class FakeTitleRuleRepo:
    def __init__(self, report: list[dict[str, Any]] | None = None) -> None:
        self.applied_for_user_ids: list[int] = []
        self.report_rows = report or []
        self.set_calls: list[tuple[int, str, str | None]] = []
        self.undo_calls: list[tuple[int, str]] = []

    def apply_all_rules(self, user_id: int) -> int:
        self.applied_for_user_ids.append(user_id)
        return 0

    def titles_report(self, user_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.report_rows]

    def set_rule(self, user_id: int, normalized_title: str, recruiter_type: str | None) -> int:
        self.set_calls.append((user_id, normalized_title, recruiter_type))
        return 0

    def undo_rule(self, user_id: int, normalized_title: str) -> int:
        self.undo_calls.append((user_id, normalized_title))
        return 0


def _make_app(repos: dict, monkeypatch: pytest.MonkeyPatch) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(contacts_bp, url_prefix="/api")
    monkeypatch.setattr("routes.contacts.get_repos", make_fake_get_repos(repos))
    monkeypatch.setattr("routes.contacts.current_user_id", lambda: _TEST_USER_ID)
    return app


def test_import_contacts_missing_file_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "contacts": FakeContactsRepo(), "settings": FakeSettingsRepo(), "title_rules": FakeTitleRuleRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.post("/api/contacts/import")
    assert response.status_code == 400


def test_import_contacts_wrong_extension_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "contacts": FakeContactsRepo(), "settings": FakeSettingsRepo(), "title_rules": FakeTitleRuleRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.post(
        "/api/contacts/import",
        data={"file": (BytesIO(b"not a csv"), "connections.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_import_contacts_success(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "contacts": FakeContactsRepo(), "settings": FakeSettingsRepo(), "title_rules": FakeTitleRuleRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    csv_bytes = (
        b"First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
        b"Jane,Doe,https://linkedin.com/in/janedoe,,Acme Inc,Engineer,15 Jan 2024\n"
    )
    response = client.post(
        "/api/contacts/import",
        data={"file": (BytesIO(csv_bytes), "Connections.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["imported"] == 1
    assert data["added"] == 1
    assert data["skipped"] == 0
    assert data["imported_at"]
    assert (
        repos["settings"].get(_TEST_USER_ID, "contacts_last_imported_at") == data["imported_at"]
    )


def test_get_contacts_status(monkeypatch: pytest.MonkeyPatch) -> None:
    settings_repo = FakeSettingsRepo()
    settings_repo.set(_TEST_USER_ID, "contacts_last_imported_at", "2026-07-25T12:00:00+00:00")
    repos = {
        "conn": FakeConnection(),
        "contacts": FakeContactsRepo([{"id": 1}, {"id": 2}]),
        "settings": settings_repo,
    }
    client = _make_app(repos, monkeypatch).test_client()

    response = client.get("/api/contacts/status")
    assert response.status_code == 200
    assert response.get_json() == {
        "count": 2,
        "last_imported_at": "2026-07-25T12:00:00+00:00",
    }


# ai: claude-sonnet-5 | 2026-09-28
class FakeCompanyRepo:
    def __init__(self, report: list[dict[str, Any]] | None = None) -> None:
        self.report_rows = report or []
        self.upserts: list[tuple[int, str, dict[str, Any]]] = []

    def report(self, user_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.report_rows]

    def upsert(self, user_id: int, company_normalized: str, fields: dict[str, Any]) -> None:
        self.upserts.append((user_id, company_normalized, fields))


# ai: claude-opus-5-5 | 2026-10-14
class FakeCandidateProfilesRepo:
    def __init__(self, content: str | None) -> None:
        self.content = content

    def get(self, user_id: int) -> dict[str, Any] | None:
        return {"content": self.content} if self.content else None


# ai: claude-sonnet-5 | 2026-09-25
def _contacts_client(
    monkeypatch: pytest.MonkeyPatch,
    contacts: FakeContactsRepo | None = None,
    outreach: FakeContactOutreachRepo | None = None,
    employment_history: FakeContactEmploymentHistoryRepo | None = None,
    companies: FakeCompanyRepo | None = None,
    title_rules: FakeTitleRuleRepo | None = None,
    settings: FakeSettingsRepo | None = None,
    candidate_profiles: FakeCandidateProfilesRepo | None = None,
    templates: FakeOutreachTemplatesRepo | None = None,
) -> Any:
    repos = {
        "conn": FakeConnection(),
        "contacts": contacts or FakeContactsRepo(),
        "contact_outreach": outreach or FakeContactOutreachRepo(),
        "contact_employment_history": employment_history or FakeContactEmploymentHistoryRepo(),
        "settings": settings or FakeSettingsRepo(),
        "candidate_profiles": candidate_profiles or FakeCandidateProfilesRepo(None),
        "companies": companies or FakeCompanyRepo(),
        "title_rules": title_rules or FakeTitleRuleRepo(),
        "jobs": FakeJobsRepo(),
        "stage_statuses": FakeStageStatusesRepo(),
        "outreach_templates": templates or FakeOutreachTemplatesRepo(),
    }
    return _make_app(repos, monkeypatch).test_client()


def test_list_contacts_returns_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _contacts_client(monkeypatch, FakeContactsRepo([{"id": 1, "full_name": "Jane Doe"}]))

    response = client.get("/api/contacts")
    assert response.status_code == 200
    # ai: gpt-5 | 2026-10-03
    assert response.get_json() == {
        "contacts": [{"id": 1, "full_name": "Jane Doe", "outreach_score": 0.0}]
    }


def test_update_contact_only_writes_fields_that_were_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    contacts = FakeContactsRepo([{"id": 1}])
    client = _contacts_client(monkeypatch, contacts)

    response = client.patch("/api/contacts/1", json={"interested": True})
    assert response.status_code == 200
    assert contacts.updates == [(1, {"interested": True})]


# ai: claude-sonnet-5 | 2026-10-13
def test_update_contact_can_set_interested_back_to_undecided(monkeypatch: pytest.MonkeyPatch) -> None:
    contacts = FakeContactsRepo([{"id": 1}])
    client = _contacts_client(monkeypatch, contacts)

    response = client.patch("/api/contacts/1", json={"interested": None})
    assert response.status_code == 200
    assert contacts.updates == [(1, {"interested": None})]


def test_update_contact_can_set_and_unset_recruiter_type(monkeypatch: pytest.MonkeyPatch) -> None:
    contacts = FakeContactsRepo([{"id": 1}])
    client = _contacts_client(monkeypatch, contacts)

    # ai: claude-sonnet-5 | 2026-09-30 -- was: recruiter_type "high" (renamed to "high_reliability")
    set_response = client.patch("/api/contacts/1", json={"recruiter_type": "high_reliability"})
    assert set_response.status_code == 200
    unset_response = client.patch("/api/contacts/1", json={"recruiter_type": None})
    assert unset_response.status_code == 200
    assert contacts.updates == [
        (1, {"recruiter_type": "high_reliability", "decided_by": "manual"}),
        (1, {"recruiter_type": None, "decided_by": "manual"}),
    ]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"recruiter_type": "yes"},
        {"recruiter_type": "recruiter"},
        {"contact_type": "recruiter"},
        {"interested": "yes"},
        {"email_address": "not-an-email"},
        {"notes": "x" * 5001},
        {"notes": 42},
        {"full_name": "Injected"},
        {"decided_by": "manual"},
        {"recruiter_type": "high_reliability", "decided_by": "title_rule"},  # ai: claude-sonnet-5 | 2026-09-30
    ],
)
def test_update_contact_rejects_invalid_body(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    contacts = FakeContactsRepo([{"id": 1}])
    client = _contacts_client(monkeypatch, contacts)

    response = client.patch("/api/contacts/1", json=body)
    assert response.status_code == 400
    assert contacts.updates == []


def test_update_contact_normalizes_blank_email_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    contacts = FakeContactsRepo([{"id": 1}])
    client = _contacts_client(monkeypatch, contacts)

    response = client.patch("/api/contacts/1", json={"email_address": "  "})
    assert response.status_code == 200
    assert contacts.updates == [(1, {"email_address": None})]


def test_update_contact_trims_notes_and_blank_becomes_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contacts = FakeContactsRepo([{"id": 1}])
    client = _contacts_client(monkeypatch, contacts)

    assert client.patch("/api/contacts/1", json={"notes": "  met at PyCon \n"}).status_code == 200
    assert client.patch("/api/contacts/1", json={"notes": "   "}).status_code == 200
    assert contacts.updates == [(1, {"notes": "met at PyCon"}), (1, {"notes": None})]


def test_update_contact_unknown_id_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _contacts_client(monkeypatch)

    response = client.patch("/api/contacts/5", json={"interested": True})
    assert response.status_code == 404


def test_log_contact_outreach_uses_supplied_date(monkeypatch: pytest.MonkeyPatch) -> None:
    outreach = FakeContactOutreachRepo(contact_ids={1})
    client = _contacts_client(monkeypatch, outreach=outreach)

    response = client.post("/api/contacts/1/outreach", json={"sent_at": "2026-09-20T15:30:00Z", "channel": "email"})
    assert response.status_code == 201
    assert outreach.added == [(1, "2026-09-20T15:30:00+00:00", "email")]


def test_log_contact_outreach_converts_offset_timestamp_to_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outreach = FakeContactOutreachRepo(contact_ids={1})
    client = _contacts_client(monkeypatch, outreach=outreach)

    response = client.post("/api/contacts/1/outreach", json={"sent_at": "2026-09-20T10:00:00-05:00", "channel": "email"})
    assert response.status_code == 201
    assert outreach.added == [(1, "2026-09-20T15:00:00+00:00", "email")]


def test_log_contact_outreach_defaults_to_today_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    outreach = FakeContactOutreachRepo(contact_ids={1})
    client = _contacts_client(monkeypatch, outreach=outreach)

    response = client.post("/api/contacts/1/outreach", json={"channel": "email"})
    assert response.status_code == 201
    assert outreach.added[0][1].endswith("+00:00")
    assert "T" in outreach.added[0][1]


@pytest.mark.parametrize(
    "bad_date",
    ["2026-09-20", "2026-09-20T10:00:00", "2026-13-40T10:00:00Z", "yesterday", 20260920],
)
def test_log_contact_outreach_rejects_bad_timestamp(
    monkeypatch: pytest.MonkeyPatch, bad_date: object
) -> None:
    outreach = FakeContactOutreachRepo(contact_ids={1})
    client = _contacts_client(monkeypatch, outreach=outreach)

    response = client.post("/api/contacts/1/outreach", json={"sent_at": bad_date, "channel": "email"})
    assert response.status_code == 400
    assert outreach.added == []


def test_log_contact_outreach_unknown_contact_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _contacts_client(monkeypatch)

    response = client.post("/api/contacts/1/outreach", json={"channel": "email"})
    assert response.status_code == 404


# ai: claude-sonnet-5 | 2026-10-15
def test_list_contact_employment_history(monkeypatch: pytest.MonkeyPatch) -> None:
    history = FakeContactEmploymentHistoryRepo()
    history.rows[1] = {"id": 1, "contact_id": 1, "company": "Watchfire", "title": "Engineer"}
    client = _contacts_client(monkeypatch, employment_history=history)

    response = client.get("/api/contacts/1/employment-history")
    assert response.status_code == 200
    assert response.get_json() == [{"id": 1, "contact_id": 1, "company": "Watchfire", "title": "Engineer"}]


def test_add_contact_employment_history(monkeypatch: pytest.MonkeyPatch) -> None:
    history = FakeContactEmploymentHistoryRepo(contact_ids={1})
    client = _contacts_client(monkeypatch, employment_history=history)

    response = client.post(
        "/api/contacts/1/employment-history",
        json={"company": "Watchfire", "title": "Engineer", "start_date": "2015", "end_date": "2018"},
    )
    assert response.status_code == 201
    data = response.get_json()
    assert data["company"] == "Watchfire"
    assert data["start_date"] == "2015"
    assert data["end_date"] == "2018"
    assert data["source"] == "manual"


def test_add_contact_employment_history_requires_company(monkeypatch: pytest.MonkeyPatch) -> None:
    history = FakeContactEmploymentHistoryRepo(contact_ids={1})
    client = _contacts_client(monkeypatch, employment_history=history)

    response = client.post("/api/contacts/1/employment-history", json={"company": "   "})
    assert response.status_code == 400
    assert history.rows == {}


def test_add_contact_employment_history_unknown_contact_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _contacts_client(monkeypatch)

    response = client.post("/api/contacts/1/employment-history", json={"company": "Watchfire"})
    assert response.status_code == 404


def test_update_contact_employment_history(monkeypatch: pytest.MonkeyPatch) -> None:
    history = FakeContactEmploymentHistoryRepo(entry_ids={7})
    client = _contacts_client(monkeypatch, employment_history=history)

    response = client.patch("/api/contact-employment-history/7", json={"end_date": "2019"})
    assert response.status_code == 200
    assert history.updates == [(7, {"end_date": "2019"})]


def test_update_contact_employment_history_unknown_id_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _contacts_client(monkeypatch)

    response = client.patch("/api/contact-employment-history/7", json={"end_date": "2019"})
    assert response.status_code == 404


def test_update_contact_employment_history_rejects_blank_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = FakeContactEmploymentHistoryRepo(entry_ids={7})
    client = _contacts_client(monkeypatch, employment_history=history)

    response = client.patch("/api/contact-employment-history/7", json={"company": "   "})
    assert response.status_code == 400
    assert history.updates == []


# ai: claude-sonnet-5 | 2026-10-18
# 2026-10-18 full review, Medium finding: company/title are NOT NULL columns
# (backend/db/schema.py), but the old validator let an explicit JSON null
# through unchanged, so this reached the repository (and, with a real
# connection, an unhandled IntegrityError -> 500) instead of being rejected
# as a 400 here. tests/test_contacts_repository.py separately proves the
# column really is NOT NULL, so this stays a true regression guard rather
# than only asserting against the validator's own behavior.
def test_update_contact_employment_history_rejects_null_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = FakeContactEmploymentHistoryRepo(entry_ids={7})
    client = _contacts_client(monkeypatch, employment_history=history)

    response = client.patch("/api/contact-employment-history/7", json={"company": None})
    assert response.status_code == 400
    assert history.updates == []


def test_update_contact_employment_history_rejects_null_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = FakeContactEmploymentHistoryRepo(entry_ids={7})
    client = _contacts_client(monkeypatch, employment_history=history)

    response = client.patch("/api/contact-employment-history/7", json={"title": None})
    assert response.status_code == 400
    assert history.updates == []


def test_update_contact_employment_history_still_allows_null_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """start_date/end_date are nullable columns -- explicit null must still work."""
    history = FakeContactEmploymentHistoryRepo(entry_ids={7})
    client = _contacts_client(monkeypatch, employment_history=history)

    response = client.patch(
        "/api/contact-employment-history/7", json={"end_date": None}
    )
    assert response.status_code == 200
    assert history.updates == [(7, {"end_date": None})]


def test_delete_contact_employment_history(monkeypatch: pytest.MonkeyPatch) -> None:
    history = FakeContactEmploymentHistoryRepo(entry_ids={7})
    client = _contacts_client(monkeypatch, employment_history=history)

    response = client.delete("/api/contact-employment-history/7")
    assert response.status_code == 200
    assert history.deleted == [7]


def test_delete_contact_employment_history_unknown_id_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _contacts_client(monkeypatch)

    response = client.delete("/api/contact-employment-history/7")
    assert response.status_code == 404


def test_set_and_clear_outreach_response(monkeypatch: pytest.MonkeyPatch) -> None:
    outreach = FakeContactOutreachRepo(outreach_ids={7})
    client = _contacts_client(monkeypatch, outreach=outreach)

    set_response = client.patch(
        "/api/contact-outreach/7", json={"response_received_at": "2026-09-22T09:15:00Z"}
    )
    assert set_response.status_code == 200
    assert outreach.responses == {7: "2026-09-22T09:15:00+00:00"}

    clear_response = client.patch("/api/contact-outreach/7", json={"response_received_at": None})
    assert clear_response.status_code == 200
    assert outreach.responses == {7: None}


def test_outreach_response_body_must_state_the_field(monkeypatch: pytest.MonkeyPatch) -> None:
    outreach = FakeContactOutreachRepo(outreach_ids={7})
    client = _contacts_client(monkeypatch, outreach=outreach)

    assert client.patch("/api/contact-outreach/7", json={}).status_code == 400
    assert outreach.responses == {}


def test_outreach_log_entry_unknown_id_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _contacts_client(monkeypatch)

    patch_response = client.patch("/api/contact-outreach/1", json={"response_received_at": None})
    assert patch_response.status_code == 404
    assert client.delete("/api/contact-outreach/1").status_code == 404


def test_delete_contact_outreach(monkeypatch: pytest.MonkeyPatch) -> None:
    outreach = FakeContactOutreachRepo(outreach_ids={7})
    client = _contacts_client(monkeypatch, outreach=outreach)

    assert client.delete("/api/contact-outreach/7").status_code == 200
    assert outreach.deleted == [7]


# ai: claude-sonnet-5 | 2026-09-25
def test_bulk_set_recruiter_dedupes_ids_and_reports_count(monkeypatch: pytest.MonkeyPatch) -> None:
    contacts = FakeContactsRepo([{"id": 1}, {"id": 2}])
    client = _contacts_client(monkeypatch, contacts)

    response = client.post(
        "/api/contacts/bulk-recruiter", json={"contact_ids": [2, 1, 2], "recruiter_type": "unrated"}
    )
    assert response.status_code == 200
    assert response.get_json() == {"updated": 2}
    assert contacts.bulk_calls == [([1, 2], "unrated")]


def test_bulk_set_recruiter_accepts_null_to_unmark(monkeypatch: pytest.MonkeyPatch) -> None:
    contacts = FakeContactsRepo([{"id": 1}])
    client = _contacts_client(monkeypatch, contacts)

    response = client.post(
        "/api/contacts/bulk-recruiter", json={"contact_ids": [1], "recruiter_type": None}
    )
    assert response.status_code == 200
    assert contacts.bulk_calls == [([1], None)]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"contact_ids": [], "recruiter_type": "unrated"},
        {"contact_ids": [1]},
        {"contact_ids": [1], "recruiter_type": "yes"},
        {"contact_ids": ["1"], "recruiter_type": "unrated"},
        {"contact_ids": [True], "recruiter_type": "unrated"},
        {"contact_ids": [1], "recruiter_type": "unrated", "interested": True},
        {"contact_ids": [1], "recruiter_type": "unrated", "decided_by": "manual"},
    ],
)
def test_bulk_set_recruiter_rejects_invalid_body(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    contacts = FakeContactsRepo([{"id": 1}])
    client = _contacts_client(monkeypatch, contacts)

    response = client.post("/api/contacts/bulk-recruiter", json=body)
    assert response.status_code == 400
    assert contacts.bulk_calls == []


# ai: claude-sonnet-5 | 2026-09-28
def test_get_companies_report_returns_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    companies = FakeCompanyRepo([{"company_normalized": "acme", "contact_count": 2}])
    client = _contacts_client(monkeypatch, companies=companies)

    response = client.get("/api/contacts/companies")
    assert response.status_code == 200
    assert response.get_json() == {"companies": [{"company_normalized": "acme", "contact_count": 2}]}


def test_update_company_only_writes_fields_that_were_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    companies = FakeCompanyRepo()
    client = _contacts_client(monkeypatch, companies=companies)

    response = client.patch(
        "/api/contacts/companies", json={"company_normalized": "acme", "is_agency": True}
    )
    assert response.status_code == 200
    assert companies.upserts == [(_TEST_USER_ID, "acme", {"is_agency": True})]


def test_update_company_accepts_workforce_and_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    companies = FakeCompanyRepo()
    client = _contacts_client(monkeypatch, companies=companies)

    response = client.patch(
        "/api/contacts/companies",
        json={"company_normalized": "acme", "workforce": "offshore_hybrid", "notes": "  big shop  "},
    )
    assert response.status_code == 200
    assert companies.upserts == [
        (_TEST_USER_ID, "acme", {"workforce": "offshore_hybrid", "notes": "big shop"})
    ]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"company_normalized": ""},
        {"company_normalized": "acme"},
        {"company_normalized": "acme", "workforce": "not-a-real-value"},
        {"company_normalized": "acme", "is_agency": "yes"},
        {"company_normalized": "acme", "notes": "x" * 5001},
        {"company_normalized": "acme", "is_agency": True, "extra_field": 1},
    ],
)
def test_update_company_rejects_invalid_body(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    companies = FakeCompanyRepo()
    client = _contacts_client(monkeypatch, companies=companies)

    response = client.patch("/api/contacts/companies", json=body)
    assert response.status_code == 400
    assert companies.upserts == []


# ai: claude-sonnet-5 | 2026-09-28
def test_get_titles_report_returns_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    title_rules = FakeTitleRuleRepo([{"normalized_title": "recruiter", "contact_count": 3}])
    client = _contacts_client(monkeypatch, title_rules=title_rules)

    response = client.get("/api/contacts/titles")
    assert response.status_code == 200
    assert response.get_json() == {
        "titles": [{"normalized_title": "recruiter", "contact_count": 3}]
    }


def test_set_title_rule_flags_a_title(monkeypatch: pytest.MonkeyPatch) -> None:
    title_rules = FakeTitleRuleRepo()
    client = _contacts_client(monkeypatch, title_rules=title_rules)

    response = client.post(
        "/api/contacts/titles/rule", json={"normalized_title": "recruiter", "recruiter_type": "unrated"}
    )
    assert response.status_code == 200
    assert title_rules.set_calls == [(_TEST_USER_ID, "recruiter", "unrated")]


def test_set_title_rule_accepts_null_to_dismiss(monkeypatch: pytest.MonkeyPatch) -> None:
    title_rules = FakeTitleRuleRepo()
    client = _contacts_client(monkeypatch, title_rules=title_rules)

    response = client.post(
        "/api/contacts/titles/rule", json={"normalized_title": "talent manager"}
    )
    assert response.status_code == 200
    assert title_rules.set_calls == [(_TEST_USER_ID, "talent manager", None)]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"normalized_title": "recruiter", "recruiter_type": "yes"},
        {"normalized_title": "recruiter", "recruiter_type": "unrated", "extra": 1},
    ],
)
def test_set_title_rule_rejects_invalid_body(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    title_rules = FakeTitleRuleRepo()
    client = _contacts_client(monkeypatch, title_rules=title_rules)

    response = client.post("/api/contacts/titles/rule", json=body)
    assert response.status_code == 400
    assert title_rules.set_calls == []


def test_undo_title_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    title_rules = FakeTitleRuleRepo()
    client = _contacts_client(monkeypatch, title_rules=title_rules)

    response = client.post("/api/contacts/titles/rule/undo", json={"normalized_title": "recruiter"})
    assert response.status_code == 200
    assert title_rules.undo_calls == [(_TEST_USER_ID, "recruiter")]


def test_undo_title_rule_rejects_invalid_body(monkeypatch: pytest.MonkeyPatch) -> None:
    title_rules = FakeTitleRuleRepo()
    client = _contacts_client(monkeypatch, title_rules=title_rules)

    response = client.post("/api/contacts/titles/rule/undo", json={})
    assert response.status_code == 400
    assert title_rules.undo_calls == []


# ai: claude-sonnet-5 | 2026-09-29
def test_suggest_title_rules_returns_suggestions(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.contacts.title_suggestion_service import (
        SuggestionResult,
        TitleSuggestion,
        TitleSuggestionPrep,
    )

    captured = {}

    def fake_prepare(user_id, repos, *, use_ai):
        captured["prepare_user_id"] = user_id
        captured["prepare_use_ai"] = use_ai
        return TitleSuggestionPrep(suggestions=[], leftovers=[], companies={})

    def fake_finish(user_id, prep, *, use_ai):
        captured["finish_user_id"] = user_id
        captured["finish_use_ai"] = use_ai
        return SuggestionResult(
            suggestions=[TitleSuggestion("recruiter", "Recruiter", "keyword match")],
            ai_error=None,
        )

    monkeypatch.setattr("routes.contacts.prepare_title_suggestions", fake_prepare)
    monkeypatch.setattr("routes.contacts.finish_title_suggestions", fake_finish)
    client = _contacts_client(monkeypatch)

    response = client.post("/api/contacts/titles/suggest", json={"use_ai": True})

    assert response.status_code == 200
    assert response.get_json() == {
        "suggestions": [
            {"normalized_title": "recruiter", "title": "Recruiter", "reason": "keyword match"}
        ],
        "ai_error": None,
    }
    assert captured == {
        "prepare_user_id": _TEST_USER_ID,
        "prepare_use_ai": True,
        "finish_user_id": _TEST_USER_ID,
        "finish_use_ai": True,
    }


def test_suggest_title_rules_defaults_use_ai_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.contacts.title_suggestion_service import (
        SuggestionResult,
        TitleSuggestionPrep,
    )

    captured = {}

    def fake_prepare(user_id, repos, *, use_ai):
        captured["prepare_use_ai"] = use_ai
        return TitleSuggestionPrep(suggestions=[], leftovers=[], companies={})

    def fake_finish(user_id, prep, *, use_ai):
        captured["finish_use_ai"] = use_ai
        return SuggestionResult(suggestions=[], ai_error=None)

    monkeypatch.setattr("routes.contacts.prepare_title_suggestions", fake_prepare)
    monkeypatch.setattr("routes.contacts.finish_title_suggestions", fake_finish)
    client = _contacts_client(monkeypatch)

    response = client.post("/api/contacts/titles/suggest", json={})

    assert response.status_code == 200
    assert captured == {"prepare_use_ai": False, "finish_use_ai": False}


def test_suggest_title_rules_rejects_invalid_body(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _contacts_client(monkeypatch)

    response = client.post("/api/contacts/titles/suggest", json={"use_ai": "yes"})
    assert response.status_code == 400


def test_apply_title_suggestions_calls_set_rule_for_each_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    title_rules = FakeTitleRuleRepo()
    client = _contacts_client(monkeypatch, title_rules=title_rules)

    response = client.post(
        "/api/contacts/titles/suggest/apply",
        json={"normalized_titles": ["recruiter", "talent partner"]},
    )

    assert response.status_code == 200
    assert response.get_json() == {"applied": 2, "decided": 0}
    assert title_rules.set_calls == [
        (_TEST_USER_ID, "recruiter", "unrated"),
        (_TEST_USER_ID, "talent partner", "unrated"),
    ]


@pytest.mark.parametrize("body", [{}, {"normalized_titles": []}, {"normalized_titles": [1]}])
def test_apply_title_suggestions_rejects_invalid_body(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    title_rules = FakeTitleRuleRepo()
    client = _contacts_client(monkeypatch, title_rules=title_rules)

    response = client.post("/api/contacts/titles/suggest/apply", json=body)
    assert response.status_code == 400
    assert title_rules.set_calls == []


# ai: claude-opus-5-5 | 2026-10-14
def test_log_contact_outreach_requires_a_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    outreach = FakeContactOutreachRepo(contact_ids={1})
    client = _contacts_client(monkeypatch, outreach=outreach)

    assert client.post("/api/contacts/1/outreach", json={}).status_code == 400
    assert client.post("/api/contacts/1/outreach", json={"channel": "fax"}).status_code == 400
    assert outreach.added == []


# ai: claude-opus-5-5 | 2026-10-14
def test_log_linkedin_outreach_records_channel_and_clears_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contacts = FakeContactsRepo([{"id": 1}])
    outreach = FakeContactOutreachRepo(contact_ids={1})
    client = _contacts_client(monkeypatch, contacts, outreach=outreach)

    response = client.post("/api/contacts/1/outreach", json={"channel": "linkedin"})
    assert response.status_code == 201
    assert response.get_json()["channel"] == "linkedin"
    assert outreach.added[0][0] == 1 and outreach.added[0][2] == "linkedin"
    assert contacts.cleared == [1]


# ai: claude-opus-5-5 | 2026-10-14
def test_log_outreach_for_unknown_contact_does_not_clear_a_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contacts = FakeContactsRepo([{"id": 1}])
    client = _contacts_client(monkeypatch, contacts)

    assert client.post("/api/contacts/1/outreach", json={"channel": "linkedin"}).status_code == 404
    assert contacts.cleared == []


# ai: claude-opus-5-5 | 2026-10-21
def _template(template_id: int, title: str, body: str) -> dict[str, Any]:
    return {"id": template_id, "title": title, "body": body, "created_at": "t", "updated_at": "t"}


# ai: claude-opus-5-5 | 2026-10-21
def test_outreach_templates_create_list_update_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    templates = FakeOutreachTemplatesRepo()
    client = _contacts_client(monkeypatch, templates=templates)

    assert client.get("/api/outreach-templates").get_json() == {"templates": []}

    created = client.post(
        "/api/outreach-templates", json={"title": "  Recruiters ", "body": "  Hi {first_name}!  "}
    )
    assert created.status_code == 201
    template = created.get_json()
    assert (template["title"], template["body"]) == ("Recruiters", "Hi {first_name}!")
    assert template["created_at"].endswith("+00:00")
    client.post("/api/outreach-templates", json={"title": "close coworkers", "body": "Hey"})
    listed = client.get("/api/outreach-templates").get_json()["templates"]
    assert [t["title"] for t in listed] == ["close coworkers", "Recruiters"]

    template_url = f"/api/outreach-templates/{template['id']}"
    updated = client.put(template_url, json={"title": "Good recruiters", "body": "Hello"})
    assert updated.status_code == 200
    assert (updated.get_json()["title"], updated.get_json()["body"]) == ("Good recruiters", "Hello")

    assert client.delete(template_url).status_code == 200
    assert [t["title"] for t in templates.rows] == ["close coworkers"]
    assert client.delete(template_url).status_code == 404
    assert client.put(template_url, json={"title": "x", "body": "y"}).status_code == 404


# ai: claude-opus-5-5 | 2026-10-21
def test_outreach_template_duplicate_title_returns_409(monkeypatch: pytest.MonkeyPatch) -> None:
    templates = FakeOutreachTemplatesRepo([_template(1, "Recruiters", "a"), _template(2, "Coworkers", "b")])
    client = _contacts_client(monkeypatch, templates=templates)

    assert client.post(
        "/api/outreach-templates", json={"title": "Recruiters", "body": "c"}
    ).status_code == 409
    assert client.put(
        "/api/outreach-templates/2", json={"title": "Recruiters", "body": "b"}
    ).status_code == 409
    assert [(t["title"], t["body"]) for t in templates.rows] == [("Recruiters", "a"), ("Coworkers", "b")]


# ai: claude-opus-5-5 | 2026-10-21
@pytest.mark.parametrize(
    "bad_body",
    [
        {"title": "T", "body": ""},
        {"title": "T", "body": "   "},
        {"title": "T", "body": "x" * 8001},
        {"title": "T", "body": None},
        {"title": "T", "body": 5},
        {"title": " ", "body": "Hi"},
        {"title": "x" * 101, "body": "Hi"},
        {"body": "Hi"},
        {"title": "T"},
        {"title": "T", "body": "Hi", "extra": 1},
    ],
)
def test_outreach_template_rejects_bad_values(
    monkeypatch: pytest.MonkeyPatch, bad_body: dict[str, object]
) -> None:
    templates = FakeOutreachTemplatesRepo([_template(1, "Recruiters", "a")])
    client = _contacts_client(monkeypatch, templates=templates)

    assert client.post("/api/outreach-templates", json=bad_body).status_code == 400
    assert client.put("/api/outreach-templates/1", json=bad_body).status_code == 400
    assert templates.rows == [_template(1, "Recruiters", "a")]


# ai: claude-opus-5-5 | 2026-10-21 -- renders the template picked by id
def test_outreach_draft_renders_the_chosen_template(monkeypatch: pytest.MonkeyPatch) -> None:
    contacts = FakeContactsRepo(
        [{"id": 1, "full_name": "Jane Q Doe", "company_raw": "Acme", "position": "CTO"}]
    )
    templates = FakeOutreachTemplatesRepo(
        [
            _template(1, "Coworkers", "Hi {first_name} ({position} at {company}) {x}"),
            _template(2, "Recruiters", "Hello {full_name}"),
        ]
    )
    client = _contacts_client(monkeypatch, contacts, templates=templates)

    response = client.post("/api/contacts/1/outreach-draft/1", json={})
    assert response.status_code == 200
    assert response.get_json() == {"draft": "Hi Jane (CTO at Acme) {x}"}
    assert client.post("/api/contacts/1/outreach-draft/2", json={}).get_json() == {
        "draft": "Hello Jane Q Doe"
    }
    assert client.post("/api/contacts/2/outreach-draft/1", json={}).status_code == 404
    missing_template = client.post("/api/contacts/1/outreach-draft/3", json={})
    assert missing_template.status_code == 404
    assert missing_template.get_json() == {"error": "Template not found"}


# ai: claude-opus-5-5 | 2026-10-14
@pytest.mark.parametrize("profile", ["Seasoned backend engineer.", None])
def test_outreach_draft_with_ai_passes_template_draft_and_profile(
    monkeypatch: pytest.MonkeyPatch, profile: str | None
) -> None:
    contacts = FakeContactsRepo([{"id": 1, "full_name": "Jane Doe", "company_raw": "Acme"}])
    templates = FakeOutreachTemplatesRepo([_template(1, "General", "Hi {first_name}")])  # ai: claude-opus-5-5 | 2026-10-21
    calls: list[tuple[int, str, str]] = []

    class FakeWriter:
        def __init__(self, user_id: int) -> None:
            self.user_id = user_id

        def write(self, contact: dict[str, Any], template_draft: str, candidate_profile: str) -> str:
            calls.append((contact["id"], template_draft, candidate_profile))
            return "AI wrote this"

    monkeypatch.setattr("routes.contacts.OutreachDraftWriter", FakeWriter)
    client = _contacts_client(
        monkeypatch,
        contacts,
        templates=templates,
        candidate_profiles=FakeCandidateProfilesRepo(profile),
    )

    response = client.post("/api/contacts/1/outreach-draft/1", json={"use_ai": True})
    assert response.status_code == 200
    assert response.get_json() == {"draft": "AI wrote this"}
    assert calls == [(1, "Hi Jane", profile or "")]


# ai: claude-opus-5-5 | 2026-10-14
def test_outreach_draft_ai_failure_returns_502(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingWriter:
        def __init__(self, user_id: int) -> None:
            pass

        def write(self, contact: dict[str, Any], template_draft: str, candidate_profile: str) -> str:
            raise AIProviderError("Anthropic API key is not configured.")

    monkeypatch.setattr("routes.contacts.OutreachDraftWriter", FailingWriter)
    client = _contacts_client(
        monkeypatch,
        FakeContactsRepo([{"id": 1, "full_name": "Jane"}]),
        templates=FakeOutreachTemplatesRepo([_template(1, "General", "Hi")]),  # ai: claude-opus-5-5 | 2026-10-21
    )

    response = client.post("/api/contacts/1/outreach-draft/1", json={"use_ai": True})
    assert response.status_code == 502
    assert response.get_json() == {"error": "Anthropic API key is not configured."}


# ai: claude-opus-5-5 | 2026-10-14
def test_stage_outreach_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    contacts = FakeContactsRepo([{"id": 1}])
    client = _contacts_client(monkeypatch, contacts)

    response = client.post("/api/contacts/1/outreach-draft/stage", json={"draft": " Hi Jane "})
    assert response.status_code == 200
    staged_at = response.get_json()["staged_at"]
    assert staged_at.endswith("+00:00")
    assert contacts.staged == [(1, "Hi Jane", staged_at)]

    assert client.post("/api/contacts/2/outreach-draft/stage", json={"draft": "Hi"}).status_code == 404
    assert client.post("/api/contacts/1/outreach-draft/stage", json={"draft": " "}).status_code == 400
    assert len(contacts.staged) == 1
