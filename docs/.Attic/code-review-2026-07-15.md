# Code Review - 2026-07-15

Full read-only review of the backend, repository layer, external integrations, frontend,
browser extension/userscripts, and tests. Prior `docs/code-review-*.md` findings were
re-checked before adding new findings; resolved items are not repeated.

## Findings

### High - Jobanni scan can fetch arbitrary caller-supplied URLs without SSRF validation

- Severity: High
- Location: `routes/jobanni.py:146`, `backend/jobanni_scanner.py:75`, `backend/jobanni_scanner.py:83`
- Impact: `/api/jobanni/scan` accepts `search_url` from the request body and passes it into a scraper that calls `urllib.request.urlopen` directly. This bypasses the project's `safe_urlopen` helper, so the route does not enforce `http`/`https`, reject credentials or unsafe ports, block loopback/private/link-local/metadata-service destinations, or re-check redirects. A same-origin caller or browser extension bug could make the Flask process probe local/internal network targets.
- Evidence: `start_scan()` reads `search_url = str(body.get("search_url") or config.JOBANNI_SEARCH_URL).strip()`, then `_run_scan()` passes it to `jobanni_scan()`. `_fetch()` builds a `urllib.request.Request` and calls `urllib.request.urlopen(req, timeout=30)` instead of `safe_urlopen`.
- Plan: validate `search_url` before starting the thread and replace `_fetch()` with `safe_urlopen`. If Jobanni must only support `jobanni.com`, allowlist that host explicitly. Add tests for loopback, private IP, disallowed scheme, and redirect rejection.

### High - Ollama provider requests bypass the outbound URL safety layer

- Severity: High
- Location: `routes/settings.py:114`, `backend/ai_provider.py:80`, `backend/ai_provider.py:186`, `backend/ai_provider.py:215`
- Impact: settings accepts any non-empty `ollama_base_url`, and `AIProviderClient` later POSTs to `{ollama_base_url}/api/chat` with raw `urllib.request.urlopen`. The test endpoint uses `safe_urlopen(..., allow_loopback=True)`, but actual scoring/chat calls do not. A stored config value can therefore make normal AI operations issue server-side POSTs to arbitrary destinations, including internal services.
- Evidence: `update_config()` saves `ollama_base_url` without URL validation. `_complete_ollama()` calls `_post_json(f"{self._ollama_base_url}/api/chat", ...)`, and `_post_json()` opens the request with `urllib.request.urlopen(request, timeout=600)`.
- Plan: route all non-Anthropic/OpenAI `urllib` calls through `safe_urlopen` with an explicit local-Ollama policy, such as `allow_loopback=True` plus a narrow allowed-port set, or validate and store only approved Ollama base URLs.

### Medium - Launcher startup failures can lose the real exception and raise `NameError`

- Severity: Medium
- Location: `launcher.py:226`, `launcher.py:230`
- Impact: if server startup raises before the Tk callback executes, the scheduled lambda references `exc` after Python has cleared the exception variable at the end of the `except` block. Instead of showing the original startup error, the UI callback can fail with `NameError`, leaving users with a generic or missing error dialog.
- Evidence: Ruff reports `F821 Undefined name exc` at `launcher.py:230`. The code schedules `lambda: messagebox.showerror("Startup Failed", str(exc), ...)` inside `except Exception as exc`.
- Plan: capture the message before scheduling the lambda, e.g. `error_message = str(exc)`, and have the callback close over `error_message`.

### Medium - Current test suite fails on the Workday title/req-id behavior

- Severity: Medium
- Location: `backend/career_site_scanner.py:996`, `tests/test_career_site_scanner.py:275`
- Impact: the suite currently has a real regression failure, which degrades confidence in later fixes. The code now appends the Workday req ID to single-job API titles, while the test still expects the raw API title.
- Evidence: `uv run pytest` collected 183 tests: 182 passed and 1 failed. `test_workday_single_job_from_api_strips_html_content` expected `"Full-Stack Software Engineer"` but received `"Full-Stack Software Engineer (JR253369)"`.
- Plan: decide whether req IDs should be included for Workday single-job API results. If yes, update the test expectation and any UI assumptions; if no, remove the `workday_title_with_req_id()` call from `_workday_single_job_from_api()`.

### Medium - Jobanni request body is parsed as raw JSON instead of validated boundary data

- Severity: Medium
- Location: `routes/jobanni.py:145`, `routes/jobanni.py:150`
- Impact: `max_jobs` is parsed with `int(...)` directly from the request body. A malformed value such as `"abc"` raises `ValueError` outside a validation handler, producing a 500 rather than a sanitized 400. This also misses the project standard requiring Pydantic validation for boundary data.
- Evidence: `start_scan()` uses `body = request.get_json(silent=True) or {}` and then `max_jobs = min(max(int(body.get("max_jobs") or config.JOBANNI_MAX_JOBS), 1), 500)` with no schema or local `ValueError` handling.
- Plan: add a small Pydantic request model for `search_url` and `max_jobs`, including normalization and bounds, and return a sanitized 400 on `ValidationError`.

### Medium - Several frontend write calls still ignore `res.ok`

- Severity: Medium
- Location: `frontend/src/App.jsx:363`, `frontend/src/components/ApplicationSheetPanel.jsx:399`, `frontend/src/components/JobQueue/index.jsx:1411`
- Impact: write calls can fail while the UI proceeds as if they succeeded. Reconnect can start a new auth flow after disconnect failed, the stage-status repair only catches network errors, and force-reset clears local import UI state even if `/api/import/reset` returned 4xx/5xx.
- Evidence: the cited calls issue `fetch(..., { method: "POST" })` without checking `res.ok`. This violates the JavaScript standard requiring write calls to check `res.ok` before refetching or updating state.
- Plan: check `res.ok` for each POST/PUT/PATCH/DELETE call, surface a user-visible failure for direct user actions, and avoid local state changes when the server rejected the write.

### Low - Formatting and lint baselines are not clean

- Severity: Low
- Location: `app.py:73`, `app.py:277`, `backend/ai_provider.py:35`, `launcher.py:226`, plus Black output across 84 files
- Impact: formatting/lint drift hides real issues in noisy verification output and weakens the review gate. Ruff also found the launcher `F821` behavior reported above.
- Evidence: `uv run ruff check .` reported 32 errors, including unused import `start_career_site_scheduler`, many `E402` late imports in `app.py`, import-order issues, and `launcher.py` `F821`. `uv run black --check .` reported 84 files would be reformatted.
- Plan: fix behavioral lint errors first, then run `ruff check --fix` where safe and `black .` using the same PyCharm/Black formatter configuration. Keep formatting-only commits separate from behavior changes.

## Verification

- `uv run pytest`: failed, 183 collected, 182 passed, 1 failed.
- `uv run ruff check .`: failed, 32 errors.
- `uv run black --check .`: failed, 84 files would be reformatted; also printed a Python 3.14/target-version parser warning.
- `npm run build` in `frontend/`: passed. Vite warned that one chunk is larger than 500 kB after minification.
- `npm run lint` in `frontend/`: passed.

## Remediation Plan

1. Put all remaining configurable/caller-supplied URL fetches behind `safe_urlopen`, starting with Jobanni and Ollama.
2. Fix the `launcher.py` startup-error closure bug.
3. Resolve the Workday test failure by choosing the intended title format and updating code or tests accordingly.
4. Add Pydantic request validation for Jobanni, then expand the same pattern to other route bodies as touched.
5. Add `res.ok` handling for the remaining frontend write calls.
6. Clean the Ruff/Black baseline in a separate formatting-focused pass.
