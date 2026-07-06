# Code Review - 2026-07-06

Scope: whole-project read-only review of the Flask backend, repository layer, external integrations, frontend, browser extension surfaces, and tests. No application code changes were made.

## Findings

### High - Tracked SQLite database remains in the repository

- Severity: High
- Location: `data/job_hunt_assistant.sqlite3:1`, `.gitignore:8`
- Open since 2026-04-21
- Impact: the repository still tracks a local SQLite database file. Even though this copy is currently zero bytes in the working tree, the tracked path can accumulate personal job-search data later and the history/index still treats the DB as a source artifact. The current ignore rules cover old DB names such as `data/jobhunt.db`, but not `data/job_hunt_assistant.sqlite3`.
- Evidence: `git ls-files data/job_hunt_assistant.sqlite3` returns the database path, and `.gitignore` has no rule for the active SQLite filename from `.env.example`.
- Plan: remove the database from the git index, add `data/job_hunt_assistant.sqlite3` plus its WAL/SHM variants to `.gitignore`, and keep only `data/.gitkeep` tracked.

### High - Career-site scanner can still fetch arbitrary internal URLs

- Severity: High
- Location: `backend/career_site_scanner.py:217`, `backend/career_site_scanner.py:235`, `routes/career_sites.py:812`
- Open since 2026-05-07
- Impact: an API user can create or update a career-site URL that only needs to start with `http://` or `https://`. The scanner then passes that URL to `urlopen` without rejecting loopback, private, link-local, metadata-service, or unusual port targets. If this app is reachable beyond a fully trusted local user, the scanner can be used for SSRF or internal network probing.
- Evidence: the route validation accepts any HTTP(S) URL at `routes/career_sites.py:812` and `routes/career_sites.py:846`; `_fetch_html` and `_fetch_json` construct `urllib.request.Request` objects and call `urlopen` directly at `backend/career_site_scanner.py:229` and `backend/career_site_scanner.py:255`.
- Plan: centralize outbound URL validation before every fetch, resolve hostnames to IPs, reject private/loopback/link-local/multicast ranges and unsafe ports, and apply the same validation after redirects.

### High - Unauthenticated GUI picker routes still expose server-side UI control

- Severity: High
- Location: `routes/system.py:71`, `routes/system.py:98`, `app.py:284`
- Open since 2026-05-07
- Impact: `/api/system/select-folder` and `/api/system/select-file` can open Tk dialogs on the server host and accept a caller-provided initial path. The app currently has no general API authentication layer, so any origin that can reach the local Flask server can trigger server-side GUI behavior. This is a denial-of-service and local environment disclosure risk, especially if `FLASK_HOST` is changed from the current localhost default.
- Evidence: the routes call `_tk.Tk()` and `_filedialog.askdirectory` / `_filedialog.askopenfilename` at `routes/system.py:80` and `routes/system.py:107`. `app.py:284` registers `system_bp`, including these `/api/system/*` routes, without an auth check.
- Plan: remove server-side GUI routes from the HTTP API or guard them behind explicit local-only/authenticated access. Prefer frontend file/folder inputs or typed path configuration for headless compatibility.

### Medium - Gmail source query construction still allows search-operator injection

- Severity: Medium
- Location: `backend/email_source_filters.py:78`, `backend/email_source_filters.py:83`, `backend/email_source_filters.py:160`
- Open since 2026-05-07
- Impact: configured email-source values are concatenated into Gmail search syntax. Sender filters are never quoted or escaped, and subject filters are only quoted when they contain whitespace. A malicious or accidental value containing Gmail operators or grouping characters can broaden/narrow the search unexpectedly before the app-side filter runs.
- Evidence: `sender_domain` and `sender_email` build `from:{match_value}` directly at `backend/email_source_filters.py:78-81`. `_gmail_query_value` only escapes double quotes and returns unquoted values without whitespace at `backend/email_source_filters.py:160-162`.
- Plan: always quote Gmail query operands and escape characters with Gmail search meaning, or validate sender filters as domains/emails and reject operator characters. Add tests with values containing `)`, `{`, `}`, `OR`, and `in:anywhere`.

### Medium - Frontend lint fails because TriagePanel calls hooks after early returns

- Severity: Medium
- Location: `frontend/src/components/TriagePanel.jsx:453`, `frontend/src/components/TriagePanel.jsx:474`
- Impact: when `loading` or `jobs.length === 0` changes between renders, `TriagePanel` can return before calling later `useMemo` hooks. React requires hooks to be called in the same order on every render; ESLint correctly flags this as a runtime crash risk when moving between loading, empty, and populated states.
- Evidence: `if (loading) return ...` appears at `frontend/src/components/TriagePanel.jsx:453`, and multiple `useMemo` calls begin afterward at `frontend/src/components/TriagePanel.jsx:474`. `npm run lint` reports six `react-hooks/rules-of-hooks` errors at lines 474, 480, 481, 482, 484, and 487.
- Plan: move all hooks above the conditional returns, computing empty-safe values from `jobs`, then render loading/empty states after hook initialization.

### Medium - Several generic API handlers still return raw exception text

- Severity: Medium
- Location: `routes/jobs.py:175`, `routes/jobs.py:751`, `routes/import_.py:585`, `routes/emails.py:238`, `routes/career_sites.py:986`
- Impact: `_safe_error_message` now sanitizes AI-provider failures, but many non-AI handlers still return `str(exc)` or interpolate `exc` into 500 responses. Infrastructure errors from Gmail, parsing, DB, filesystem, or import analysis can expose internal paths, provider details, connection strings, or stack-derived messages to the frontend.
- Evidence: examples include manual ingest returning `str(exc)` on a generic 500 at `routes/jobs.py:175-177`, Gmail fetch errors interpolating `exc` at `routes/jobs.py:751-753` and `routes/emails.py:292-293`, captured import analysis returning `str(exc)` at `routes/import_.py:585-587`, and career-site re-evaluation returning `str(exc)` at `routes/career_sites.py:986-988`.
- Plan: keep `str(exc)` only for validated user-input exceptions mapped to 400/401. For generic 500 paths, log the full exception server-side and return a stable sanitized message.

### Medium - OAuth client secret file is untracked but not ignored

- Severity: Medium
- Location: `googleusercontent.com.json:1`, `.gitignore:4`
- Impact: the workspace contains a root-level Google OAuth client JSON file with a `client_secret`. It is currently untracked, but `.gitignore` only ignores `credentials/google_credentials.json` and `credentials/token.json`, so this root file can be accidentally committed.
- Evidence: `git status --short` lists `?? googleusercontent.com.json`, and the file contains an OAuth client configuration with a `client_secret` field. `.gitignore` has no pattern for `*.apps.googleusercontent.com` downloads or `googleusercontent.com.json`.
- Plan: move the file into the ignored `credentials/google_credentials.json` path if it is needed, or delete it after setup. Add ignore coverage for downloaded Google OAuth JSON filenames.

### Low - Import listener authentication is still optional

- Severity: Low
- Location: `config.py:168`, `config.py:174`, `backend/import_listener.py:96`
- Open since 2026-05-07
- Impact: the browser import listener is enabled by default and its token defaults to an empty string. The host default is now `127.0.0.1`, which reduces exposure, but any local process can still post import payloads or claim queue work unless the user manually configures `JOB_IMPORT_LISTENER_TOKEN`.
- Evidence: `DEFAULT_JOB_IMPORT_LISTENER_ENABLED` defaults to true at `config.py:168-170`, `DEFAULT_JOB_IMPORT_LISTENER_TOKEN` defaults to `""` at `config.py:174`, and token validation only runs when `self._token` is truthy at `backend/import_listener.py:96-100`.
- Plan: generate a random listener token during setup or require a non-empty token when the listener is enabled. Surface the token to the extension setup flow instead of silently running unauthenticated.

### Low - Career-site adapter fallback still swallows unexpected programmer errors

- Severity: Low
- Location: `backend/career_site_scanner.py:650`, `backend/career_site_scanner.py:659`, `backend/career_site_scanner.py:674`, `backend/career_site_scanner.py:692`, `backend/career_site_scanner.py:701`
- Open since 2026-06-08
- Impact: adapter dispatch catches broad `Exception` and falls back to later adapters or the generic scanner. Expected network/parse failures are reasonable fallback cases, but programmer errors such as `TypeError`, `AttributeError`, or `KeyError` are also swallowed and recorded as ordinary adapter failures.
- Evidence: `scan_site` catches broad `Exception` for Greenhouse, Lever, Workday, Ashby, and embedded Greenhouse adapter calls. These handlers log only warning text without `exc_info=True` and continue scanning.
- Plan: introduce adapter-specific expected exception types for network/parse/empty-result failures and catch only those. At minimum, include tracebacks when broad fallback is intentionally retained.

## Verification

- `uv run pytest tests -q`: passed, `119 passed in 5.58s`.
- `npm run build` in `frontend/`: passed. Vite transformed 68 modules and emitted the existing large chunk warning for `assets/index-DX4rPb5I.js` at 564.90 kB.
- `npm run lint` in `frontend/`: failed with 6 `react-hooks/rules-of-hooks` errors in `frontend/src/components/TriagePanel.jsx`.

## Remediation Plan

1. Remove the tracked SQLite database and ignore the active SQLite filename and sidecar files.
2. Add outbound URL validation to career-site and related fetch paths before network requests and redirects.
3. Remove or strictly gate server-side Tk picker routes.
4. Fix `TriagePanel` hook ordering so frontend lint passes.
5. Sanitize remaining generic 500 responses and Gmail query operands.
6. Move/delete the root OAuth client secret file and expand ignore rules.
7. Require or generate an import listener token.
8. Narrow career-site adapter exception handling or log tracebacks for swallowed fallbacks.

## Evaluation & Remediation Plan (follow-up, same day)

Re-checked every finding against the current tree (post the `backend/db.py` /
`JobQueue.jsx` split earlier today — neither touched any file this review
flags, so all line numbers below are current). All nine are still present.
Context that shapes the recommendations below: this is a **single-user,
localhost-only** tool — `FLASK_HOST` defaults to `127.0.0.1` (`app.py:520`),
CORS is restricted to configured origins (`app.py:129`), and it's licensed
"Personal Use Only." That doesn't erase any of these findings, but it does
mean the realistic attacker is "a malicious webpage the user visits, or
another process on the user's own machine" — not an internet-facing
adversary — and that's the lens used below to separate "fix now" from
"defer."

### Fix now — cheap, safe, clearly worth it

1. **Tracked SQLite database.** Still tracked (`git ls-files` confirms
   `data/job_hunt_assistant.sqlite3`), still uncovered by `.gitignore`.
   Zero risk to fix, real risk if left (the file is 0 bytes today only
   because it happens to be freshly reset — the moment local dev runs
   against it, real job-search data becomes trackable). Untrack it, ignore
   `data/job_hunt_assistant.sqlite3` + `-wal`/`-shm` sidecars, keep
   `data/.gitkeep`.

2. **Gmail search-operator injection.** Confirmed at
   `backend/email_source_filters.py:78-81` (unquoted `from:{match_value}`)
   and `:160-162` (`_gmail_query_value` only escapes quotes, and only when
   whitespace is present). Bounded to one function, easy to add tests for
   (`)`, `{`, `OR`, `in:anywhere` as the review suggests). Worth doing
   because these values come from user-configured email sources that run
   unattended on a schedule — a typo or copy-paste artifact silently
   changing what gets matched is a real, if low-drama, correctness bug.

3. **`TriagePanel` hook-order violation.** Confirmed: `if (loading) return`
   at line 436, six `useMemo` calls starting at line 457 fire after it.
   This is a genuine React rule violation (not just style) — it will crash
   with "Rendered fewer hooks than expected" the moment `loading` flips
   true→false in a way that changes which memos would be evaluated. Purely
   mechanical fix (hoist the `useMemo`s above the early return, make them
   empty-array-safe), and `npm run lint`/`npm run build` verify it directly.

4. **Raw exception text in generic 500 handlers.** Confirmed at all five
   cited sites (`routes/jobs.py:175,751`, `routes/import_.py:585`,
   `routes/emails.py:238,292`, `routes/career_sites.py:986`) plus one the
   original review didn't list (`routes/emails.py:292` cleanup handler
   is fine — logs via `logger.exception` — but the Gmail-fetch one at
   `routes/emails.py:296` has the same `str(exc)`-to-client pattern). This
   is a direct, named rule in `PYTHON_STANDARDS.md` ("Never return raw
   `str(exc)` ... from a generic handler"), so it's not a judgment call.
   Fix: log full exception server-side (already happening in most of
   these), return a fixed sanitized string client-side.

5. **Untracked OAuth client secret file.** `googleusercontent.com.json`
   is real and still sitting at the repo root with a live `client_secret`.
   `.gitignore` has no pattern that would catch it. This is the one where
   "fix now" is almost free: add an ignore rule (e.g. `*.apps.googleusercontent.com.json`
   plus the exact filename) so it can never be accidentally `git add -A`'d.
   **Flag for the user, not just a code fix:** this file's contents were
   read directly during this evaluation (to confirm the finding), so the
   secret has now passed through this conversation. It's a Google
   "installed app" OAuth client secret, which Google's own model treats as
   not fully confidential (no server-side secret protection is possible
   for installed/native apps) — but out of caution, consider rotating it
   in the Google Cloud Console and moving the replacement into the already
   -ignored `credentials/google_credentials.json` path instead of the repo
   root.

6. **Career-site adapter fallback swallows programmer errors.** Confirmed,
   5 call sites in `backend/career_site_scanner.py:650-701`, all
   `logger.warning(...)` with no traceback. The multi-adapter fallback
   *design* (try Greenhouse, then Lever, then Workday, ... then generic) is
   intentional and reasonable to keep broad — narrowing every adapter to
   exact expected exception types is a bigger, lower-value effort. The
   cheap, high-value fix is just adding `exc_info=True` to the five
   `logger.warning` calls so a real bug (`TypeError`/`AttributeError`) still
   leaves a traceback in the logs instead of vanishing into "adapter
   failed" noise.

### Worth fixing, but scoped down from the original ask

1. **Career-site scanner SSRF.** Still unguarded — `_fetch_html`/`_fetch_json`
   (`backend/career_site_scanner.py:217,235`) call `urlopen` directly, and
   `routes/career_sites.py:813,847` only check the `http(s)://` prefix.
   This is worth doing *because* career-site URLs are scanned on an
   unattended schedule (`CAREER_SITE_SCAN_INTERVAL_MINUTES`), so a
   mistyped or malicious URL isn't a one-off click — it's an automated,
   repeated fetch. Recommend a focused fix: one small
   `backend/url_safety.py` helper that resolves the hostname, rejects
   loopback/private/link-local/multicast ranges and non-standard ports,
   and gets called both before the initial request and again after every
   redirect hop (the review specifically calls out redirect bypass, and
   `urlopen` follows redirects transparently). Not doing the larger
   "authenticate every internal API caller" version of this — that's a
   bigger architectural change for a tool with exactly one user.

2. **Unauthenticated Tk picker routes.** Confirmed, and actually slightly
   worse than the original write-up implies: `select-folder`/`select-file`
   read the body with `request.get_json(silent=True)`, which doesn't
   require `Content-Type: application/json` — so a "simple" cross-origin
   POST (the kind that skips CORS preflight entirely) from any webpage the
   user happens to have open can pop a native file-picker dialog on their
   desktop. The response body is unreadable to the attacker page (CORS
   blocks that part), so this is a nuisance/local-DoS vector, not data
   exfiltration. Recommend a cheap backstop rather than the full "remove
   server-side GUI" rework the original review proposed: reject the
   request in the route itself if an `Origin` header is present and not in
   the configured CORS allow-list (that header is still sent on
   preflight-exempt simple requests, so checking it server-side closes the
   gap that `flask-cors` alone doesn't). Full removal in favor of a
   frontend-native picker is a real UX rewrite — worth doing eventually,
   not blocking on it now.

### Defer / no action recommended

1. **Import listener token optional by default.** Confirmed default is
   still `""` (`config.py:174`), and validation is skipped entirely when
   empty (`backend/import_listener.py:96`). Recommend **not** forcing a
   token by default: the listener already defaults to `127.0.0.1`-only
   (fixed since this finding was first opened), and the browser extension
   is presumably already configured to talk to it *without* a token today
   — silently requiring one would break that flow for the one user of this
   tool without warning. Cheaper, non-breaking alternative: log a startup
   warning when the listener is enabled with an empty token, so it's
   visible without changing behavior. Given it's already scoped to
   localhost and flagged Low, this is a "nice to have," not a "do now."

This finding has been open since 2026-05-07 and re-confirmed open in at
least one prior review before this one — that recurrence is itself a signal
it's correctly triaged as low-urgency for this project rather than
perpetually forgotten.

