# Full Code Review - 2026-09-26

Scope reviewed: Flask application startup and request guards, session/account authentication, route and service boundaries, Postgres repositories/schema, Celery/background workflows, external HTTP and AI integrations, React/Vite frontend, browser extension capture/autofill flows, and the tracked files in `../infra` (`docker-compose.yml`, `caddy.json`, and `caddy_registry.py`). Generated assets, local data, credentials, virtual environments, and dependency internals were excluded.

Baseline: `JobHuntAssistant` was clean on `main` at `79f63bc` before this report was added. `../infra` was clean on `master` at `05a5f6d`.

Verification performed:

- `uv run ruff check .` - failed with three import-order findings in `routes/session_auth.py`, `routes/voice.py`, and `tests/test_default_admin_setup.py`.
- `uv run pytest -q` - 626 passed, 1 skipped, 2 failed, and 7 setup errors. The seven setup errors were sandbox temp-directory permissions; rerunning those tests with a workspace-local `--basetemp` produced 7 passes. The two `FitScorer` failures are real test-isolation regressions described below.
- `npm run lint` - completed with four warnings and no errors (one missing hook dependency and three Fast Refresh export warnings).
- `npm run build` - passed; Vite warned that the main JavaScript chunk is 646.63 kB minified.
- `node --check` passed for every extension JavaScript file.
- `docker compose config -q` accepted the infra Compose file (the Docker CLI also reported that its user-level config file was inaccessible in the sandbox).

---

## Findings

### Critical - The default extension listener is unauthenticated while Caddy exposes it beyond loopback

The listener token defaults to empty (`config.py:250`, `.env.example:58`), and an empty token explicitly makes every protected listener route pass authentication (`backend/scanning/import_listener.py:248-255`). Those routes include queue claiming, the application profile, saved application answers, the uploaded resume PDF, capture submission, and answer creation (`backend/scanning/import_listener.py:257-421`). Startup only logs a warning and continues when no token is configured (`app.py:641-652`). The extension also defaults to an empty token (`browser-extension/background.js:506-516`, `browser-extension/options.js:37-46`).

Although the listener itself defaults to `127.0.0.1`, the application registers it behind Caddy as `jha-listener` (`app.py:978-980`), and the infra Compose file publishes Caddy's port 80 on all host interfaces (`../infra/docker-compose.yml:25-35`). A client that can reach the developer machine can supply the expected `Host` header and reach the listener through Caddy. This changes the threat from “any local process” to potentially any client on the same reachable network.

Impact: an unauthenticated network client can download highly sensitive applicant data (profile, answer bank, and resume), claim or manipulate browser-capture work, submit attacker-controlled imports, and write learned answers.

Recommendation: require a non-empty, high-entropy listener token and fail startup if the listener is enabled without one. Bind every published dev-infra port to `127.0.0.1` unless LAN access is explicitly required. Keep `/health` public if needed, but never expose the data/write routes without authentication. Add an integration test proving an empty token cannot start a data-bearing listener.

### High - Browser-extension and scheduler work is assigned to the first account, not the authenticated user

The standalone listener has no user identity and deliberately resolves all capture queue, profile, resume, and answer-bank operations through `first_account_user_id()` (`app.py:496-608`). That helper explicitly returns the lowest account ID and states that it is not multi-tenant behavior (`backend/utils/utils.py:110-120`). The same fallback appears in closed-request capture and schedulers (`routes/applications.py:818-835`, `backend/scanning/schedulers.py:77-79`, `backend/scanning/schedulers.py:213-215`).

Impact: after a second account is created, its extension cannot receive its own queue/profile/answers and its captures may be written into the first account. Conversely, anyone with the global listener token receives the first account's private autofill data. Scheduled tasks also silently operate on only one tenant.

Recommendation: issue per-user extension credentials, store only a hash server-side, resolve each listener request to a user before invoking callbacks, and include `user_id` explicitly in every Celery/scheduler task payload. Remove `first_account_user_id()` after all non-request call paths carry an identity. Add two-user tests covering queue claims, imports, profile/resume reads, learned answers, and scheduled work.

### High - Resume files are globally shared and can cross tenant boundaries

Resume metadata/text is user-scoped in Postgres, but every upload overwrites the same file, `uploads/resume.pdf` (`routes/settings.py:691-723`). The extension listener then loads metadata for the first account but reads that single global file (`app.py:559-578`). The code comments already acknowledge that the DB row and file can belong to different users (`app.py:560-564`).

Impact: one user's upload replaces the bytes served for another user's resume. The extension can receive a PDF belonging to a different account while labeling it with the first user's filename. This is both data corruption and a direct privacy breach.

Recommendation: store resume bytes under a user-owned immutable key (for example `uploads/<user_id>/<uuid>.pdf`) and persist that key in the resume row. Read bytes using the same user-scoped row selected for metadata. Delete or garbage-collect the prior object only after the new DB/file update succeeds.

### High - A known default Flask signing key permits session forgery when configuration is omitted

`FLASK_SECRET_KEY` falls back to the public literal `change-this-in-production` (`config.py:54`), and that value directly signs Flask sessions (`app.py:137`). There is no startup validation rejecting the placeholder.

Impact: on any installation that omits the environment value, an attacker who can reach the app can forge a cookie containing `authenticated`, an arbitrary `user_id`, and session state. That bypasses the login gate and may allow impersonation or admin access.

Recommendation: remove the fallback and fail fast unless the key is present and meets a minimum entropy/length requirement. For local onboarding, generate and persist a random secret once rather than shipping a universal value. Add a startup/config test that rejects the placeholder and empty keys.

### High - Shared infra publishes unauthenticated control/data services on all host interfaces

The Compose short-form port mappings publish Caddy, its admin API, Redis, MailHog, and both MinIO ports on all interfaces (`../infra/docker-compose.yml:28-31`, `:46-50`, `:61-67`, `:73-84`). Caddy's admin endpoint explicitly listens on `0.0.0.0:2019` with no authentication (`../infra/caddy.json:2-4`), Redis has no password, and MinIO uses committed credentials `admin` / `password` (`../infra/docker-compose.yml:82-84`). The registry additionally creates an HTTP reverse-proxy alias to the Caddy admin API (`../infra/caddy_registry.py:151-177`).

Impact: another machine that can reach the host can modify Caddy's live routing, read/write Redis (including Celery broker data), inspect captured mail and password-reset links, and administer MinIO with known credentials. A compromised web page or local process also has a simple unauthenticated route-management target.

Recommendation: bind host mappings to `127.0.0.1` (for example `127.0.0.1:7019:2019`), keep the Caddy admin listener off the public interface, remove the `caddy.localhost` admin proxy, require Redis authentication, and source randomized MinIO credentials from an untracked environment file. If LAN access is intentional, put the services behind firewall rules and authenticated TLS instead.

### Medium - `FitScorer` unit tests now make an unmocked production database connection

`FitScorer._build_system_prompt()` opens repositories to load timezone/profile settings (`backend/scoring/fit_scorer.py:174-180`). Two tests mock only the AI client and therefore attempt to connect to the configured database (`tests/test_fit_scorer.py:147-176`). The full suite produced two failures at exactly that connection boundary.

Impact: nominal unit tests depend on network access and the developer's live database configuration. CI or offline runs fail, and a test can accidentally read production-like settings.

Recommendation: inject the already-resolved timezone/profile content into `FitScorer`, or inject a settings/profile provider. At minimum, mock the repository/settings boundary in both tests. Keep a separate marked integration test for the real Postgres path.

### Medium - Login rate limiting collapses clients behind the reverse proxy into one bucket

Authentication throttling keys attempts by `request.remote_addr` (`routes/session_auth.py:82-84`). The normal app topology uses Caddy as a reverse proxy, but the Flask app does not install trusted-proxy handling for `X-Forwarded-For`. Requests proxied by Caddy therefore appear to originate from the proxy rather than their actual client.

Impact: failed logins from one client can exhaust the five-attempt bucket for every client using the proxy. In a multi-user or LAN deployment this creates a low-effort denial of service, while multiple workers still maintain separate in-memory counters and weaken brute-force protection.

Recommendation: configure `ProxyFix` with an exact trusted-proxy count only when running behind the known local Caddy instance, then derive the rate-limit key from the corrected address. Move counters to Redis with atomic expiry so all workers share one limit, and consider keying by both normalized email and client address.

### Low - Extension answer learning reports success to itself even when the write fails

The `learnAnswer` message handler sends a POST but never checks `res.ok` (`browser-extension/background.js:586-603`). A 401 from a stale token or a 500 from storage is silently ignored.

Impact: the user can accept “remember this answer,” yet the answer is not saved and no failure is surfaced. This is especially likely while listener authentication is being configured.

Recommendation: check the response status, parse the sanitized server error, and return a structured failure to the content script so its confirmation UI can remain visible or display an error.

### Low - Static quality gates are not clean

Ruff reports import-order violations in `routes/session_auth.py:55`, `routes/voice.py:24`, and `tests/test_default_admin_setup.py:46`. ESLint reports a missing `loadAccount` dependency in `frontend/src/components/CompleteSetupPage.jsx:145` and three Fast Refresh warnings in `frontend/src/components/SettingsPanel.jsx:2132`, `:2719`, and `:2730`. The production build succeeds but emits a 646.63 kB main-chunk warning.

Impact: none of these currently blocks runtime behavior, but a warning-tolerant baseline makes new regressions harder to distinguish and the large initial bundle increases startup/parse cost.

Recommendation: fix the import order, stabilize or inline `loadAccount` so the effect dependency is accurate, move non-component exports out of `SettingsPanel.jsx`, and introduce route/panel-level lazy loading for the largest UI sections.

---

## Positive Notes

- The application now has a centralized session gate, admin-only system-settings guard, setup-completion guard, and Origin/Referer write protection.
- User-scoped repository calls are widespread, and the multi-tenant test suite covers many direct HTTP/repository paths.
- Caller-controlled outbound fetches generally use the shared DNS/redirect-aware SSRF helper.
- Database connections are normally managed through `get_repos()` and closed in a `finally`-backed context manager.
- The browser extension scripts are syntactically valid, capture responses check failure status, and dangerous HTML insertion in the inspected confirmation UI escapes the interpolated question.
- Frontend lint and production build complete; 626 Python tests passed in the full run, and all seven sandbox-affected temp-path tests passed when rerun with a local temp directory.
- The infra Compose model is syntactically valid and uses persistent volumes plus health checks for Redis and MinIO.

## Recommended Remediation Order

1. Make the extension listener fail closed and restrict every infra port to loopback.
2. Replace the global listener identity and global resume path with per-user credentials and storage.
3. Reject the default Flask secret and rotate any installation that has used it.
4. Lock down Caddy admin, Redis, MailHog, and MinIO credentials/access.
5. Restore unit-test isolation and make Ruff/ESLint clean.
6. Move authentication throttling to a proxy-aware shared store.
