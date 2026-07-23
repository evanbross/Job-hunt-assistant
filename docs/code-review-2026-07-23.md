# Full Code Review - 2026-07-23

Scope reviewed: Flask app startup, route registration, database repository layer, background scanner/listener paths, Gmail/OAuth integration, SSRF helpers, React/Vite frontend, browser extension queue capture, project config, and test/build/lint health.

Verification run:

- `uv run pytest tests -q` - passed, 274 tests.
- `npm run build` in `frontend/` - passed. Vite emitted only the existing chunk-size warning.
- `uv run ruff check .` - failed on import ordering in two files.
- `uv run pytest -q` from the repo root - failed during collection because `tmp/pytest` is not readable.

## Findings

### High - OAuth and frontend proxy break when Flask auto-selects a non-5004 port

`app.py` intentionally falls back to another Flask port when `FLASK_PORT`/5004 is already occupied (`app.py:595`, `app.py:597`, `app.py:601`, `app.py:611`, `app.py:614`). That fallback is not propagated to the rest of the app:

- Google OAuth always uses `http://localhost:5004/api/auth/google/callback` (`config.py:143`, `routes/auth.py:96`, `routes/auth.py:141`).
- Vite always proxies API calls to `http://localhost:5004` (`frontend/vite.config.js:44`).
- README setup also documents only the fixed 5004 callback (`README.md:139`).

Impact: if port 5004 is busy, Flask can appear to start successfully on a different port, but the development frontend still sends API calls to 5004 and Google redirects OAuth callbacks to 5004. That makes login/API behavior fail in a confusing way exactly when the automatic fallback path is used.

Recommendation: either fail fast when the configured Flask port is unavailable, or make `PUBLIC_BACKEND_ORIGIN`/OAuth redirect/proxy target derive from one shared configured port. For OAuth specifically, avoid silently changing the runtime port unless the redirect URI is also updated and registered with Google.

### High - Browser capture queue claiming is not atomic under a threaded listener

The import listener uses `ThreadingHTTPServer` (`backend/import_listener.py:46`, `backend/import_listener.py:302`). Each `GET /queue` request can call `claim_queue()` concurrently (`backend/import_listener.py:229`, `backend/import_listener.py:245`). The repository claim path first selects pending rows and then updates them to `processing` in a separate statement (`backend/db/repositories/browser_capture_queue.py:110`, `backend/db/repositories/browser_capture_queue.py:127`, `backend/db/repositories/browser_capture_queue.py:137`).

Impact: two overlapping `/queue` requests can both read the same `pending` rows before either update commits, then both return the same work to browser-extension clients. That can open duplicate tabs, submit duplicate captures/imports, and make queue state harder to reason about under WebSocket wake-ups plus fallback polling.

Recommendation: make claiming a single atomic operation. For Postgres, use `UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED LIMIT ...) RETURNING id, url, ...` inside one transaction. For SQLite, use a transaction mode that obtains the write lock before selecting, or an atomic status transition keyed by status with returned rows.

### Medium - The default root pytest command fails because `tmp/pytest` is collected

`uv run pytest tests -q` passes all tests, but `uv run pytest -q` fails during collection with `PermissionError: [WinError 5] Access is denied: 'F:\\Evan-F\\Projects\\JobHuntAssistant\\tmp\\pytest'`.

Impact: contributors and automation using the default pytest command get a red test run even though the actual test suite passes. This creates noise and can hide real failures.

Recommendation: add pytest config in `pyproject.toml`, for example `testpaths = ["tests"]` and/or `norecursedirs = ["tmp", ".venv", "frontend/dist"]`.

### Medium - Ruff currently fails on import ordering

`uv run ruff check .` reports fixable `I001` import-order violations in:

- `backend/utils.py:37`
- `routes/jobs.py:37`

Impact: the configured lint gate is not green. This is small, but it prevents using Ruff as a reliable pre-commit/CI signal until fixed.

Recommendation: run `uv run ruff check . --fix` or manually sort those imports while preserving existing attribution comments.

### Medium - Generated Swagger docs are structurally misleading

The app builds Swagger docs dynamically in `app.py`, but every POST/PUT/PATCH receives a generic optional object body (`app.py:224`, `app.py:233`) and every route gets the same generic response set (`app.py:244`). The generator does not inspect Pydantic models, accepted JSON fields, or route-specific status codes.

Impact: `/apidocs/` looks authoritative but omits the real request/response contracts. For routes with strict fields, async start responses, 401/403/404/409/502 paths, or multipart uploads, clients generated from this spec will be wrong.

Recommendation: either mark the generated spec as route inventory only, or add explicit per-route OpenAPI metadata for the public API surfaces that users or tools rely on.

### Low - Some generated/imported user-facing errors still expose raw exception text

Several internal paths correctly sanitize generic failures, but setup/test and background status paths still return raw exception strings in places such as OpenAI setup testing (`routes/setup.py:253`) and scan state errors (`routes/jobs.py:1739`, `routes/jobs.py:1747`, `routes/applications.py:1088`).

Impact: in a local-only app this is mostly diagnostic convenience. If the server is exposed beyond the local machine, raw errors can disclose local paths, provider messages, or operational details.

Recommendation: keep detailed exception text in logs and return short sanitized messages to the browser for generic failures. Continue allowing explicit provider authentication errors where they are intentionally user-actionable.

## Notes

The repository layer generally follows the project rule that route handlers do not contain raw SQL; the only route-level SQL found was the health probe (`routes/system.py:81`). Dynamic column SQL in repositories is mostly protected by class-level allowlists.

The SSRF-safe `safe_urlopen` helper is used for caller/configuration-derived server-side fetches. The remaining direct `requests.get` calls I reviewed target fixed public APIs or URLs constrained by Greenhouse host regexes, so I did not classify them as SSRF defects.
