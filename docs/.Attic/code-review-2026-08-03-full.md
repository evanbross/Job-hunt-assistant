# Full Code Review - 2026-08-03

Scope reviewed: Flask startup and blueprint registration, route handlers, repository layer, Celery/background workers, Gmail/OAuth integration, SSRF helpers, React/Vite frontend, browser extension capture path, project config, tests, and build/lint health.

Verification run:

- `uv run ruff check .` - passed.
- `uv run pytest tests -q` - passed, 363 tests passed, 1 skipped.
- `npm run build` in `frontend/` - passed. Vite still reports the existing single-chunk size warning: `assets/index-B59p2KUW.js` is 559.19 kB minified.

Note: `docs/code-review-2026-08-03.md` already existed as an untracked file before this review. This report was written to a new filename to avoid overwriting it.

---

## Findings

### Medium - JD health-check scheduler can keep submitting runs when no result row is recorded

`jd_health_check_should_run_now()` derives the last run from `jd_health_check_results` via `repos["jd_health_results"].get_last_run_at()` (`backend/utils.py:501-532`). The scheduler then submits a task every poll while that check remains due (`backend/utils.py:579-586`). However, `run_jd_health_check_task()` writes result rows only inside the per-target loop (`backend/tasks.py:726-791`). If the feature is enabled with zero enabled targets, the task returns success with `{total: 0}` but writes no result row, so `get_last_run_at()` remains `None` (`backend/db/repositories/jd_health_check_results.py:77-80`) and the scheduler will submit another run every 15 minutes.

Impact: an enabled but empty health-check configuration produces repeated Celery tasks forever. A related edge case exists for long-running checks: until at least one target result commits, the scheduler has no in-flight/run-level marker and can submit another scheduled run on the next poll.

Recommendation: record a run-level heartbeat/history row independent of target results, or track a scheduler in-flight/last-submitted timestamp. Treat "no enabled targets" as not due, or write a no-target run record.

### Medium - Manual JD health-check "Run Now" can enqueue overlapping runs

The Settings UI sets `running` before POSTing but does not guard `runNow()` against being called again (`frontend/src/components/SettingsPanel.jsx:1052-1062`). The `SectionHeader` action button also has no disabled support and always calls `action.onClick` (`frontend/src/components/SettingsPanel.jsx:1070-1073`, `frontend/src/components/SettingsPanel.jsx:2042-2049`). On the backend, `POST /api/jd-health-check/run` unconditionally calls `run_jd_health_check_task.delay()` (`routes/jd_health_check.py:164-167`).

Impact: double-clicks, impatient repeated clicks, or multiple browser tabs can start concurrent live parser scans. That duplicates outbound traffic, duplicates result rows for the same run window, and can send duplicate failure emails.

Recommendation: add a frontend guard/disabled action and a backend concurrency guard. The backend should be authoritative, for example by checking a Celery lock, DB run table, or Redis lock before accepting a new manual run.

### Medium - Scheduled JD health checks start before Flask startup is known to be valid

In `app.py`, the Celery worker and JD health-check scheduler are started before the Flask port availability check (`app.py:601-613`). The scheduler performs its first due check immediately when its daemon thread starts (`backend/utils.py:552-586`). If port 5004 is occupied, `app.py` exits at `app.py:613-628`, but an overdue scheduled health-check task may already have been submitted.

Impact: a launch attempt that should fail cleanly because the backend cannot bind its required port can still trigger real outbound parser checks and possible Gmail notification attempts before the process exits.

Recommendation: move the fixed-port validation before starting Celery and the JD scheduler, or make scheduler startup happen only after all fail-fast startup checks have passed.

### Low - Frontend build still ships a single oversized initial bundle

`npm run build` succeeds, but Vite reports `dist/assets/index-B59p2KUW.js` at 559.19 kB minified, above the default 500 kB warning threshold. `App.jsx` statically imports the large top-level panels (`frontend/src/App.jsx:36-38`) and renders them conditionally later (`frontend/src/App.jsx:513-533`), so users download Job Queue, Settings, and Email History code in the initial payload regardless of the active tab.

Impact: local usage will work, but first paint and reload latency grow as more panels are added.

Recommendation: code-split top-level tab panels with `React.lazy()`/`Suspense`, or configure Rollup `manualChunks` in `frontend/vite.config.js`.

### Low - Future-dated AI attribution comment

`frontend/src/components/SettingsPanel.jsx:945` contains `// ai: claude-sonnet-5 | 2026-08-04`, but this review was performed on 2026-08-03. Project standards require the actual work date in AI attribution comments.

Impact: attribution metadata is slightly misleading and can make future blame/history audits harder to interpret.

Recommendation: correct the marker date to the actual edit date for that change.

---

## Positive Notes

- Ruff, backend tests, and the frontend production build are currently green.
- The previous port auto-selection bug in `app.py` is fixed, and the current dirty `launcher.py` diff applies the same fail-fast behavior instead of silently shifting away from port 5004.
- The browser-capture queue claim path now uses a single `UPDATE ... FOR UPDATE SKIP LOCKED ... RETURNING` statement, addressing the prior duplicate-claim race.
- Server-side external URL fetches reviewed in the parser/import paths continue to route through `safe_urlopen()` with DNS/IP validation and redirect re-validation.
