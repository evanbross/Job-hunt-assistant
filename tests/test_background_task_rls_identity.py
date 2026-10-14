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

"""Two-user RLS-identity regression tests for the background threads and
route-owned Celery tasks fixed by the 2026-09-28 code review
(docs/code-review-2026-09-28-full.md). Each of these functions receives
`user_id` explicitly but was found calling `get_repos()` bare somewhere in
its body -- under Postgres row-level security's fail-closed design
(backend/db/row_level_security.py), that silently returns zero rows instead
of the caller's own data. Each test here seeds a second account's matching
row and proves the function under test both sees the caller's own data (the
fix didn't just make everything invisible) and never reads/writes the other
account's row."""

from __future__ import annotations

from urllib.error import URLError

import psycopg2
import psycopg2.extras
import pytest

import config
from backend.db import get_connection
from backend.db.repositories.user_accounts import PostgresUserAccountRepository
from backend.db.row_level_security import set_current_user
from backend.utils.utils import get_repos

# ai: claude-sonnet-5 | 2026-09-28

_TS = "2026-09-28T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _test_db(pg_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DATABASE_URL", pg_dsn)


def _new_user(conn, email: str) -> int:
    account = PostgresUserAccountRepository(conn).create_pending(email, f"seed-{email}", "2099-01-01")
    conn.commit()
    return account["id"]


class TwoUsers:
    """A real owner + other account in Postgres, RLS armed. `owner_id`/`other_id`
    are ready to use with `get_repos(...)`; `raw` is a superuser connection for
    seeding/inspecting rows across both accounts without RLS getting in the way."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        conn = get_connection(dsn)
        self.owner_id = _new_user(conn, "owner@example.com")
        self.other_id = _new_user(conn, "other@example.com")
        conn.close()
        self.raw = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)
        self.raw.autocommit = True

    def close(self) -> None:
        self.raw.close()

    def insert_job(self, user_id: int, **overrides: object) -> int:
        row = {
            "dedup_key": f"key-{user_id}-{overrides.get('url', '')}",
            "company": "Acme",
            "job_title": "Engineer",
            "url": "https://example.com/j",
            "source": "email",
            "status": "pending_review",
            "date_found": "2026-09-28",
        }
        row.update(overrides)
        with get_repos(user_id) as repos:
            job_id = repos["jobs"].insert(user_id, row)
            repos["conn"].commit()
        return job_id

    def job_row(self, job_id: int, *, as_user_id: int) -> dict:
        """The non-superuser `raw` connection is itself RLS-armed -- reading
        any tenant table through it needs the same identity GUC `get_repos`
        would set, or a fail-closed policy hides every row from it too."""
        cur = self.raw.cursor()
        set_current_user(cur, as_user_id)
        cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
        return dict(cur.fetchone())

    def set_setting(self, user_id: int, key: str, value: str) -> None:
        cur = self.raw.cursor()
        set_current_user(cur, user_id)
        cur.execute(
            "INSERT INTO user_settings (user_id, key, value) VALUES (%s, %s, %s)"
            " ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value",
            (user_id, key, value),
        )


@pytest.fixture
def two_users(pg_dsn: str):
    users = TwoUsers(pg_dsn)
    yield users
    users.close()


# ─── _run_wttj_repair (routes/jobs.py) ───────────────────────────────────────


def test_run_wttj_repair_only_sees_the_calling_users_jobs(
    two_users: TwoUsers, monkeypatch: pytest.MonkeyPatch
) -> None:
    from routes import jobs as jobs_route

    owner_job = two_users.insert_job(
        two_users.owner_id,
        source="Welcome to the Jungle",
        url="https://www.welcometothejungle.com/jobs/owner-job",
    )
    other_job = two_users.insert_job(
        two_users.other_id,
        source="Welcome to the Jungle",
        url="https://www.welcometothejungle.com/jobs/other-job",
    )
    other_before = two_users.job_row(other_job, as_user_id=two_users.other_id)

    # No real network fetch: force the title-verification fetch to fail fast
    # so the candidate is counted (proving the read boundary is correct)
    # without needing real WTTJ HTML.
    monkeypatch.setattr(
        jobs_route,
        "_fetch_wttj_page_html",
        lambda url: (_ for _ in ()).throw(URLError("blocked in test")),
    )

    jobs_route._run_wttj_repair(two_users.owner_id)

    state = jobs_route._wttj_repair_state
    assert state.total == 1, "should only count the owner's one WTTJ job, not both accounts'"
    assert [f["id"] for f in state.failed] == [owner_job]
    assert (
        two_users.job_row(other_job, as_user_id=two_users.other_id) == other_before
    ), "other account's job must be untouched"


# ─── _run_import_repair (routes/jobs.py) ─────────────────────────────────────


def test_run_import_repair_only_sees_the_calling_users_jobs(
    two_users: TwoUsers, monkeypatch: pytest.MonkeyPatch
) -> None:
    from routes import jobs as jobs_route

    # No company -> normalize_and_validate() flags "missing_company", which is
    # what routes the job into the repair loop's get_repos(user_id) block.
    two_users.insert_job(two_users.owner_id, company="", job_title="Missing Co Job")
    other_job = two_users.insert_job(two_users.other_id, company="", job_title="Missing Co Job")
    other_before = two_users.job_row(other_job, as_user_id=two_users.other_id)

    monkeypatch.setattr(jobs_route, "_write_repair_findings_doc", lambda *a, **k: None)

    jobs_route._run_import_repair(two_users.owner_id)

    state = jobs_route._import_repair_state
    assert state.total == 1, "should only count the owner's one candidate job, not both accounts'"
    assert state.error is None
    assert (
        two_users.job_row(other_job, as_user_id=two_users.other_id) == other_before
    ), "other account's job must be untouched"


# ─── run_board_search_task (routes/board_search.py, Celery task) ────────────


class _FakeJobBoardSearcherNoResults:
    def search_all(self, search_queries: list[str]) -> list:
        return []


def test_run_board_search_task_reads_only_the_calling_users_jobs(
    two_users: TwoUsers, monkeypatch: pytest.MonkeyPatch
) -> None:
    from routes import board_search as board_search_route

    owner_job = two_users.insert_job(two_users.owner_id, company="OwnerCo")
    other_job = two_users.insert_job(two_users.other_id, company="OtherCo")
    other_before = two_users.job_row(other_job, as_user_id=two_users.other_id)

    # No AI call for query generation, no real network board search -- this
    # test is only about whether the read phase (jobs/resume/penalties) sees
    # the right account's rows.
    monkeypatch.setattr(
        board_search_route, "_generate_board_search_queries", lambda user_id: ["x"]
    )
    monkeypatch.setattr(board_search_route, "JobBoardSearcher", _FakeJobBoardSearcherNoResults)

    result = board_search_route.run_board_search_task.apply(args=(two_users.owner_id,)).result

    assert result["errors"] == []
    assert result["candidates_found"] == 0
    assert (
        two_users.job_row(other_job, as_user_id=two_users.other_id) == other_before
    ), "other account's job must be untouched"
    # owner_job untouched too (board search never mutates existing rows) -- the
    # real assertion is that reaching this point required the read phase to
    # succeed without raising for the owner's own connection.
    assert two_users.job_row(owner_job, as_user_id=two_users.owner_id)["company"] == "OwnerCo"


# ─── run_career_site_scan_task (routes/career_sites.py, Celery task) ────────


class _FakeCareerSiteScannerNoJobs:
    def scan_site(self, *, site_name: str, site_url: str, scan_offset: int = 0) -> dict:
        return {"jobs": [], "detected_platform": "generic", "next_scan_offset": 0}


class _FakeFitScorerForCareerScan:
    def __init__(self, user_id: int) -> None:
        pass


def test_run_career_site_scan_task_only_scans_the_calling_users_sources(
    two_users: TwoUsers, monkeypatch: pytest.MonkeyPatch
) -> None:
    from routes import career_sites as career_sites_route

    with get_repos(two_users.owner_id) as repos:
        repos["career_sites"].insert(two_users.owner_id, {"name": "Owner Careers", "url": "https://owner.example/careers"})
        repos["conn"].commit()
    with get_repos(two_users.other_id) as repos:
        repos["career_sites"].insert(two_users.other_id, {"name": "Other Careers", "url": "https://other.example/careers"})
        repos["conn"].commit()

    monkeypatch.setattr(career_sites_route, "CareerSiteScanner", _FakeCareerSiteScannerNoJobs)
    monkeypatch.setattr(career_sites_route, "FitScorer", _FakeFitScorerForCareerScan)

    result = career_sites_route.run_career_site_scan_task.apply(
        args=(two_users.owner_id,), kwargs={"trigger": "manual"}
    ).result

    assert result["sites_total"] == 1, (
        "should only see the owner's one enabled career site, not both accounts'"
    )
    assert result["jobs_found"] == 0


# ─── jd_health_check_should_run_now (backend/scanning/schedulers.py) ────────
# Polled from the health-check scheduler's daemon thread, no Flask session --
# the very first bug this whole file exists to catch a repeat of.


def test_jd_health_check_should_run_now_only_sees_the_calling_users_settings(
    two_users: TwoUsers,
) -> None:
    from backend.scanning.schedulers import jd_health_check_should_run_now

    with get_repos(two_users.owner_id) as repos:
        repos["jd_health_targets"].insert(
            two_users.owner_id,
            {"platform": "greenhouse", "label": "Owner target", "url": "https://example.com/o"},
        )
        repos["settings"].set(two_users.owner_id, "jd_health_check_enabled", "true")
        repos["conn"].commit()
    with get_repos(two_users.other_id) as repos:
        repos["jd_health_targets"].insert(
            two_users.other_id,
            {"platform": "greenhouse", "label": "Other target", "url": "https://example.com/x"},
        )
        repos["settings"].set(two_users.other_id, "jd_health_check_enabled", "false")
        repos["conn"].commit()

    assert jd_health_check_should_run_now(two_users.owner_id) is True, (
        "owner enabled it with a target and no prior run -- should be overdue"
    )
    assert jd_health_check_should_run_now(two_users.other_id) is False, (
        "other disabled it -- must not run because of the owner's setting"
    )


# ─── _generate_board_search_queries (routes/board_search.py) ────────────────
# Also only ever called from the Celery task above, no Flask session.


def test_generate_board_search_queries_reads_only_the_calling_users_profile(
    two_users: TwoUsers, monkeypatch: pytest.MonkeyPatch
) -> None:
    from routes import board_search as board_search_route

    with get_repos(two_users.owner_id) as repos:
        repos["candidate_profiles"].upsert(
            two_users.owner_id, "Owner profile: Python engineer.", _TS
        )
        repos["conn"].commit()
    with get_repos(two_users.other_id) as repos:
        repos["candidate_profiles"].upsert(
            two_users.other_id, "Other profile: designer.", _TS
        )
        repos["conn"].commit()

    captured_profiles: list[str] = []

    def fake_complete(self, *, max_tokens: int, messages: list) -> str:
        captured_profiles.append(messages[0].content)
        return "query one\nquery two"

    monkeypatch.setattr(
        board_search_route.AIProviderClient, "complete", fake_complete, raising=True
    )

    board_search_route._generate_board_search_queries(two_users.owner_id)

    assert len(captured_profiles) == 1
    assert "Owner profile: Python engineer." in captured_profiles[0]
    assert "Other profile: designer." not in captured_profiles[0]


# ─── _run_manual_login (routes/greenhouse.py) ────────────────────────────────


class _FakeGreenhouseSearcher:
    """Records the email it was constructed with instead of launching a real
    Playwright browser, so the test can prove which account's setting was read."""

    captured_emails: list[object] = []

    def __init__(self, *, email: object, session_store: object) -> None:
        type(self).captured_emails.append(email)

    def manual_login(self, *, timeout_seconds: int) -> None:
        pass


def test_run_manual_login_reads_only_the_calling_users_email_setting(
    two_users: TwoUsers, monkeypatch: pytest.MonkeyPatch
) -> None:
    from routes import greenhouse as greenhouse_route

    two_users.set_setting(two_users.owner_id, "greenhouse_email", "owner@gh.example.com")
    two_users.set_setting(two_users.other_id, "greenhouse_email", "other@gh.example.com")
    _FakeGreenhouseSearcher.captured_emails = []
    monkeypatch.setattr(greenhouse_route, "GreenhouseSearcher", _FakeGreenhouseSearcher)

    greenhouse_route._run_manual_login(two_users.owner_id)

    assert _FakeGreenhouseSearcher.captured_emails == ["owner@gh.example.com"]
    assert greenhouse_route._login_state.error is None


# ─── run_greenhouse_scan_task (routes/greenhouse.py, Celery task) ────────────


class _FakeGreenhouseSearcherNoJobs(_FakeGreenhouseSearcher):
    def scrape(self, **kwargs: object) -> list:
        return []


def test_run_greenhouse_scan_task_reads_only_the_calling_users_email_setting(
    two_users: TwoUsers, monkeypatch: pytest.MonkeyPatch
) -> None:
    from routes import greenhouse as greenhouse_route

    two_users.set_setting(two_users.owner_id, "greenhouse_email", "owner@gh.example.com")
    two_users.set_setting(two_users.other_id, "greenhouse_email", "other@gh.example.com")
    _FakeGreenhouseSearcherNoJobs.captured_emails = []
    monkeypatch.setattr(greenhouse_route, "GreenhouseSearcher", _FakeGreenhouseSearcherNoJobs)

    result = greenhouse_route.run_greenhouse_scan_task.apply(
        args=(two_users.owner_id, "https://boards.greenhouse.io/x", 10)
    ).result

    assert _FakeGreenhouseSearcherNoJobs.captured_emails == ["owner@gh.example.com"]
    assert result["error"] is None


# ─── run_re_evaluate_task (routes/fit.py, Celery task) ───────────────────────


class _FakeFitScorer:
    """No Anthropic call: scores every job identically so the test can focus
    on which jobs the task saw and rescored, not on scoring quality."""

    scored_calls: list[tuple[str, str]] = []

    def __init__(self, user_id: int) -> None:
        pass

    def score(self, *, job_title: str, company: str, **kwargs: object) -> dict:
        type(self).scored_calls.append((company, job_title))
        return {"score": 80, "reasoning": "ok", "salary_min": None, "salary_max": None}


def test_run_re_evaluate_task_only_rescoresthe_calling_users_jobs(
    two_users: TwoUsers, monkeypatch: pytest.MonkeyPatch
) -> None:
    from routes import fit as fit_route

    owner_job = two_users.insert_job(two_users.owner_id, company="OwnerCo", job_title="Owner Job")
    other_job = two_users.insert_job(two_users.other_id, company="OtherCo", job_title="Other Job")
    other_before = two_users.job_row(other_job, as_user_id=two_users.other_id)

    _FakeFitScorer.scored_calls = []
    monkeypatch.setattr(fit_route, "FitScorer", _FakeFitScorer)

    result = fit_route.run_re_evaluate_task.apply(args=(two_users.owner_id,)).result

    assert _FakeFitScorer.scored_calls == [("OwnerCo", "Owner Job")], (
        "should only rescore the owner's job, not both accounts'"
    )
    assert result["result"]["rescored"] == 1
    owner_after = two_users.job_row(owner_job, as_user_id=two_users.owner_id)
    assert owner_after["fit_score"] == 80
    assert (
        two_users.job_row(other_job, as_user_id=two_users.other_id) == other_before
    ), "other account's job must be untouched"


# ─── _run_rescan_broken_imports (routes/jobs.py) ─────────────────────────────


class _FakeGmailClientNoNetwork:
    """Stands in for GmailClient so the rescan loop's candidate-selection
    (the get_repos(user_id) call under test) runs for real while the actual
    Gmail re-fetch fails fast and cleanly, without any network access."""

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id

    def get_message(self, message_id: str) -> object:
        raise RuntimeError("no network in test")


def test_run_rescan_broken_imports_only_sees_the_calling_users_jobs(
    two_users: TwoUsers, monkeypatch: pytest.MonkeyPatch
) -> None:
    from routes import jobs as jobs_route

    # A non-http(s) url with a non-empty dedup_key is what _rescan_import_candidates
    # selects.
    owner_job = two_users.insert_job(
        two_users.owner_id, url="broken-owner", dedup_key="broken-owner-msg-1"
    )
    other_job = two_users.insert_job(
        two_users.other_id, url="broken-other", dedup_key="broken-other-msg-1"
    )
    other_before = two_users.job_row(other_job, as_user_id=two_users.other_id)

    monkeypatch.setattr(jobs_route, "GmailClient", _FakeGmailClientNoNetwork)

    jobs_route._run_rescan_broken_imports(two_users.owner_id)

    state = jobs_route._rescan_import_state
    assert state.total == 1, "should only count the owner's one broken job, not both accounts'"
    assert [u["id"] for u in state.unresolved] == [owner_job]
    assert state.error is None
    assert (
        two_users.job_row(other_job, as_user_id=two_users.other_id) == other_before
    ), "other account's job must be untouched"
