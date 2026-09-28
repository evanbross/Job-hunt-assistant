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

from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from typing import Any

import pytest
from flask import Flask

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

    def get_all(self, user_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]

    def sync_import(self, user_id: int, rows: list[dict[str, Any]]) -> ContactSyncResult:
        self.rows = rows
        return ContactSyncResult(added=len(rows), updated=0, removed=0)

    def list_with_emails(self, user_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]

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


class FakeContactEmailsRepo:
    def __init__(
        self, contact_ids: set[int] | None = None, email_ids: set[int] | None = None
    ) -> None:
        self.contact_ids = contact_ids or set()
        self.email_ids = email_ids or set()
        self.added: list[tuple[int, str]] = []
        self.responses: dict[int, str | None] = {}
        self.deleted: list[int] = []

    def add(self, user_id: int, contact_id: int, sent_at: str) -> dict[str, Any] | None:
        if contact_id not in self.contact_ids:
            return None
        self.added.append((contact_id, sent_at))
        return {"id": 99, "sent_at": sent_at, "response_received_at": None}

    def set_response(self, user_id: int, email_id: int, response_received_at: str | None) -> bool:
        if email_id not in self.email_ids:
            return False
        self.responses[email_id] = response_received_at
        return True

    def delete(self, user_id: int, email_id: int) -> bool:
        if email_id not in self.email_ids:
            return False
        self.deleted.append(email_id)
        return True


class FakeSettingsRepo:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, user_id: int, key: str, default: str | None = None) -> str | None:
        return self.values.get(key, default)

    def set(self, user_id: int, key: str, value: str) -> None:
        self.values[key] = value


def _make_app(repos: dict, monkeypatch: pytest.MonkeyPatch) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(contacts_bp, url_prefix="/api")
    monkeypatch.setattr("routes.contacts.get_repos", make_fake_get_repos(repos))
    monkeypatch.setattr("routes.contacts.current_user_id", lambda: _TEST_USER_ID)
    return app


def test_import_contacts_missing_file_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "contacts": FakeContactsRepo(), "settings": FakeSettingsRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.post("/api/contacts/import")
    assert response.status_code == 400


def test_import_contacts_wrong_extension_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "contacts": FakeContactsRepo(), "settings": FakeSettingsRepo()}
    client = _make_app(repos, monkeypatch).test_client()

    response = client.post(
        "/api/contacts/import",
        data={"file": (BytesIO(b"not a csv"), "connections.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_import_contacts_success(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = {"conn": FakeConnection(), "contacts": FakeContactsRepo(), "settings": FakeSettingsRepo()}
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


def test_get_contacts_company_map_groups_by_normalized_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repos = {
        "conn": FakeConnection(),
        "contacts": FakeContactsRepo(
            [
                {
                    "id": 1,
                    "full_name": "Jane Doe",
                    "company_normalized": "acme",
                    "position": "Engineer",
                    "profile_url": "https://linkedin.com/in/janedoe",
                    "connected_on": "2024-01-15",
                },
                {
                    "id": 2,
                    "full_name": "No Company",
                    "company_normalized": "",
                    "position": "",
                    "profile_url": None,
                    "connected_on": None,
                },
            ]
        ),
        "settings": FakeSettingsRepo(),
    }
    client = _make_app(repos, monkeypatch).test_client()

    response = client.get("/api/contacts/company-map")
    assert response.status_code == 200
    data = response.get_json()
    assert list(data.keys()) == ["acme"]
    assert data["acme"][0]["full_name"] == "Jane Doe"


# ai: claude-sonnet-5 | 2026-09-28
class FakeCompanyRepo:
    def __init__(self, report: list[dict[str, Any]] | None = None) -> None:
        self.report_rows = report or []
        self.upserts: list[tuple[int, str, dict[str, Any]]] = []

    def report(self, user_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.report_rows]

    def upsert(self, user_id: int, company_normalized: str, fields: dict[str, Any]) -> None:
        self.upserts.append((user_id, company_normalized, fields))


# ai: claude-sonnet-5 | 2026-09-25
def _contacts_client(
    monkeypatch: pytest.MonkeyPatch,
    contacts: FakeContactsRepo | None = None,
    emails: FakeContactEmailsRepo | None = None,
    companies: FakeCompanyRepo | None = None,
) -> Any:
    repos = {
        "conn": FakeConnection(),
        "contacts": contacts or FakeContactsRepo(),
        "contact_emails": emails or FakeContactEmailsRepo(),
        "settings": FakeSettingsRepo(),
        "companies": companies or FakeCompanyRepo(),
    }
    return _make_app(repos, monkeypatch).test_client()


def test_list_contacts_returns_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _contacts_client(monkeypatch, FakeContactsRepo([{"id": 1, "full_name": "Jane Doe"}]))

    response = client.get("/api/contacts")
    assert response.status_code == 200
    assert response.get_json() == {"contacts": [{"id": 1, "full_name": "Jane Doe"}]}


def test_update_contact_only_writes_fields_that_were_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    contacts = FakeContactsRepo([{"id": 1}])
    client = _contacts_client(monkeypatch, contacts)

    response = client.patch("/api/contacts/1", json={"interested": True})
    assert response.status_code == 200
    assert contacts.updates == [(1, {"interested": True})]


def test_update_contact_can_set_and_unset_recruiter_type(monkeypatch: pytest.MonkeyPatch) -> None:
    contacts = FakeContactsRepo([{"id": 1}])
    client = _contacts_client(monkeypatch, contacts)

    set_response = client.patch("/api/contacts/1", json={"recruiter_type": "high"})
    assert set_response.status_code == 200
    unset_response = client.patch("/api/contacts/1", json={"recruiter_type": None})
    assert unset_response.status_code == 200
    assert contacts.updates == [
        (1, {"recruiter_type": "high"}),
        (1, {"recruiter_type": None}),
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


def test_log_contact_email_uses_supplied_date(monkeypatch: pytest.MonkeyPatch) -> None:
    emails = FakeContactEmailsRepo(contact_ids={1})
    client = _contacts_client(monkeypatch, emails=emails)

    response = client.post("/api/contacts/1/emails", json={"sent_at": "2026-09-20T15:30:00Z"})
    assert response.status_code == 201
    assert emails.added == [(1, "2026-09-20T15:30:00+00:00")]


def test_log_contact_email_converts_offset_timestamp_to_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emails = FakeContactEmailsRepo(contact_ids={1})
    client = _contacts_client(monkeypatch, emails=emails)

    response = client.post("/api/contacts/1/emails", json={"sent_at": "2026-09-20T10:00:00-05:00"})
    assert response.status_code == 201
    assert emails.added == [(1, "2026-09-20T15:00:00+00:00")]


def test_log_contact_email_defaults_to_today_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    emails = FakeContactEmailsRepo(contact_ids={1})
    client = _contacts_client(monkeypatch, emails=emails)

    response = client.post("/api/contacts/1/emails", json={})
    assert response.status_code == 201
    assert emails.added[0][1].endswith("+00:00")
    assert "T" in emails.added[0][1]


@pytest.mark.parametrize(
    "bad_date",
    ["2026-09-20", "2026-09-20T10:00:00", "2026-13-40T10:00:00Z", "yesterday", 20260920],
)
def test_log_contact_email_rejects_bad_timestamp(
    monkeypatch: pytest.MonkeyPatch, bad_date: object
) -> None:
    emails = FakeContactEmailsRepo(contact_ids={1})
    client = _contacts_client(monkeypatch, emails=emails)

    response = client.post("/api/contacts/1/emails", json={"sent_at": bad_date})
    assert response.status_code == 400
    assert emails.added == []


def test_log_contact_email_unknown_contact_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _contacts_client(monkeypatch)

    response = client.post("/api/contacts/1/emails", json={})
    assert response.status_code == 404


def test_set_and_clear_email_response(monkeypatch: pytest.MonkeyPatch) -> None:
    emails = FakeContactEmailsRepo(email_ids={7})
    client = _contacts_client(monkeypatch, emails=emails)

    set_response = client.patch(
        "/api/contact-emails/7", json={"response_received_at": "2026-09-22T09:15:00Z"}
    )
    assert set_response.status_code == 200
    assert emails.responses == {7: "2026-09-22T09:15:00+00:00"}

    clear_response = client.patch("/api/contact-emails/7", json={"response_received_at": None})
    assert clear_response.status_code == 200
    assert emails.responses == {7: None}


def test_email_response_body_must_state_the_field(monkeypatch: pytest.MonkeyPatch) -> None:
    emails = FakeContactEmailsRepo(email_ids={7})
    client = _contacts_client(monkeypatch, emails=emails)

    assert client.patch("/api/contact-emails/7", json={}).status_code == 400
    assert emails.responses == {}


def test_email_log_entry_unknown_id_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _contacts_client(monkeypatch)

    patch_response = client.patch("/api/contact-emails/1", json={"response_received_at": None})
    assert patch_response.status_code == 404
    assert client.delete("/api/contact-emails/1").status_code == 404


def test_delete_contact_email(monkeypatch: pytest.MonkeyPatch) -> None:
    emails = FakeContactEmailsRepo(email_ids={7})
    client = _contacts_client(monkeypatch, emails=emails)

    assert client.delete("/api/contact-emails/7").status_code == 200
    assert emails.deleted == [7]


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
