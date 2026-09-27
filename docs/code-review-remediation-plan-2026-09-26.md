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

## Item 3 progress (2026-09-26)

Status: **implemented, not yet committed.**

- **Fail fast.** `FLASK_SECRET_KEY` and `SETTINGS_ENCRYPTION_KEY` are validated at
  startup (`backend/security/`): a missing, example/placeholder, whitespace-padded,
  repetitive, or (for the Flask secret) under-32-character value stops the app with
  every problem listed and the fix. The code default `change-this-in-production`
  is gone. `APP_ENV` defaults to `production`; an explicit `APP_ENV=development`
  additionally generates a *missing* Flask secret into `.env` (never an encryption
  key, never overwriting a weak value). The Celery worker refuses to start without
  a valid encryption key. Test runs get their own throwaway secrets and never read
  a developer's real `.env`.
- **Rotation.** Both secrets have `*_FALLBACKS` lists: Flask signs with the new
  secret and still accepts sessions signed with an old one; the encryption layer is
  now a `MultiFernet` (encrypt with the primary, decrypt with any).
  `scripts/manage_secrets.py` provides `generate`, `rotate-flask-secret`,
  `rotate-encryption-key` (re-encrypts every stored ciphertext in one transaction,
  new `.env` written first so a failure can't strand data) and `check-encryption`
  (tells you when the old key can be dropped). Preview by default; `--write`
  applies. See `docs/secrets-and-key-rotation.md`.
- **Session cookies.** Explicitly `HttpOnly` and `SameSite=Lax`; `Secure` follows
  the app base URL (`SESSION_COOKIE_SECURE=auto|true|false`) and a Secure cookie
  also gets the `__Host-` name prefix. Startup logs the effective settings and
  warns in production if cookies aren't Secure. Tests read the real `Set-Cookie`
  headers and prove a session survives a secret rotation only with the fallback.
- **Known default credential removed.** The bootstrap admin was seeded on every
  fresh install with the documented password `changeme`. It is now a random
  one-time password logged once (or `BOOTSTRAP_ADMIN_PASSWORD`), tracked by a
  `bootstrap_password_pending` flag instead of comparing to a known string, and a
  startup migration replaces `changeme` on any install that never completed setup.
- **Wizard.** The setup wizard's AI step was already broken (it posted API keys to
  `/api/setup/save-env`, which stopped accepting them in August). That route and
  its `flask_secret_ok` check are removed; the wizard saves keys through
  `PUT /api/config` (per-account, encrypted).

Not done here (still open in this plan): production secret sourcing from a secrets
manager (this is env-var based), audit logging of secret rotation, and the item 4
infrastructure hardening.

## Item 4 progress (2026-09-26)

Status: **implemented and tested; two repositories changed (this one and `../infra`),
nothing committed in either.** The running dev stack was NOT restarted: the new port
bindings only take effect when its containers are recreated
(`cd ../infra && docker compose up -d`).

**Dev stack (`../infra`)**
- Every published port (Caddy 80 and admin 7019, Redis, MailHog SMTP + UI, MinIO API +
  console) is bound to `127.0.0.1`. Nothing is reachable from the LAN or a Tailscale
  peer. The phone-over-Tailscale setup is unaffected: it talks to Flask on :5004
  directly and never went through Caddy.
- **Caddy admin API.** Tested in throwaway containers: setting `origins` alone did
  nothing (Caddy disables Host checking when the admin API listens on a wildcard
  address, which Docker port publishing requires). `"enforce_origin": true` is what
  works: a browser-borne request (any page's `Origin`, including DNS-rebinding
  patterns) gets 403, while `caddy_registry` (now sending a matching `Origin` on every
  admin call) works. Any other client of the admin API must send an `Origin`.
- The `caddy.localhost` alias that proxied the admin API through the public :80
  listener is removed, and a Caddy still running it has it deleted on first contact.
  `caddy_registry`'s public functions are unchanged (BizMan and CostcoPrices import it).
- **MinIO** root credentials now come from `../infra/.env` (git-ignored, generated);
  compose refuses to start MinIO without them. The committed `admin` / `password` are gone.
- **Redis** password is opt-in (`REDIS_PASSWORD`) because MySitter connects to
  `dev_redis` without one; unset keeps today's behaviour.

**Production reference (`deploy/`)**, validated with `docker compose config`,
`caddy validate`, and a live run of the Redis service:
- Caddy is the only thing publishing ports (80/443); the admin API is off; security
  headers and a body-size cap are set (microphone stays allowed for the voice feature).
- Redis: TLS only (plain port disabled), password required, internal-only network
  with no internet route, runs as its own user with a read-only root filesystem and
  every capability dropped. Verified: correct password works, wrong password and
  plain-text connections are refused, the network has no route out.
- **Celery over TLS.** New `backend/security/redis_tls.py`: a `rediss://` URL
  requires certificate verification against `REDIS_TLS_CA_FILE` and refuses to
  proceed without a CA rather than downgrading. Verified against the real service
  from a container (broker and result backend connect; a wrong CA is rejected).
  `scripts/generate_redis_tls.py` creates the CA and server certificate (a live test
  found Python 3.13's strict verification needs key-identifier extensions).
- Every secret is a required compose variable; `deploy/.env.production` and
  `deploy/secrets/` are git-ignored.

**Not included (deliberately, see `deploy/README.md`):** the application image. `app.py`
is a desktop launcher (Vite, its own Celery worker, dev-Caddy registration), so a
production image needs a WSGI server, managed listener/WebSocket processes, and a
container entrypoint. That is a separate piece of work and should be item 4b. Also
open: `*_FILE` secret support, and the extension deriving the WebSocket host only for
`*.localhost` names.

## Item 5 progress (2026-09-26)

Status: **implemented and tested, nothing committed.**

**Trusted-proxy handling** (`backend/security/client_ip.py`, config `TRUSTED_PROXIES`)
- Empty by default: no proxy is trusted and forwarding headers are ignored, which is right
  when the app is reached directly. Entries are IPs or CIDRs; a bad entry or a `/0` fails
  startup.
- `X-Forwarded-For` / `X-Forwarded-Proto` are honoured only when the immediate peer is in
  that set, so a caller who reaches Flask or the listener directly cannot forge its
  address. The forwarded chain is walked from the right and stops at the first hop that is
  not trusted or not an IP.
- A WSGI middleware rewrites `REMOTE_ADDR` and `wsgi.url_scheme` (installed in
  `_init_runtime_state`). The scheme matters beyond throttling: the CSRF origin guard in
  `app.py` compares against `request.scheme`, which is `http` behind a TLS-terminating
  proxy and would reject every write.
- The production compose file pins Caddy to `172.28.0.2` on `edge` and sets
  `TRUSTED_PROXIES` to exactly that address (guarded by a test).

**Shared limiter** (`backend/security/rate_limiter.py`)
- Sliding window in Redis sorted sets (same Redis and TLS settings as Celery, `jha:rl:*`
  keys), so every worker shares one count. Emails and tokens are hashed before they become
  keys. Falls back to per-process counters, with a warning and a 30 s retry, if Redis is
  down: not fail-open, not a login outage.

**Policies** (`routes/session_auth.py`, `backend/scanning/import_listener.py`)
- Login: address 30/15 min, account 15/15 min, address+account 5/15 min. Failures only;
  a success clears the pair but not the address or account counters, so one valid login
  does not reset the budget for guessing others. Same behaviour for accounts that do not exist.
- Registration: address 10/h and target email 3/h, every attempt counted (each one sends
  mail or probes existence). Forgot password: address 10/15 min, plus 3/h per email that is
  silently suppressed so the response never says which addresses exist.
- Token submission (reset, complete registration, confirm email change): 20 failures/15 min
  per address, shared across the three. Change password: 5 failures/15 min per user.
  Email change: 5/h per user and 3/h per target address.
- Listener: 120 failed authentications/15 min per resolved client address (sized so one
  stale extension token retrying does not lock out valid tokens behind the same address)
  and 600 requests/min per token. Responses are 429 with `Retry-After`.

**Judgement calls**
- The account-wide login limit lets an attacker who knows an email force a temporary
  lockout for that account. The pair limit and account limit are the standard trade; the
  account limit is set at 15 so it takes deliberate distributed effort. A lockout stays
  visible (429 with `Retry-After`) rather than silent.
- **Per-tenant AI cost quotas: not built.** Users supply their own provider keys, so
  runaway spend is theirs. Revisit if the service ever provides AI access itself.
- Not throttled: the queue WebSocket (single-use 256-bit tickets, issued by the throttled
  listener) and the Google OAuth callback.

**Tests:** `tests/test_client_ip.py`, `tests/test_rate_limiter.py` (both backends, the
Redis one against the real dev Redis, skipped if none), throttle sections in
`tests/test_session_auth.py` and `tests/test_import_listener.py`. `tests/conftest.py` gives
every test its own in-memory limiter and a controllable clock.

## Item 6 progress (2026-09-26)

Status: **implemented and tested, nothing committed.**

- The `FitScorer` half was done during item 2 (its tests stub `_build_system_prompt` and no
  longer read the real settings database).
- **Guard:** `tests/db_guard.py`. `pytest_sessionstart` in `tests/conftest.py` aborts the
  whole run (exit code 2, no test executed) unless the test database is on a local host,
  has "test" in its name, and is not the application's own `DATABASE_URL`. `pg_dsn` checks
  again immediately before its `TRUNCATE`, so the destructive step cannot be reached any
  other way. Covered by `tests/test_db_guard.py`, including a child pytest run pointed at a
  remote database to prove the abort.

## Item 7 progress (2026-09-26)

Status: **implemented, nothing committed. The pipeline itself has not run: the repo is on
GitLab and there is no runner here, so each command was verified locally instead.**

`.gitlab-ci.yml`, run on merge requests and pushes to the default branch:
- `ruff` (blocking): `ruff check .` from the frozen lockfile (`--frozen` fails if `uv.lock`
  drifts from `pyproject.toml`).
- `frontend` (blocking): `npm ci`, `npm run lint:ci`, `npm run build`. `lint:ci` is ESLint
  with `--max-warnings 4`: the four existing warnings (Tier C) are tolerated, a fifth
  fails. Lower the number as they are fixed.
- `pytest` (blocking): against `postgres:17` and `redis:7` service containers, with JUnit
  and coverage reports. The suite's database guard (item 6) only accepts local hosts, so the
  job sets `TEST_DATABASE_EXTRA_HOSTS=postgres`: an explicit, single-host opt-in; the
  test-name and not-the-app's-database checks still apply.
- `pip-audit` and `npm-audit` (report only, `allow_failure`).

**Audit findings today** (the reason they are not blocking yet): `cryptography` 49.0.0
(fix 50.0.0), `pyasn1` 0.6.3 (0.6.4), `mistune` 3.3.2 (3.3.3), `anyio` 4.14.1, and 9 npm
advisories (6 high). `cryptography` guards the settings encryption, so upgrade it first.
Then remove `allow_failure` from both jobs.

Not included: `ruff format --check` (the codebase is not formatted to it), branch
protection requiring the pipeline to pass (a GitLab project setting, not a file).

## Tier C progress (2026-09-26)

Status: **implemented, nothing committed.** ESLint is now at 0 warnings and `lint:ci` in
`.gitlab-ci.yml` allows none (`--max-warnings 0`).

- **Lazy loading:** every tab except the job queue, plus the login-adjacent pages
  (verify account/email, reset password, complete setup), the voice widget and the Toptal
  modal, load on first use behind `Suspense`. Main bundle 646 kB to 445 kB (115 kB gzip);
  Settings (104 kB) is its own chunk. `RecentChangesModal` stays eager because
  `JobQueue` imports it statically, so lazy-loading it would only add a warning.
- **Fast Refresh warnings:** `SchemaFieldGroup`, `fetchSettingsSchema`, and the shared
  `labelStyle`/`helpTextStyle` moved out of `SettingsPanel.jsx` into
  `components/SchemaFields.jsx`, `utils/settingsSchema.js` and `components/settingsStyles.js`.
  This also stops `CompleteSetupPage` dragging the whole Settings panel into its chunk.
- **`loadAccount` hook dependency:** `CompleteSetupPage` keeps `onComplete` in a ref so
  `loadAccount` is a stable `useCallback` and the mount effect runs once.
- **`learnAnswer`:** already fixed earlier (the background script reports save failures).
- **Verified:** lint 0 problems, production build, and in the running app (signed in)
  Settings, Contacts and Phrase Book load their chunks with no console errors. The
  Applications and Email History tabs use the same mechanism but I did not click through them.

## Beyond the review: two-user isolation tests (2026-09-26)

Status: **implemented and tested, nothing committed.** Row-level security, the audit log
and data lifecycle are still to do.

`tests/test_tenant_isolation_matrix.py` replaces "a few resources by example" with the
whole surface:
- **Schema:** every table except three named shared ones has a NOT NULL `user_id` that
  references `user_accounts` with `ON DELETE CASCADE`. A new table that forgets it fails.
- **Coverage:** every `/api/*` route with a path parameter (59) must be in the matrix, and
  a matrix entry for a removed route fails, so a new id route cannot ship untested.
- **Attack:** account B calls every one of those routes on account A's resource. Each must
  be refused (403/404), or be on a short allowlist with a stated reason. Then A's rows must
  be unchanged **and B must own no new rows**: a row B owns that points at A's job is a
  cross-tenant write even when A's own rows are intact.
- **Control:** the owner is not refused on the same routes (re-seeded before each call), so
  the attack test cannot pass because a path is wrong for everyone. Routes that would call
  an AI provider or Google are exempt from this half.
- **Listings:** no parameterless GET route shows A's data to B (and at least 8 do show it to A).
- **Deletion:** deleting an account leaves no rows behind and does not touch the other account.

**Bugs found and fixed**
1. `POST /api/applications/<job_id>/stages` never checked the job belonged to the caller,
   so any account could attach a stage to another account's job id. Now 404. (Confirmed:
   the matrix fails again if the check is removed.)
2. `GET /api/jd-health-check/run/<task_id>/status` answered for any Celery task id. Task
   ids are recorded against the submitting account in Redis (`backend/security/
   task_ownership.py`) and the route answers 404 for anyone else. It fails closed if Redis
   is unreachable.

**Allowlisted non-refusals** (safe, but they answer 200 for a resource that is not yours,
because the SQL is scoped by `user_id` and simply matches nothing): DELETE application
answer / capture-queue item / feedback, PUT `/applications/<id>`, POST `/emails/<id>/hide`.
`mark-read` and `rescan` authenticate to the caller's own Google account first. Returning
404 from the scoped-no-op routes would need the repositories to report rows affected.

**Still open, found while writing this**
- The database does not stop a row from referencing another account's parent: a composite
  foreign key (`job_id, user_id` to `jobs(id, user_id)`, likewise contacts) would.
  Worth doing together with row-level security.
- The `latest_application_stage` view is not filtered by `user_id`; it is only reachable
  through the read-only voice-query role, whose SQL guard must scope by user.
- `PUT /api/email-subject-rules/<id>` answers 500 when `enabled` is a JSON boolean (the
  column is an integer). The frontend sends integers, so this is only a validation gap.

## Beyond the review: Postgres row-level security (2026-09-27)

Status: **implemented and tested, nothing committed.**

**Design** (`backend/db/row_level_security.py`): every table with a NOT NULL `user_id`
(read from the schema, not a hardcoded list) gets `ENABLE`/`FORCE ROW LEVEL SECURITY` and
a policy `user_id = current_setting('app.current_user_id', true)::integer`. `get_repos()`
sets that session GUC on every connection it opens -- from an explicit `user_id` argument
for background code (Celery tasks, the extension listener, schedulers), or from the Flask
session for routes, with no per-route change needed -- and commits it immediately so a
later `conn.rollback()` in the same request can't silently erase it. No identity set means
no rows, not all rows: fail closed. `init_db()` disables RLS before migrations/seeding
(which legitimately cross accounts) and re-arms it as its last step, every startup.

**A real bug, not just defense in depth:** `routes/voice.py`'s NL-to-SQL voice query never
scoped its generated SQL by `user_id` -- the column isn't even in `backend/sql_guard.py`'s
`ALLOWED_COLUMNS`, so the LLM had no reason to filter by it. Any authenticated account
could ask a question that returned another account's job data. RLS on the read-only
`jobhunt_readonly` connection (which now also sets the session identity) closes this
regardless of what SQL gets generated; `tests/test_row_level_security.py` proves it with
the exact shape sql_guard allows through (a plain unfiltered `SELECT`), including through
the real HTTP endpoint.

**One exclusion:** `extension_tokens`. Authenticating a bearer token means finding which
account it belongs to *before* any identity is known -- RLS would make that lookup
impossible. Its own `WHERE user_id = %s` filtering (verified by the isolation matrix)
still protects its CRUD routes.

**A superuser bypasses RLS unconditionally, FORCE included** -- this is a Postgres rule
with no code-level workaround. The default local-dev and CI Postgres role (`postgres`) is
a superuser, which would have made every RLS policy silently inert while looking like it
passed. Fixed by introducing `jha_test_app`, a real non-superuser role that owns the test
database:
- `scripts/setup_test_db.sql` creates it locally (drop-and-recreate `jha_test_db` owned by
  it, rather than `REASSIGN OWNED BY`, which fails on a few objects the bootstrap
  superuser owns "required by the database system"). `.env`/`.env.example`'s
  `LOCAL_POSTGRES_USER` now points at it, not `postgres`.
- `scripts/provision_ci_test_role.py` does the same against the `.gitlab-ci.yml` pytest
  job's throwaway `postgres` service container, before pytest runs.
- `tests/conftest.py`'s `pg_dsn` fixture also now points `config.POSTGRES_HOST/PORT/DB/
  SSLMODE` at the test database (previously only `DATABASE_URL` was overridden), fixing a
  separate pre-existing gap where `ensure_readonly_role()`'s `GRANT CONNECT ON DATABASE
  {config.POSTGRES_DB}` always targeted the real (locally nonexistent) app database and
  silently failed under every test file, not just this one.

**Fixed elsewhere as a result:** several existing tests called `get_repos()` directly
(outside any Flask request, with no explicit `user_id`) to seed data -- correct before RLS
existed, now correctly refused by the fail-closed default. Updated to pass the user_id
explicitly: `tests/test_multi_tenant_isolation.py` and the `Tenants` fixture in
`tests/test_tenant_isolation_matrix.py`.

**Not done:** row-level security does not by itself stop a row from referencing another
account's *parent* row (e.g. a stage whose `job_id` points at someone else's job) --
that needs a composite foreign key (`job_id, user_id` → `jobs(id, user_id)`), flagged
already in the two-user isolation tests entry above. Also not done: applying this same
non-superuser-role pattern to the user's real local dev database (only the throwaway test
database was changed) -- left for the user to decide, since it touches real data ownership.

## RLS rollout: fixing every other affected call site (2026-09-27)

Status: **implemented and tested, nothing committed.** The full suite failed with ~100
failures the first time RLS was armed against a real non-superuser role; all are now fixed.

**Two more real production bugs found this way** (same class as the voice-query and
closed-req-stage bugs above -- a background/listener function that already receives
`user_id` explicitly but called `get_repos()` without passing it, so it silently ran with
no identity once RLS was enforced):
- `routes/scan.py`'s `run_scan_task` (the email-scan Celery task) -- 8 call sites.
- `scripts/manage_secrets.py`'s `rotate-encryption-key` and `check-encryption` commands --
  these read/write *every* account's encrypted secrets by design, so they now bracket
  `rotate_all()` with `disable_for_migrations()`/`enable_and_force()` (the same pattern
  `init_db()` uses), rather than take an identity at all.

**Test fixture fallout, all fixed, no test's actual assertions changed:**
- Repository-level unit tests that open a raw connection (`get_connection()` /
  `open_repositories()`) and create their own account now call the new
  `backend.db.row_level_security.set_current_user()` right after creating that account
  (usually inside a shared `_new_user`/`_make_user_id` helper, fixing every test in the
  file at once). Tests that operate as two different users on one connection switch the
  identity before each user's own operation.
- Tests that call a migration function standalone (`_migrate_legacy_*`, `_migrate`,
  `rotate_all`) now bracket it with `disable_for_migrations()`, mirroring `init_db()`.
- A handful of tests build a minimal Flask app and monkeypatch a route module's
  `current_user_id` directly instead of logging in for real (an established, deliberate
  pattern in this codebase for isolating one blueprint). `get_repos()` doesn't see that
  monkeypatch -- it reads the actual Flask session -- so these fixtures now also set
  `session["user_id"]`, via a `before_request` hook when the test reassigns identity
  mid-test (`tests/test_candidate_profile.py`, `tests/test_google_tokens.py`) or a one-time
  `session_transaction()` otherwise (`tests/test_resume_file_service.py`).
- `tests/conftest.py`'s `pg_dsn` fixture now also points `config.POSTGRES_HOST/PORT/DB/
  SSLMODE` at the test database, not just `DATABASE_URL` -- fixing a separate pre-existing
  gap where `ensure_readonly_role()` always targeted the real (locally nonexistent) app
  database and silently failed under every test file, not just the ones exercising it.

**Files touched:** `app.py`, `routes/{applications,scan,voice}.py`, `backend/tasks.py`,
`backend/cover_letter/{cover_letter_generator,cover_letter_learning}.py`,
`backend/email/gmail_client.py`, `backend/scanning/sources/greenhouse_session_store.py`,
`backend/scoring/fit_scorer.py`, `scripts/manage_secrets.py`, and the test files listed in
git status. Full suite green as of this entry.

**Process note:** two separate full-suite runs were interfered with by other commands I ran
concurrently against the same shared local Postgres/venv (a `uv sync` mid-run, and an
earlier foreground test file run) -- both produced spurious failures unrelated to this
work. Nothing should touch the test database or the `.venv` while a background suite run
is in flight.
