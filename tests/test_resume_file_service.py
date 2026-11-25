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
from io import BytesIO
from typing import Any

import fitz
import pytest
from cryptography.fernet import Fernet
from flask import Flask

import backend.crypto as crypto
import config
from backend.db import (
    PostgresResumeRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user
from backend.resume.resume_file_service import (
    MAX_RESUME_BYTES,
    InvalidResumeFileError,
    ResumeFileService,
    extract_pdf_text,
    validate_pdf_upload,
)
from routes.settings import settings_bp
from tests.db_roles import runtime_database_url
from tests.login_session import sign_in
from web.extension_listener import get_resume_file_bytes

# ai: claude-sonnet-5 | 2026-09-26


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """A fresh Fernet key per test, with the module-level cache reset so the
    tests neither depend on nor leak the developer's real key."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", key)
    monkeypatch.setattr(crypto, "_fernet", None)
    return key


def make_pdf(text: str = "Evan Ross - Senior Engineer") -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _new_user(conn: Any, email: str = "owner@example.com") -> int:
    account = PostgresUserAccountRepository(conn).create_pending(
        email, f"seed-{email}", "2099-01-01"
    )
    set_current_user(conn.cursor(), account["id"])
    return account["id"]


# ─── validation and extraction ──────────────────────────────────────────────────


def test_extract_pdf_text_reads_from_memory() -> None:
    assert "Senior Engineer" in extract_pdf_text(make_pdf())


@pytest.mark.parametrize(
    ("data", "message"),
    [
        pytest.param(b"", "empty", id="empty"),
        pytest.param(b"not a pdf at all", "not a valid PDF", id="not-a-pdf"),
        pytest.param(b"%PDF-" + b"x" * MAX_RESUME_BYTES, "5 MB or smaller", id="oversized"),
    ],
)
def test_validate_pdf_upload_rejects_bad_input(data: bytes, message: str) -> None:
    with pytest.raises(InvalidResumeFileError, match=message):
        validate_pdf_upload(data)


def test_extract_pdf_text_rejects_a_corrupt_pdf() -> None:
    with pytest.raises(InvalidResumeFileError, match="could not be read"):
        extract_pdf_text(b"%PDF-1.7 this is not really a pdf")


# ─── storage ────────────────────────────────────────────────────────────────────


def test_replace_then_load_round_trips_the_original_bytes(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        service = ResumeFileService(PostgresResumeRepository(conn))
        pdf = make_pdf()

        saved = service.replace(user_id, "My Resume.pdf", pdf, "extracted text")
        conn.commit()

        assert saved["has_file"] is True
        assert saved["file_size"] == len(pdf)
        assert service.load_file(user_id) == (pdf, "My Resume.pdf")
    finally:
        conn.close()


def test_the_pdf_is_encrypted_at_rest(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        pdf = make_pdf("A very identifiable resume line")
        ResumeFileService(PostgresResumeRepository(conn)).replace(user_id, "r.pdf", pdf, "t")
        conn.commit()

        cur = conn.cursor()
        cur.execute("SELECT file_encrypted FROM resume WHERE user_id = %s", (user_id,))
        stored = cur.fetchone()["file_encrypted"]
        assert "%PDF" not in stored
        assert "identifiable" not in stored
        assert stored != pdf.decode("latin-1")
    finally:
        conn.close()


def test_each_user_only_ever_loads_their_own_file(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        first_id = _new_user(conn)
        second_id = _new_user(conn, "second@example.com")
        service = ResumeFileService(PostgresResumeRepository(conn))
        first_pdf, second_pdf = make_pdf("first user"), make_pdf("second user")

        set_current_user(conn.cursor(), first_id)
        service.replace(first_id, "first.pdf", first_pdf, "first")
        set_current_user(conn.cursor(), second_id)
        service.replace(second_id, "second.pdf", second_pdf, "second")
        conn.commit()

        set_current_user(conn.cursor(), first_id)
        assert service.load_file(first_id) == (first_pdf, "first.pdf")
        set_current_user(conn.cursor(), second_id)
        assert service.load_file(second_id) == (second_pdf, "second.pdf")
    finally:
        conn.close()


def test_a_new_upload_replaces_the_previous_one(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        service = ResumeFileService(PostgresResumeRepository(conn))
        service.replace(user_id, "old.pdf", make_pdf("old"), "old")
        newer = make_pdf("new")
        service.replace(user_id, "new.pdf", newer, "new")
        conn.commit()

        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM resume WHERE user_id = %s", (user_id,))
        assert cur.fetchone()["n"] == 1
        assert service.load_file(user_id) == (newer, "new.pdf")
    finally:
        conn.close()


def test_get_latest_never_loads_the_encrypted_file(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresResumeRepository(conn)
        ResumeFileService(repo).replace(user_id, "r.pdf", make_pdf(), "the text")

        summary = repo.get_latest(user_id)
        assert "file_encrypted" not in summary
        assert summary["content"] == "the text"
    finally:
        conn.close()


def test_a_resume_with_no_stored_file_reports_has_file_false(pg_dsn: str) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        repo = PostgresResumeRepository(conn)
        repo.insert(user_id, {"filename": "old.pdf", "content": "t", "uploaded_at": "2026-01-01"})

        assert repo.get_latest(user_id)["has_file"] is False
        assert ResumeFileService(repo).load_file(user_id) is None
        assert ResumeFileService(repo).load_file(user_id + 999) is None
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("submitted", "stored"),
    [
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\evan\\resume.pdf", "resume.pdf"),
        ("   ", "resume.pdf"),
        ("x" * 400 + ".pdf", ("x" * 400 + ".pdf")[:255]),
    ],
)
def test_filenames_are_reduced_to_a_safe_basename(
    pg_dsn: str, submitted: str, stored: str
) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        saved = ResumeFileService(PostgresResumeRepository(conn)).replace(
            user_id, submitted, make_pdf(), "t"
        )
        assert saved["filename"] == stored
    finally:
        conn.close()


def test_a_file_encrypted_under_another_key_fails_loudly(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        service = ResumeFileService(PostgresResumeRepository(conn))
        service.replace(user_id, "r.pdf", make_pdf(), "t")

        monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
        monkeypatch.setattr(crypto, "_fernet", None)
        with pytest.raises(ValueError, match="Could not decrypt"):
            service.load_file(user_id)
    finally:
        conn.close()


# ─── HTTP layer and the extension listener callback ─────────────────────────────


@pytest.fixture
def resume_client(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, int]:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        conn.commit()
    finally:
        conn.close()
    flask_app = Flask(__name__)
    flask_app.secret_key = "test"
    flask_app.register_blueprint(settings_bp, url_prefix="/api")
    monkeypatch.setattr("routes.settings.current_user_id", lambda: user_id)
    client = flask_app.test_client()
    # get_repos() (backend/db/row_level_security.py) reads the real Flask session, not
    # the current_user_id() monkeypatch above.
    with client.session_transaction() as sess:
        sign_in(sess, user_id)

    return client, user_id


def _upload(client: Any, data: bytes, filename: str = "resume.pdf") -> Any:
    return client.post(
        "/api/resume", data={"file": (BytesIO(data), filename)}, content_type="multipart/form-data"
    )


def test_upload_route_stores_the_file_and_the_listener_can_serve_it(resume_client: Any) -> None:
    client, user_id = resume_client
    pdf = make_pdf()

    response = _upload(client, pdf)

    assert response.status_code == 201
    assert response.get_json()["has_file"] is True
    assert client.get("/api/resume").get_json()["has_file"] is True
    assert get_resume_file_bytes(user_id) == (pdf, "resume.pdf")
    assert get_resume_file_bytes(user_id + 1) is None


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"just text", id="text"),
        pytest.param(b"%PDF-1.7 corrupt", id="corrupt"),
        pytest.param(b"%PDF-" + b"x" * (MAX_RESUME_BYTES + 10), id="oversized"),
    ],
)
def test_upload_route_rejects_unusable_files(resume_client: Any, data: bytes) -> None:
    client, _ = resume_client

    response = _upload(client, data)

    assert response.status_code == 400
    assert "error" in response.get_json()
    assert client.get("/api/resume").get_json() is None


def test_upload_route_rejects_non_pdf_extensions(resume_client: Any) -> None:
    client, _ = resume_client
    assert _upload(client, make_pdf(), "resume.docx").status_code == 400
