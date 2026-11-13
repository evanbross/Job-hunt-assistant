# Full Code Review - 2026-08-19

Scope reviewed: Flask startup and blueprint registration, route handlers, repository layer, background workers and schedulers, Gmail/OAuth integration, SSRF helpers, AI-provider integration, React/Vite frontend, browser-extension capture path, project configuration, and automated tests.

Working-tree note: the review included the current uncommitted changes visible at review time, including `backend/ai_provider.py`, `backend/db/repositories/jobs.py`, `routes/jobs.py`, multiple `frontend/src/components/JobQueue/*` files, `frontend/src/components/JobCard.jsx`, `frontend/src/components/TriagePanel.jsx`, `frontend/src/utils/jobQueueUtils.js`, and the new `frontend/src/components/common/SnoozeModal.jsx`.

Verification performed:

- Static/source inspection with `rg`, targeted file reads, and current diff review.
- Did not run `uv run pytest`, `uv run ruff`, `npm run lint`, or `npm run build` for this review pass.

---

## Findings

### High - No application-level authentication protects state-changing local-control APIs

The app has Google OAuth routes, CORS, and an Origin/Referer write guard (`app.py:136-160`), but the ordinary API surface is otherwise unauthenticated. Sensitive local-control endpoints can update runtime config and paths (`routes/settings.py:75-253`), write Google OAuth client credentials (`routes/setup.py:294-333`), write the candidate profile (`routes/setup.py:342-354`), open/clear authenticated scraper sessions (`routes/greenhouse.py:235-258`, `routes/wellfound.py:247-270`), trigger scans/imports, and write cover letters to caller-selected folders (`routes/cover_letter.py:135-244`).

Impact: this is acceptable for a trusted localhost personal tool, but it is not safe for a commercial or shared deployment. Any user who can reach the Flask server can alter credentials/config, trigger costly AI/API work, disclose local filesystem paths, or write files into accessible server directories.

Recommendation: explicitly enforce localhost-only binding for the personal app, or add real app authentication/authorization before exposing it beyond a single-user machine. For commercial deployment, add an authenticated user/session model, per-user data ownership, and role checks around setup/config/filesystem/auth/session-control endpoints.

### High - Long-running jobs hold database connections while doing network and AI work

Several background flows open `with get_repos()` and then perform slow outbound work before the DB connection is released. `routes/board_search.py:188-360` holds a DB connection while fetching remote board data (`routes/board_search.py:210`) and scoring every candidate through the AI provider (`routes/board_search.py:285`). The same pattern appears in career-site scanning: the connection opens at `routes/career_sites.py:329`, then scan/network work and AI scoring happen inside that block (`routes/career_sites.py:462`, `routes/career_sites.py:665`). Email scanning opens the repos block at `routes/scan.py:252` and performs per-email parser/scoring work inside it (`routes/scan.py:758`). Re-evaluation also keeps the connection across per-job AI scoring (`routes/fit.py:90`, `routes/fit.py:162`).

Impact: a slow AI provider, job board, scraper, or Gmail flow can tie up Postgres connections for minutes. Under multiple users, scheduled work, retries, or overlapping background tasks, this can exhaust the pool and block unrelated reads/writes. It also keeps transactions open longer than necessary.

Recommendation: split each pipeline into short DB phases. Load the needed rows, close the repo context, perform external/network/AI work without a DB connection, then reopen a fresh repo context for batched writes. For large jobs, persist task/run state in the DB or Celery result backend rather than relying on a single long transaction.

### Medium - In-memory background state and daemon threads are single-process only

Many workflows store `running`, progress, errors, and result state in module globals and daemon threads: scan (`routes/scan.py:97`, `routes/scan.py:971-972`), board search (`routes/board_search.py:81`, `routes/board_search.py:391-392`), fit re-evaluation (`routes/fit.py:78`, `routes/fit.py:236-237`), career-site scans (`routes/career_sites.py:101`, `routes/career_sites.py:1036-1055`), closed-req scans (`routes/applications.py:622`, `routes/applications.py:1226-1231`), and the Wellfound/Greenhouse manual browser flows (`routes/wellfound.py:73`, `routes/greenhouse.py:75`).

Impact: this works in a single Flask process, but status and mutual-exclusion guarantees break under a multi-worker WSGI server, restart, or horizontal deployment. One worker can start work while another reports idle; duplicate work can start; progress disappears on process death.

Recommendation: keep the current model only for local single-process use. For production, move task state and locks into Postgres/Redis/Celery primitives with durable run records and per-task IDs, then make status endpoints read from that shared store.

### Medium - App import performs production side effects

Importing `app.py` creates directories, initializes the DB (`app.py:176`), starts the browser import listener (`app.py:437-443`), and starts the queue WebSocket server (`app.py:445`). Only Vite/Celery startup is guarded under `if __name__ == "__main__"` (`app.py:663-666`).

Impact: tests, CLI imports, WSGI imports, and tooling that merely import `app` can unexpectedly touch the database and bind listener ports. This makes startup harder to reason about and can create port collisions or duplicate helper services in non-dev process managers.

Recommendation: introduce an app factory or explicit startup function. Keep route registration import-safe, and start listeners/schedulers only from the chosen runtime entry point after config validation succeeds.

### Medium - Some outbound HTTP paths bypass the shared SSRF helper

The project has strong SSRF protection in `backend/utils/url_safety.py`, and most caller-derived fetches use `safe_urlopen()`. Two remaining direct `requests.get()` paths do not use that helper: Greenhouse detail API fetches in `backend/scanning/sources/greenhouse_searcher.py:247-260`, and public board searches in `backend/scanning/job_board_searcher.py:132-183`.

Impact: the board searcher currently targets fixed Remotive/The Muse hosts, so the immediate SSRF risk is limited. The Greenhouse API URL is derived from regex-matched job URLs, which narrows it, but it still creates a second outbound-fetch stack with different redirect/DNS behavior. Over time, this is exactly where future caller-supplied URLs tend to creep in without the central guard.

Recommendation: wrap these calls in an `httpx` or urllib helper that applies the same DNS/IP and redirect validation as `safe_urlopen()`, or add an allowlisted-host HTTP client for fixed public APIs. Add regression tests beside `tests/test_url_safety.py` for these remaining call paths.

### Medium - Request validation is inconsistent across route modules

Newer endpoints use Pydantic models, for example application profile updates (`routes/application_profile.py:55-99`) and closed-req scan options (`routes/applications.py:1178-1215`). Many other request bodies are still manually parsed with `request.get_json()` and ad hoc checks, including config updates (`routes/settings.py:75-253`), job mutations (`routes/jobs.py:206-379`, `routes/jobs.py:693-713`, `routes/jobs.py:1108-1132`), email source updates (`routes/emails.py:511-613`), and cover-letter actions (`routes/cover_letter.py:135-244`, `routes/cover_letter.py:250-494`).

Impact: validation behavior drifts across endpoints: some normalize booleans and enums, some silently coerce strings, some return raw exception text, and some accept partial bodies with no typed schema. This raises the cost of adding frontend features and makes edge cases harder to test comprehensively.

Recommendation: migrate the higher-churn route families to Pydantic boundary models first: settings/config, jobs, emails, and cover-letter actions. Keep validators responsible for normalization, and return sanitized 400s from a shared validation helper.

### Medium - Frontend has lint/build checks but no automated behavior tests

`frontend/package.json:6-10` provides `dev`, `build`, `preview`, and `lint`, but no `test` script. No frontend `*.test.jsx`, `*.spec.jsx`, or `__tests__` files were found under `frontend/`. This leaves large interactive flows in `frontend/src/components/JobQueue/index.jsx`, `frontend/src/components/TriagePanel.jsx`, `frontend/src/components/ApplicationSheetPanel.jsx`, and modal-heavy common components covered only by manual use and production builds.

Impact: JSX syntax and hook-rule issues may be caught by build/lint, but user-visible behavior regressions are easy to miss: disabled/enabled button logic, polling cleanup, optimistic list updates, dialog lifecycle, and error-state rendering.

Recommendation: add Vitest + React Testing Library for component behavior and MSW or fetch mocks for API flows. Start with tests for Job Queue actions, company-history enablement, snooze modal behavior, import progress polling, and Application Sheet stage/status updates.

### Low - Generated API docs are likely too shallow for clients

Swagger generation is inferred from Flask route rules and methods (`app.py:181-286`) rather than route-level request/response schemas. It explicitly creates generic body parameters and notes that schemas are intentionally not inferred (`app.py:276-286`).

Impact: `/apidocs/` is useful as an endpoint index, but it cannot reliably support client generation, contract tests, or typed frontend API calls. As Pydantic models spread, this gap will become more visible because the actual boundary schemas exist in code but are not published.

Recommendation: when adding Pydantic request/response models, expose them through explicit OpenAPI schemas or generate specs from a typed route layer. Treat the current Swagger output as navigation, not a contract.

### Low - Current working-tree endpoint lacks test coverage

The working tree adds `PostgresJobRepository.get_company_counts()` (`backend/db/repositories/jobs.py:198-213`), `GET /api/jobs/company-counts` (`routes/jobs.py:183-191`), and frontend company-history enablement (`frontend/src/utils/jobQueueUtils.js:139-158`, `frontend/src/components/JobQueue/index.jsx:405-411`). No matching tests were added in the visible diff.

Impact: this is narrow, but it affects visible queue behavior. A normalization mismatch, failed fetch, or count edge case can disable the Company History action incorrectly.

Recommendation: add a repository test for normalized grouping and a route/unit test for the endpoint payload. On the frontend, add a small utility test for `companyCountKey()` / `hasCompanyHistoryAvailable()` once frontend tests exist.

---

## Positive Notes

- Repository methods consistently keep raw SQL out of route handlers, and dynamic identifier interpolation is mostly guarded by per-repository allowlists.
- The URL safety helper validates DNS results and redirect targets, and important caller-supplied fetch paths route through it.
- The CSRF-style Origin/Referer guard is centralized in `app.py` and has unit coverage through `tests/test_is_trusted_api_write_origin.py`.
- The voice SQL path uses a validator plus a dedicated readonly role and closes the connection in `finally`.
- The import pipeline has moved the heaviest work into Celery task state, which is a stronger pattern than the remaining in-process scan state.

