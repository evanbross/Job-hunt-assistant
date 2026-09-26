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
- Resume PDF is still a single shared file (`uploads/resume.pdf`): the
  listener now serves the authenticated user's DB row but whichever file was
  written last. This is item 2 and is the next thing to do.
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
