# Code Review - 2026-06-25

Scope: whole-project review of the Flask backend, repository layer, Google integrations, React/Vite frontend, browser extension surface, and tests. No application code changes were made.

## Findings

### High - `/api/system/health` reports DB failure for the current connection wrappers

- Location: `routes/system.py:60`, `routes/system.py:63`
- Impact: the health endpoint calls `repos["conn"].execute("SELECT 1")`, but the project connection wrappers expose `cursor()`, `commit()`, `rollback()`, and `close()`. `_SQLiteConnection` and `_ReconnectingPgConn` do not expose `execute()`. Any deployment or local launcher using `/api/system/health` can see a false `503` even while normal repository operations work.
- Evidence: `backend/db.py` connection wrappers define `cursor()` but no matching `execute()` method.
- Plan: change the health probe to use `cursor().execute("SELECT 1")`, or add a small public `execute()` method to both connection wrappers. Add a Flask route test for `/api/system/health` against a temporary SQLite DB so this stays covered.

### High - test suite currently fails, so regression signal is degraded

- Location: `tests/test_jobs_description_route.py:267`, `tests/test_jobs_description_route.py:276`, `routes/jobs.py:395`, `routes/jobs.py:417`
- Impact: `test_return_to_queue_restores_added_job_and_removes_auto_applied_stage` still builds a fake repo dict without `job_status_history`, but `return_job_to_queue` now writes status history. The route test returns `500` from `KeyError`, so this workflow no longer has a passing route-level regression test.
- Plan: update the fake repository fixture to include a `job_status_history` fake and assert the expected history row.

- Location: `tests/test_scan_feedback.py:64`, `tests/test_scan_feedback.py:88`, `routes/scan.py:251`
- Impact: `test_noise_feedback_does_not_skip_entire_email` patches `EmailParser` with a fake class that does not accept the current `EmailParser(client=...)` constructor. The scan aborts before exercising the intended behavior.
- Plan: update `FakeEmailParser` to accept `*args, **kwargs`, then assert the scan still creates both jobs and marks the source email scanned.

### Medium - SQLite migration/default timestamps still use local-time SQL

- Location: `backend/db.py:279`, `backend/db.py:283`, `backend/db.py:284`, `backend/db.py:285`, `backend/db.py:286`, `backend/db.py:2009`, `backend/db.py:2018`, `backend/db.py:2031`, `backend/db.py:2038`, `backend/db.py:2045`, `backend/db.py:2335`, `backend/db.py:2414`, `backend/db.py:2437`
- Impact: project standards require internal timestamps and dates to be UTC. The SQLite translation layer maps Postgres UTC expressions to `date('now')` / `datetime('now')`, and several SQLite migrations/defaults use those expressions directly. SQLite's `now` is UTC in typical builds, but the code does not make that contract explicit and differs from the Python-side `datetime.now(UTC)` pattern. This is risky during the SQLite-to-Postgres migration because timestamp semantics are already split across two dialect paths.
- Plan: standardize SQLite generated timestamps with explicit UTC intent, preferably by passing Python-generated UTC values for migrations/backfills where practical and documenting/isolating any remaining SQLite UTC SQL helper. Add focused tests for date defaults/backfills around UTC boundaries if this code remains active.

### Medium - Google Sheets `read_jobs` swallows API failures as an empty sheet

- Location: `backend/sheets_client.py:283`, `backend/sheets_client.py:325`, `backend/sheets_client.py:327`
- Impact: `SheetsClient.read_jobs()` catches `HttpError`, logs a warning, and returns `[]`. Callers doing duplicate detection or sheet mirroring can treat a transient Sheets outage as "no tracked jobs", which can create duplicate queue entries or hide integration failures from the UI. This also conflicts with the Google standards that service clients should log and re-raise `HttpError`, leaving routes to map it to a sanitized HTTP response.
- Plan: re-raise `HttpError` from `read_jobs()`. Update routes/services that call it to catch `NotAuthenticatedError` and `HttpError` explicitly and return `401` or `502` with sanitized messages.

### Medium - Application sheet panel performs a mutating repair POST on mount

- Location: `frontend/src/components/ApplicationSheetPanel.jsx:409`, `routes/sheet.py:534`
- Impact: opening the application sheet automatically calls `/api/sheet/repair/normalize-stage-statuses` with `POST`. In React 18 StrictMode, mount effects run twice in development, so the repair can execute twice. More importantly, a normal read-only view has an implicit write side effect, which makes debugging and user intent less clear.
- Plan: either move this repair into a startup/migration path, make it an explicit user/admin action, or guard the effect with a `useRef(false)` and expose any error instead of silently swallowing it.

### Low - OAuth pending-state memory has no expiry or cap

- Location: `routes/auth.py:52`, `routes/auth.py:93`, `routes/auth.py:105`
- Impact: every `/api/auth/google` call stores a state verifier in `_pending_auths` until the callback consumes it. Abandoned flows never expire. In normal personal use this is small, but repeated reconnect attempts or automated hits can grow process memory and leave stale state tokens around longer than needed.
- Plan: store creation timestamps with pending auth state, expire old entries on each new auth start/callback, and cap the dict size.

### Low - setup auth polling can create overlapping intervals

- Location: `frontend/src/components/SetupWizardPanel.jsx:156`, `frontend/src/components/SetupWizardPanel.jsx:158`, `frontend/src/components/SetupWizardPanel.jsx:179`
- Impact: `startAuthPolling()` assigns `authPollRef.current = setInterval(...)` without clearing any existing interval first. Re-clicking connect or re-entering the flow can create multiple active pollers until one sees authentication. This is a small but avoidable source of extra requests and state races.
- Plan: call `clearInterval(authPollRef.current)` before assigning a new interval, and keep the existing cleanup on unmount.

## Verification

- `uv run pytest -q` failed during collection because `tmp/pytest` is not readable in this workspace.
- `uv run pytest tests -q` ran the project tests: `95 passed`, `2 failed`.
- `npm run build` in `frontend/` passed. Vite emitted a chunk-size warning for the main JS bundle.
- `npm run lint` in `frontend/` passed.

## Remediation Plan

1. Fix the health check DB probe and add a route test.
2. Repair the two failing tests so the suite is trustworthy again.
3. Change Sheets service-layer `HttpError` handling to re-raise, then add route-level Google error handling tests.
4. Move or guard the sheet normalization repair so mounting a read panel is not an implicit write.
5. Normalize timestamp generation semantics across SQLite and Postgres migration paths.
6. Add small frontend hardening fixes for OAuth polling intervals.
7. Add auth-state cleanup for abandoned OAuth attempts.
