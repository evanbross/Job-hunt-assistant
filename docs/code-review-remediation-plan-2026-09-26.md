# Code Review Remediation Plan (2026-09-26)

Source: `docs/code-review-2026-09-26-full.md`. Findings were checked against
the code and the local `.env` before ranking.

## Framing

This app is treated as a **commercial, multi-tenant cloud service**, not a
single-user local tool. Consequences for triage:

- A finding is never downgraded because only one account or machine exists
  today. "Needs two users to bite" is the bite.
- Dev-only infrastructure defaults (loopback listener, Caddy, MailHog,
  committed MinIO credentials) are not a production design.
- Structural fixes (identity, per-user storage) are preferred over quick
  patches that would have to be redone.

## Verification of the review's findings

| Finding | Confirmed | Note |
|---|---|---|
| Listener unauthenticated by default | Yes | `.env` has no `JOB_IMPORT_LISTENER_TOKEN`; empty token passes every protected route (`backend/scanning/import_listener.py` `_check_token`). |
| Infra ports on all interfaces | Yes | `../infra/docker-compose.yml` publishes Caddy 80 + admin 7019, Redis 16379, MailHog, MinIO (`admin`/`password`) with no loopback binding; `caddy.json` admin listens on 0.0.0.0. |
| Default Flask secret | Yes (code) | `config.py:54` falls back to `change-this-in-production`; local `.env` sets a key, but the code default is unsafe for any other install. |
| First-account fallback | Yes | `first_account_user_id()` used in `app.py` listener callbacks, `backend/scanning/schedulers.py`, `routes/applications.py`. |
| Global resume file | Yes | `uploads/resume.pdf` written in `routes/settings.py`, read in `app.py`. |
| FitScorer tests hit a real DB | Yes | Two tests mock only the AI client; `_build_system_prompt` opens repos against the configured DB. |
| Login throttle behind proxy | Yes | Keyed on `request.remote_addr`, no `ProxyFix`; in-memory per-process counters. |
| `learnAnswer` ignores errors | Yes | `browser-extension/background.js` never checks `res.ok`. |
| Ruff (3) / ESLint (4) | Yes | Ruff findings auto-fixable; Fast Refresh warnings predate the review. |

Additional finding discovered during verification: `routes/auth.py`
`/auth/disconnect` deletes the single shared Google token file
(`config.GOOGLE_TOKEN_FILE`), so one user disconnecting disconnects everyone.
Same class as the global resume file. Known related gap from earlier work:
`candidate_profile.md` is also a shared file.

## Priority list

### Tier A - structural, do first (items 1 and 2 share one change: user identity everywhere)

1. **Identity on every extension call.**
   - Per-user, hashed, revocable, per-device extension tokens with label,
     created / last-used / optional expiry. Token shown once at creation;
     only a SHA-256 hash is stored (tokens are high-entropy random values).
   - Delete `first_account_user_id()`; nothing in the listener, capture queue,
     or schedulers runs without an explicit user.
   - Every Celery and scheduler task carries an explicit `user_id`; scheduled
     work fans out per tenant.
   - The queue WebSocket authenticates with a short-lived one-time ticket, not
     a URL token (browsers cannot set headers on WebSocket connections).
   - Remove the global static listener token entirely (no compatibility shim).
   - Listener requires authentication on every non-`/health` route.
   - Design so the credential model works for both the local dev listener and
     a future public TLS API.
2. **Remove every global file that holds per-user data.** Resume
   (`uploads/resume.pdf`), Google token (`GOOGLE_TOKEN_FILE`), and
   `candidate_profile.md` move to per-user storage: object storage keyed by
   user, encrypted at rest, with size/type validation; migrate existing files
   to the first account.
3. **Secrets fail-fast.** Refuse to boot on a missing or placeholder
   `FLASK_SECRET_KEY`; support key rotation; verify session cookie flags
   (`Secure`, `HttpOnly`, `SameSite`).

### Tier B - next

4. **Real production infrastructure**, not only loopback-binding dev. Stopgap:
   bind dev ports to `127.0.0.1` in `../infra` (Caddy admin, Redis, MailHog,
   MinIO). Caveat: phone access over Tailscale may need Caddy port 80 left
   reachable. Production needs its own configuration: no MailHog, no Caddy
   admin API, Redis with authentication and TLS, credentials from a secrets
   manager, network segmentation.
5. **Login and abuse throttling.** Trusted-proxy handling with an exact proxy
   count (only trust `X-Forwarded-For` from the real proxy; Flask may also be
   directly reachable), counters shared in Redis, keys on both account and
   address, applied to login, registration and password reset; per-token
   limits and per-tenant AI cost quotas.
6. **Test isolation.** Inject/mock the settings source in the `FitScorer`
   tests and add a guard so the suite refuses to run against any database
   other than the local test one.
7. **Quality gates enforced in CI:** ruff, ESLint, pytest, production build.

### Tier C - previously "skip", now included

- Route-level lazy loading for the 646 kB main bundle.
- Fast Refresh warnings in `SettingsPanel.jsx` and the `loadAccount` hook
  dependency in `CompleteSetupPage.jsx`.
- `learnAnswer`: check `res.ok`, surface errors to the content script; bump
  the extension version.

### Beyond the review - decide up front for a commercial service

- **Row-level security in Postgres** as defense in depth behind `user_id`
  filters (one missed filter would otherwise be a cross-tenant leak).
- **Two-user isolation tests** for every route and background task.
- **Audit log** for credential events (token created / used / revoked) and
  account actions.
- **Data lifecycle:** per-user export and deletion, backups, retention rules.

## Suggested order

1 and 2 together, then 3, then the rest. The small fixes are trivial and can
land early at any point: `ruff --fix`, the `learnAnswer` fix, the `FitScorer`
test isolation.

## Item 1 progress (2026-09-26)

Status: **implemented, not yet committed.** Full suite 708 passed; ESLint clean
apart from three pre-existing Fast Refresh warnings; extension JS syntax-checked.

Done:
- 1a `extension_tokens` table, repository, and `ExtensionTokenService`
  (`backend/extension_auth/`). SHA-256 hash only, plaintext shown once; label,
  created / last-used (write-throttled to once a minute) / optional expiry /
  revocation; deleting a user cascades.
- 1b Listener authenticates every non-`/health` request with
  `Authorization: Bearer jha_...` *before reading any body*, resolves a user, and
  passes it into every callback. The shared static token, its system setting,
  `.env` variable, Settings field and startup warning are removed (a migration
  deletes the stored value). A failing auth backend returns 503, never a
  default user. Query-string and `X-Job-Import-Token` auth are gone.
- 1c `routes/extension_tokens.py` (create / list / revoke, session-authenticated,
  strict validation, 20 active tokens per user, `Cache-Control: no-store` on
  the create response) and a Settings > Browser Extension panel.
- 1d Queue WebSocket authenticates the handshake with a single-use 30-second
  ticket issued over authenticated HTTP (`POST /ws-ticket`); connections are
  tracked per user and a wake-up is only pushed to the user whose queue has
  pending work. Added a clean `stop()`.
- 1e Extension 1.24.0: bearer header via shared `extension-auth.js`, WebSocket
  ticket flow (with a guard against duplicate sockets), toolbar badge and
  notification when the token is rejected, `learnAnswer` now checks `res.ok`
  and reports failures, options page validates the token and its Test
  Connection button checks it against an authenticated endpoint.
- 1f `first_account_user_id()` deleted. `submit_closed_req_capture` takes the
  user explicitly; the JD-health and career-site schedulers fan out over every
  active account with per-user failure isolation
  (`PostgresUserAccountRepository.list_active_user_ids`).

Tests added: token service/repository (real Postgres), route validation and
caps, listener auth on every protected route, per-user data separation,
legacy auth forms rejected, body-not-read-before-auth, WebSocket ticket auth
and per-user push over a real socket, cross-tenant closed-req capture.

Known remaining gaps (not part of item 1):
- (Resolved by item 2, below: the resume PDF is now stored per user.)
- No per-token or per-IP throttling of failed listener authentication yet
  (item 5); tokens are 256-bit so guessing is infeasible, but each bad request
  costs a DB lookup.
- The WebSocket ticket store is in-process memory (single-process deployment);
  a scaled deployment needs Redis behind the same interface.
- The listener is plain HTTP on loopback; a public deployment needs TLS and
  should expose the same endpoints through the main API. Audit logging of
  token events is not built yet.

## Item 1 build sequence

1a. `extension_tokens` table + repository + token service (create / verify /
    revoke / list) with tests.
1b. Listener authenticates per request and resolves a user; listener callbacks
    take an explicit `user_id`; remove the static token and
    `first_account_user_id()` from the listener path.
1c. Session-authenticated API and Settings UI to generate, list and revoke
    tokens.
1d. Queue WebSocket one-time ticket.
1e. Extension: token handling, WebSocket ticket flow, visible errors on 401;
    bump `manifest.json` version.
1f. Schedulers / Celery tasks / closed-req capture carry explicit `user_id`;
    remove `first_account_user_id()` entirely.

## Item 2 progress (2026-09-26)

Status: **implemented, not yet committed.** Full backend suite green apart from
the failures fixed along the way; ruff clean; frontend build and ESLint clean
apart from the three pre-existing Fast Refresh warnings.

Every file that held per-user data on the server's disk now lives in the
database, scoped to its owner, with secrets encrypted at rest (the existing
Fernet helper, `SETTINGS_ENCRYPTION_KEY`).

- **2a Resume PDF.** Stored encrypted in the user's own `resume` row
  (`backend/resume/resume_file_service.py`); validated (PDF magic bytes, 5 MB
  cap, filename reduced to a basename) and text-extracted from memory. Replaces
  `uploads/resume.pdf`. `resume.get_latest` no longer loads the encrypted blob
  and reports `has_file`; the Resume settings panel tells a user whose original
  PDF was never stored to re-upload it.
- **2b Google.** Each user's OAuth token is an encrypted row
  (`google_oauth_tokens`, `GoogleTokenStore`); `GmailClient` now takes a
  `user_id` and all nine call sites pass the acting user. Disconnect and
  refresh affect only that user (previously one user disconnecting removed
  everyone's token, and every account's scans read the same mailbox). The
  OAuth *client* config is now an admin-only, encrypted system setting managed
  through the setup wizard (`google_oauth_client_config`, a new `hidden` flag
  keeps it out of the generic settings UI/API). The authorization callback is
  bound to the user who started the flow and rejects a different logged-in
  account.
- **2c Candidate profile.** Now a per-user row (`candidate_profiles`) read
  through `get_candidate_profile(user_id, repos)`; the file-path setting, the
  wizard's "detect a file in the project folder" flow, and the server-side
  native file dialog (`/api/system/select-file`) are gone. New
  `GET/PUT /api/candidate-profile` (validated, 100 000 character cap;
  rescores the queue only when the text changed), a text editor in Settings and
  the wizard with client-side "Import from a file", and learned cover-letter
  rules are appended to the user's row under a row lock. A missing profile is a
  409 with a clear message instead of a 500. Previously any account without its
  own path read one shared file.
- **2d Greenhouse login session.** Now an encrypted per-user row
  (`greenhouse_sessions`, `GreenhouseSessionStore`) instead of
  `credentials/greenhouse_session.json`; the searcher takes a store instead of
  a path and `clear-session` only clears the caller's.

One-time startup migrations (idempotent, never delete: files are renamed to
`*.migrated` or only read) move an existing install's data across: the resume
PDF goes to the account with the newest resume row; `token.json`,
`google_credentials.json`, and `greenhouse_session.json` go to the first
account / system settings; each account's `candidate_profile_path` file (and,
for the first account only, the implicit shared default) is copied into its
row. If `SETTINGS_ENCRYPTION_KEY` is missing they are deferred with a warning.
The test suite is isolated from the developer's real files (autouse fixture in
`tests/conftest.py`).

Also fixed while here: unauthenticated listener POSTs now drain a bounded
amount of the request body after the 401 so Windows doesn't reset the
connection before the client reads the response; the `FitScorer` unit tests no
longer open a database connection.

Found, not fixed (candidates for the next items):
- `POST /api/system/select-folder` opens a native folder dialog on the *server*
  (tkinter) and the cover-letter save path is a server-side folder. Both are
  desktop-only and, in a cloud deployment, a server-side file write chosen by
  the caller: replace with an in-app download/export.
- Greenhouse "Login with browser" opens a visible browser on the server;
  inherently desktop-only, needs redesign (extension-based capture) before a
  hosted deployment.
- The Wellfound session file (`credentials/wellfound_session.json`) is shared
  on purpose: it only carries an anti-bot clearance cookie for public pages
  (no login), so it is not per-user data. It is still an on-disk cache that
  won't be shared across instances.
- Extracted resume text, the candidate profile, and the application profile /
  answer bank are stored in plaintext columns (the resume *PDF*, tokens and
  sessions are encrypted). Decide on a consistent at-rest policy (column
  encryption vs. database/volume encryption) as part of the data-lifecycle work.
- The OAuth `_pending_auths` state and the WebSocket ticket store are
  in-process memory; a multi-instance deployment needs Redis behind the same
  interfaces.
