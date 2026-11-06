# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-09-29
"""routes/import_.py's reset_import and extract_imported_job_urls -- covers
the 2026-09-29 Pydantic validation cleanup. import_and_queue_all_jobs is
deliberately left out of this pass (see routes/import_.py's comment on that
route): it forwards its body straight to queue_import_capture(), which is
also called from a non-Flask listener thread with a raw dict and has a
large, not-fully-enumerable field surface (browser-extension capture
payloads) -- validating it risks silently breaking a legitimate capture."""

from __future__ import annotations

from typing import Any

import pytest
from flask import Flask

import config
from backend.db import (
    PostgresJobRepository,
    PostgresUserAccountRepository,
    get_connection,
)
from backend.db.row_level_security import set_current_user
from backend.scanning.import_progress import ImportProgress, ImportProgressState
from routes.import_ import import_bp
from tests.db_roles import runtime_database_url


def _make_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(import_bp, url_prefix="/api")
    return app


# ai: claude-opus-5-5 | 2026-10-14 -- reset is now scoped to the session's
# user, so tracked entries need a real owner and the client a session.
_RESET_USER_ID = 1


def _tracked(user_id: int = _RESET_USER_ID) -> ImportProgressState:
    return ImportProgressState(
        running=False,
        phase="done",
        progress=100,
        message="",
        trigger="ui",
        started_at="2026-10-14T00:00:00+00:00",
        user_id=user_id,
    )


def _logged_in_client(user_id: int = _RESET_USER_ID):
    client = _make_app().test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user_id
    return client


# ai: claude-opus-5-5 | 2026-11-06 -- through the shared store (backend/scanning/import_progress.py)
# that replaced routes/import_.py's in-process dict; conftest gives each test an empty one.
def _tracked_ids(progress: ImportProgress, *ids: str, user_id: int = _RESET_USER_ID) -> set[str]:
    return {import_id for import_id in ids if progress.find(import_id, user_id) is not None}


def test_reset_import_clears_one_tracked_import(isolated_import_progress: ImportProgress) -> None:
    isolated_import_progress.put("abc", _tracked())
    isolated_import_progress.put("xyz", _tracked())

    client = _logged_in_client()
    response = client.post("/api/import/reset", json={"import_id": "abc"})

    assert response.status_code == 200
    assert _tracked_ids(isolated_import_progress, "abc", "xyz") == {"xyz"}


def test_reset_import_without_id_clears_everything_the_caller_owns(
    isolated_import_progress: ImportProgress,
) -> None:
    isolated_import_progress.put("abc", _tracked())
    isolated_import_progress.put("def", _tracked())

    client = _logged_in_client()
    response = client.post("/api/import/reset", json={})

    assert response.status_code == 200
    assert _tracked_ids(isolated_import_progress, "abc", "def") == set()


# ai: claude-opus-5-5 | 2026-10-14
def test_reset_import_never_clears_another_users_imports(
    isolated_import_progress: ImportProgress,
) -> None:
    other = _RESET_USER_ID + 1
    isolated_import_progress.put("mine", _tracked())
    isolated_import_progress.put("theirs", _tracked(user_id=other))

    client = _logged_in_client()
    client.post("/api/import/reset", json={"import_id": "theirs"})
    assert _tracked_ids(isolated_import_progress, "theirs", user_id=other) == {"theirs"}

    client.post("/api/import/reset", json={})
    assert _tracked_ids(isolated_import_progress, "mine") == set()
    assert _tracked_ids(isolated_import_progress, "theirs", user_id=other) == {"theirs"}


def test_reset_import_falls_back_to_query_arg(isolated_import_progress: ImportProgress) -> None:
    isolated_import_progress.put("abc", _tracked())
    isolated_import_progress.put("xyz", _tracked())

    client = _logged_in_client()
    response = client.post("/api/import/reset?import_id=abc", json={})

    assert response.status_code == 200
    assert _tracked_ids(isolated_import_progress, "abc", "xyz") == {"xyz"}


def test_reset_import_coerces_non_string_import_id_to_string(
    isolated_import_progress: ImportProgress,
) -> None:
    """Matches the pre-Pydantic route's str(...) coercion exactly -- a
    numeric import_id is stringified, not rejected."""
    isolated_import_progress.put("123", _tracked())

    client = _logged_in_client()
    response = client.post("/api/import/reset", json={"import_id": 123})

    assert response.status_code == 200
    assert _tracked_ids(isolated_import_progress, "123") == set()


def test_extract_job_urls_returns_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("routes.import_.dump_import_capture", lambda body: None)

    class FakeService:
        def analyze_capture(self, content, content_format, *, base_url):
            assert content == "some html"
            assert content_format == "html"
            assert base_url == "https://example.com/jobs"
            return {"urls": ["https://example.com/jobs/1"]}

    monkeypatch.setattr("routes.import_.CapturedPageImportService", FakeService)

    client = _make_app().test_client()
    response = client.post(
        "/api/import/job-urls",
        json={
            "content": "some html",
            "format": "html",
            "url": "https://example.com/jobs",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {"urls": ["https://example.com/jobs/1"]}


def test_extract_job_urls_defaults_format_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("routes.import_.dump_import_capture", lambda body: None)

    class FakeService:
        def analyze_capture(self, content, content_format, *, base_url):
            assert content_format == "auto"
            assert base_url is None
            return {"urls": []}

    monkeypatch.setattr("routes.import_.CapturedPageImportService", FakeService)

    client = _make_app().test_client()
    response = client.post("/api/import/job-urls", json={"content": "x"})

    assert response.status_code == 200


def test_extract_job_urls_surfaces_analyze_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("routes.import_.dump_import_capture", lambda body: None)

    class FakeService:
        def analyze_capture(self, content, content_format, *, base_url):
            raise ValueError("nothing to extract")

    monkeypatch.setattr("routes.import_.CapturedPageImportService", FakeService)

    client = _make_app().test_client()
    response = client.post("/api/import/job-urls", json={"content": "x"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "nothing to extract"


# ─── /repair/fix-unknown-companies ───────────────────────────────────────────


def _new_user(conn: Any, email: str = "owner@example.com") -> int:
    account = PostgresUserAccountRepository(conn).create_pending(
        email, f"seed-{email}", "2099-01-01"
    )
    set_current_user(conn.cursor(), account["id"])
    return account["id"]


_JOBGETHER_JOBPOSTING_HTML = """
<html><head>
<script type="application/ld+json">
{"@type": "JobPosting", "title": "Senior Engineer",
 "hiringOrganization": {"name": "TestCo"}}
</script>
</head><body></body></html>
"""


@pytest.fixture
def repair_client(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, int]:
    monkeypatch.setattr(config, "DATABASE_URL", runtime_database_url(pg_dsn))
    conn = get_connection(pg_dsn)
    try:
        user_id = _new_user(conn)
        conn.commit()
    finally:
        conn.close()
    flask_app = Flask(__name__)
    flask_app.secret_key = "test"
    flask_app.register_blueprint(import_bp, url_prefix="/api")
    client = flask_app.test_client()
    # get_repos() (backend/db/row_level_security.py) reads the real Flask session.
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
    return client, user_id


def test_fix_unknown_companies_repairs_jobgether_row_via_live_page_fetch(
    repair_client: tuple[Any, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Jobgether job with a blank company (e.g. job 6071/EnergyHub, captured
    2026-10-08 — the extension's client-side extraction came up empty) must be
    repaired from the live offer page's schema.org JobPosting, same as the
    one-off scripts/repair_jobgether_companies.py script does."""
    client, user_id = repair_client
    conn = get_connection(config.DATABASE_URL)
    try:
        set_current_user(conn.cursor(), user_id)

        job_id = PostgresJobRepository(conn).insert(
            user_id,
            {
                "company": "",
                "job_title": "Senior Engineer",
                "url": "https://jobgether.com/offer/abc123-senior-engineer",
                "source": "browser_ext@local",
                "status": "triage",
            },
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        "routes.import_.fetch_jobgether_page_html",
        lambda url: _JOBGETHER_JOBPOSTING_HTML,
    )

    response = client.post("/api/repair/fix-unknown-companies")

    assert response.status_code == 200
    assert response.get_json() == {"fixed": 1, "remaining": 0}

    conn = get_connection(config.DATABASE_URL)
    try:
        set_current_user(conn.cursor(), user_id)

        updated = PostgresJobRepository(conn).get_by_id(user_id, job_id)
    finally:
        conn.close()
    assert updated["company"] == "TestCo"


def test_fix_unknown_companies_still_repairs_non_jobgether_rows(
    repair_client: tuple[Any, int],
) -> None:
    """Non-Jobgether missing-company rows must still go through the existing
    (fast, no-network) ATS-URL repair path unaffected by the Jobgether branch."""
    client, user_id = repair_client
    conn = get_connection(config.DATABASE_URL)
    try:
        set_current_user(conn.cursor(), user_id)

        job_id = PostgresJobRepository(conn).insert(
            user_id,
            {
                "company": "",
                "job_title": "Engineer",
                "url": "https://boards.greenhouse.io/acmeco/jobs/1",
                "source": "browser_ext@local",
                "status": "triage",
            },
        )
        conn.commit()
    finally:
        conn.close()

    response = client.post("/api/repair/fix-unknown-companies")

    assert response.status_code == 200
    assert response.get_json() == {"fixed": 1, "remaining": 0}

    conn = get_connection(config.DATABASE_URL)
    try:
        set_current_user(conn.cursor(), user_id)

        updated = PostgresJobRepository(conn).get_by_id(user_id, job_id)
    finally:
        conn.close()
    assert updated["company"] == "Acmeco"


# ai: claude-sonnet-5 | 2026-10-09
def test_fix_unknown_companies_does_not_overwrite_concurrent_user_correction(
    repair_client: tuple[Any, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-10-08 review: the Jobgether branch reads the row, releases the
    connection, then does a slow live-page fetch before writing -- a real
    window for a concurrent user correction. Simulate that correction landing
    while the fetch is "in flight" (a second connection commits it from inside
    the monkeypatched fetch) and assert the stale fetched value never
    overwrites it."""
    client, user_id = repair_client
    conn = get_connection(config.DATABASE_URL)
    try:
        set_current_user(conn.cursor(), user_id)
        job_id = PostgresJobRepository(conn).insert(
            user_id,
            {
                "company": "",
                "job_title": "Senior Engineer",
                "url": "https://jobgether.com/offer/abc123-senior-engineer",
                "source": "browser_ext@local",
                "status": "triage",
            },
        )
        conn.commit()
    finally:
        conn.close()

    def _fetch_with_concurrent_correction(url: str) -> str:
        race_conn = get_connection(config.DATABASE_URL)
        try:
            set_current_user(race_conn.cursor(), user_id)
            PostgresJobRepository(race_conn).update(
                user_id, job_id, {"company": "UserCorrectedCo"}
            )
            race_conn.commit()
        finally:
            race_conn.close()
        return _JOBGETHER_JOBPOSTING_HTML

    monkeypatch.setattr(
        "routes.import_.fetch_jobgether_page_html", _fetch_with_concurrent_correction
    )

    response = client.post("/api/repair/fix-unknown-companies")

    assert response.status_code == 200
    assert response.get_json() == {"fixed": 0, "remaining": 0}

    conn = get_connection(config.DATABASE_URL)
    try:
        set_current_user(conn.cursor(), user_id)
        updated = PostgresJobRepository(conn).get_by_id(user_id, job_id)
    finally:
        conn.close()
    assert updated["company"] == "UserCorrectedCo"
