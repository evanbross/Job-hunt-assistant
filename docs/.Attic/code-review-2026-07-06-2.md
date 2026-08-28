# Code Review - 2026-07-06 (2)

Scope: whole-project read-only review of the Flask backend, repository layer, external integrations, frontend, browser extension surfaces, and tests. No application code changes were made.

Carried-forward findings from prior reviews were rechecked first. The tracked SQLite database, Gmail query escaping, TriagePanel hook ordering, generic 500 sanitization, OAuth JSON ignore coverage, health-check DB probe, failing tests, and career-site adapter traceback logging appear fixed in the current tree and are not repeated below.

## Findings

### High - Outbound URL fetch paths still lack internal-network protection

- Severity: High
- Location: `backend/career_site_scanner.py:217`, `backend/career_site_scanner.py:229`, `backend/career_site_scanner.py:235`, `backend/career_site_scanner.py:255`, `routes/career_sites.py:812`, `routes/career_sites.py:846`, `routes/settings.py:192`, `routes/settings.py:195`, `routes/settings.py:198`, `routes/applications.py:355`, `routes/applications.py:386`, `backend/email_parser.py:540`, `backend/email_parser.py:553`
- Open since 2026-05-07
- Impact: caller-controlled, configured, imported, or email-derived URLs can still make the server issue HTTP requests without rejecting loopback, private, link-local, multicast, metadata-service, or unsafe-port targets. In the localhost-only deployment model this is mostly a malicious-local-page or malicious-email risk, but scheduled career scans and unattended email parsing can repeat the fetch without an explicit user click.
- Evidence: career-site creation and update only require the URL to match `^https?://` before storing it, and `_fetch_html` / `_fetch_json` call `urlopen` directly. The Ollama connection test builds `tags_url` from request JSON `ollama_base_url` and fetches it. Closed-req scanning fetches persisted job URLs. Email tracking URL resolution follows URLs extracted from email bodies. None of these paths resolve the hostname and reject internal IP ranges before the request or after redirects.
- Plan: add a shared outbound URL safety helper used by all server-side fetches that accept non-constant URLs. It should parse and normalize the URL, allow only `http` and `https`, reject credentials and unsafe ports, resolve DNS, reject private/loopback/link-local/multicast/reserved ranges, and enforce the same checks on every redirect target.

### Medium - State-changing API routes still have no CSRF or origin enforcement

- Severity: Medium
- Location: `app.py:129`, `routes/auth.py:145`, `routes/auth.py:148`, `routes/scan.py:830`, `routes/scan.py:837`, `routes/career_sites.py:930`, `routes/career_sites.py:937`, `routes/import_.py:519`, `routes/import_.py:524`
- Open since 2026-05-07
- Impact: CORS restricts which origins can read API responses, but it does not stop all cross-origin writes. Several POST routes require no JSON body and can be triggered as simple cross-origin form/fetch requests by a webpage running on the user's machine. Examples include disconnecting Google auth, starting an email scan, starting a career-site scan, and force-resetting import state.
- Evidence: the app configures CORS at `app.py:129`, but the cited routes do not check a CSRF token, session auth, same-origin header, or allowed `Origin` before mutating state. `auth_disconnect` deletes the Google token on POST; `scan_emails` and `run_career_scan_now` start background threads on POST; `reset_import` clears import task state on POST.
- Plan: add a small API guard for state-changing routes that rejects unexpected `Origin` / `Referer` values and, for browser-origin calls, requires a CSRF token or same-origin session proof. Apply it consistently to POST, PUT, PATCH, and DELETE endpoints. Keep extension/listener traffic on a separate explicit token path.

### Medium - Server-side Tk picker routes are remotely triggerable local UI actions

- Severity: Medium
- Location: `routes/system.py:71`, `routes/system.py:76`, `routes/system.py:80`, `routes/system.py:83`, `routes/system.py:89`, `routes/system.py:98`, `routes/system.py:103`, `routes/system.py:107`, `routes/system.py:110`, `routes/system.py:119`
- Open since 2026-05-07
- Impact: `/api/system/select-folder` and `/api/system/select-file` can open native dialogs on the server host. A cross-origin request cannot read the selected path because of CORS, but it can still pop UI, block workflow, and potentially disclose environment details through raw error text if the caller is allowed to read the response.
- Evidence: both routes use `request.get_json(silent=True)` and then call `_tk.Tk()` plus `askdirectory` / `askopenfilename`. They catch broad `Exception` and return the raw exception text in the JSON error. There is no local-only, same-origin, or auth check inside the routes.
- Plan: remove these server-side GUI routes or guard them behind the same origin/CSRF protection as other state-changing routes. Prefer frontend-native file/folder selection or typed path configuration for headless compatibility. Return a fixed sanitized error message and log the full exception server-side.

### Low - Import listener authentication remains optional

- Severity: Low
- Location: `config.py:168`, `config.py:172`, `config.py:174`, `backend/import_listener.py:97`, `backend/import_listener.py:98`, `backend/import_listener.py:99`, `backend/import_listener.py:100`
- Open since 2026-05-07
- Impact: the import listener defaults to enabled on `127.0.0.1`, which limits exposure, but authentication is disabled unless `JOB_IMPORT_LISTENER_TOKEN` is configured. Any local process can post import payloads or claim queue work in the default setup.
- Evidence: `DEFAULT_JOB_IMPORT_LISTENER_ENABLED` defaults to true, `DEFAULT_JOB_IMPORT_LISTENER_HOST` defaults to `127.0.0.1`, and `DEFAULT_JOB_IMPORT_LISTENER_TOKEN` defaults to the empty string. The listener only validates `X-Job-Import-Token` or `token` query parameters inside `if self._token:`.
- Plan: keep the current local-only default if backwards compatibility matters, but surface a startup/settings warning when the listener is enabled without a token. Longer term, generate a token during setup and share it with the browser extension configuration.

## Verification

- `uv run pytest tests -q`: passed, `121 passed in 6.74s`.
- `npm run build` in `frontend/`: passed. Vite transformed 83 modules and emitted the existing chunk-size warning for `assets/index-SiTf8FKs.js` at 567.66 kB.
- `npm run lint` in `frontend/`: passed.

## Remediation Plan

1. Add shared outbound URL validation and apply it to career-site scanning, Ollama testing, closed-req URL checks, and email tracking URL resolution.
2. Add same-origin/CSRF enforcement for state-changing Flask API routes, with a separate explicit token path for extension/listener traffic.
3. Remove or strictly gate the Tk picker endpoints, and sanitize their 500 responses.
4. Warn on startup/settings when the import listener is enabled without a token, then plan a token generation flow for the extension.
