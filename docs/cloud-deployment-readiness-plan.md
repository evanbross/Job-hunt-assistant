# Cloud Deployment Readiness Plan

Status: proposal / not started
Author: Claude (planning session, 2026-11-05), for Evan Ross
Scope: everything between "multi-tenant app that runs on Evan's desktop" and
"hosted service other people can sign up for and use"

---

## Where things stand

A lot of the hard multi-tenant work is **already done**, so this plan is mostly about
the runtime, the remaining single-machine assumptions, and the external services a
public launch needs. Already in place:

| Area | State |
|---|---|
| Tenancy | Every user table has `user_id`; every repo method takes it; Postgres **row-level security** is a fail-closed backstop (`backend/db/row_level_security.py`) |
| Accounts | Email-verified registration, forgot-password, email change, audit log, data export, deletion with a grace period (`routes/session_auth.py`, `backend/security/data_lifecycle.py`) |
| Secrets | `FLASK_SECRET_KEY` / `SETTINGS_ENCRYPTION_KEY` validated at startup, rotation tooling (`docs/secrets-and-key-rotation.md`) |
| Per-user secrets | Google tokens, AI keys, resume PDFs encrypted in the DB, not on disk |
| Abuse | Redis-backed rate limiter, trusted-proxy client IP, CSRF origin guard |
| Extension | Per-user bearer tokens (`backend/extension_auth`, `routes/extension_tokens.py`); WebSocket host derivation already handles hosted domains |
| Live updates | SSE over Redis pub/sub (`routes/events.py`), so it works across processes |
| Infra reference | `deploy/docker-compose.prod.yml` + `deploy/Caddyfile`: TLS, network isolation, Redis TLS+auth |
| Pagination | Keyset cursors on the large lists |

What's missing falls into six groups below. Phases 1–2 are hard blockers; nothing
can be deployed until they're done. Phases 3–5 are blockers for *letting other people
in*. Phase 6 is the go-live gate.

---

## Phase 0 — Decisions to make first

These change what the later phases look like, so settle them before writing code.

1. **Hosting shape. Recommendation: one Linux VM running the existing
   `deploy/docker-compose.prod.yml`, with Neon as managed Postgres.**
   The compose file, Caddy config, network split and Redis TLS already exist and were
   designed for this. A $20–40/month VM (Hetzner, DigitalOcean, Lightsail) handles a
   few dozen users easily. A PaaS (Fly.io, Render) or ECS/Cloud Run is the right
   *next* step once there's real load or a need for zero-downtime deploys, but moving
   there first means throwing away working, reviewed infra config. Phase 1 is written
   so the image works on either.
2. **Who is this for, initially?** Invite-only beta (a handful of people you know) vs.
   open public signup. This decides how urgently Phase 3 (Google verification) blocks
   you — see 3.1. Recommendation: **invite-only beta first**, with a registration
   allowlist (4.6), because Google verification can take weeks.
3. **Which scanning sources are hosted features?** Some sources only work on a
   desktop with a visible browser (see 2.5). Decide per source: keep (via the
   extension), port to headless, or hide in hosted mode.
4. **Do you supply AI, or do users bring keys?** Today users bring their own keys,
   which is why per-tenant AI cost quotas were deliberately not built
   (`deploy/README.md`). If you ever supply a house key, quotas and billing become a
   blocker.

---

## Phase 1 — A real production runtime (blocker)

`app.py` is a desktop launcher: it rebuilds the frontend, spawns Vite, spawns its own
Celery worker, registers with the dev Caddy, and starts several background threads
inside the web process. None of that belongs in a server.

### 1.1 Split `app.py` into an app factory and a desktop launcher — done 2026-11-05
`web/factory.py` `create_app()` (no I/O), `web/request_guards.py`, `web/swagger_docs.py`,
`web/serving.py` (secrets, cookie flags, trusted proxies), `web/extension_listener.py`
(listener + WebSocket wiring). `app.py` is now only the desktop launcher's `main()`.
Tests build the app from the factory (`flask_app` fixture in `tests/conftest.py`).
Not done here: a WSGI entry point (1.2) and moving migrations out of startup (1.6).

Original scope:
- Extract a `create_app()` (blueprint registration, `before_request` guards, CORS,
  proxy middleware, error handlers) into its own module with **no side effects at
  import time**.
- Keep `app.py` (or `launcher.py`) as the desktop entry point that calls
  `create_app()` and then does the Vite/Celery/Caddy work.
- **Why:** gunicorn imports the app in every worker. Anything at import time — DB
  migrations, starting threads, spawning subprocesses — runs N times, concurrently.

### 1.2 Pick and wire a WSGI server — done 2026-11-06
`wsgi.py` + `gunicorn.conf.py` (gthread, `GUNICORN_WORKERS` × `GUNICORN_THREADS`,
preload off); compose `app` runs it. `wsgi.py` never migrates and starts no threads;
`web.serving.prepare_for_serving()` refuses to start on an unmigrated database or a
role RLS wouldn't apply to. gunicorn is a non-Windows dependency (it can't run there).
The old acceptance wording ("more streams than threads still answers") only holds for
gevent; with gthread, capacity is a hard ceiling, now stated in `deploy/README.md`.

Original scope:
- `deploy/README.md` already lays out the constraint: each open tab holds a
  long-lived `/api/events` SSE stream, so a small fixed thread pool (Waitress) starves.
- Recommendation: **gunicorn `gthread`**, sized `workers × threads` ≥ expected open
  tabs + headroom. It avoids gevent's monkey-patching risk with psycopg2, the
  connection pool, and Redis pub/sub. Revisit gevent only if stream count gets large.
- Add `gunicorn` to `pyproject.toml`.
- Acceptance test (already written in `deploy/README.md`): with more SSE streams open
  than worker threads, ordinary `/api/...` requests still answer promptly and a
  change in one tab still reaches the others.

### 1.3 Run the extension listener and WebSocket server as their own processes — done 2026-11-06
`python -m web.extension_listener` (`main()`), the compose `listener` service; Caddy's
`listener.`/`listener-ws.` hosts forward to it. It validates secrets, refuses an
unmigrated database or an RLS-exempt role (same check as `wsgi.py`), and exits instead of
falling back to another port, since the proxy upstream is fixed. The desktop still runs
both servers inside `app.py`. Separating the process required the import-progress part
of 1.5: listener-queued imports are now tracked in Redis
(`backend/scanning/import_progress.py`), so `/api/import/status` on any web worker sees
them.

Original scope:
- The listener (`backend/scanning/import_listener.py`, ports 8765/8766) currently
  starts from the app process. In production it should be its own compose service
  (same image, different command), so a gunicorn worker restart doesn't kill it and
  multiple workers don't each try to bind the port.
- Update `deploy/Caddyfile`'s `listener.` / `listener-ws.` upstreams to the new
  service name.

### 1.4 Move the in-process schedulers to Celery beat — done 2026-11-06
`backend/celery_app.py`'s `beat_schedule` ticks the career-site scan, the JD health
check and the data lifecycle sweep; compose has one `beat` service, and the desktop
`app.py` spawns beat next to its worker. Each tick fans out per account
(`backend/scanning/schedulers.py` `for_each_active_account`); beat also sends every tick
once on start, which keeps the old "catch up on next launch" behaviour. The sweep runs
on its own `maintenance` queue, consumed only by a worker holding the owner's URL, and
refuses a role RLS applies to (`assert_exempt_from_rls`). Moving the ticks out of the
web process needed the career-scan and JD-health-check parts of 1.5's per-user task
registry (`backend/active_tasks.py`, `jha:task:<kind>:<user_id>`), or a manual run in a
web worker and a scheduled one in a Celery worker could both start. The candidate
profile file watcher stays a desktop-only thread (2.3).

Original scope:
- `start_career_site_scheduler`, `start_jd_health_check_scheduler`,
  `start_data_lifecycle_scheduler` and `start_candidate_profile_file_watcher` are
  `while True: sleep()` threads started in the web process (`app.py:~1110`).
- With 4 gunicorn workers you'd get 4 copies of every sweep — including account
  deletion. Make them Celery beat tasks (one `beat` service in compose) that fan out
  per user. This also matches the standing event-driven preference: beat is the
  slow fallback tick, tasks are the work.
- The file watcher is a separate problem — see 2.3.

### 1.5 Move per-process state into Redis or the DB — done 2026-11-06
Nothing a second web or worker process needs to see is kept in one process's memory any
more. Each item, and where it went:

- `routes/import_.py` `_active_imports` (import progress) — Redis,
  `backend/scanning/import_progress.py` (with 1.3), capped per account.
- The `_*_last_task_id_by_user` / `_*_submitted_at_by_user` pairs in `scan.py`,
  `board_search.py`, `fit.py`, `greenhouse.py`, `applications.py`, `career_sites.py`,
  `jd_health_check.py` — `backend/active_tasks.py`, a Redis registry keyed
  `jha:task:<kind>:<user_id>` (24h TTL) whose check-then-dispatch runs under a Redis
  lock, so two processes can't both start a run. Submit times are wall-clock now
  (`is_celery_task_active`), since they cross processes. Rescan-all clears the old
  results inside that check, so it can't delete under a scan another worker started.
- `routes/auth.py` `_pending_auths` (OAuth state) — Redis,
  `backend/email/google_oauth_pending.py`, 10-minute TTL, taken with GET+DELETE in one
  MULTI so a state token is good for exactly one callback, on whichever worker.
- `routes/applications.py` `_host_fetch_last_at` (per-host politeness spacing) — Redis,
  `backend/scanning/host_fetch_spacing.py`: `SET NX PX` per host, so the closed-req
  scan's 1-second spacing holds across every Celery worker process.
- The `*State()` singletons in `routes/jobanni.py` and `routes/jobs.py` (WTTJ repair,
  broken-import rescan, import repair) — these were threads in the web process with
  one state shared by **every account** (one user's results, company names included,
  showed in another's status, and one user's run blocked everyone's). Now Celery tasks
  with a state per run, published as task progress (`backend/task_progress.py`) and
  found per account through the registry.
- Every module that talks to Redis builds its client through
  `backend/security/redis_tls.py:app_redis_client()`, so TLS verification is set in
  one place.

Still per-process, deliberately: `routes/greenhouse.py`'s `_login_state`. That flow
opens a visible browser on the machine running the app, so it only means anything on
the desktop; hosted mode should switch it off (2.7's `APP_MODE`).

### 1.6 Migrations as a release step, not a startup step — done 2026-11-06
`python -m backend.db.migrate` (compose `migrate` service, its own profile) is the deploy
step; it records a hash of the migration source in `schema_state`, and serving
processes refuse to start on a mismatch (`backend/db/schema_version.py`). The RLS
problem was fixed at the root rather than by sequencing: two DB roles. The owner
(`DATABASE_OWNER_URL`) runs migrations and cross-account maintenance and is exempt
from RLS by design; the app serves as a separate runtime role (`DATABASE_URL`) that
RLS always applies to and that migrate creates and grants. RLS is never switched off
any more — that also fixed the account-deletion sweep and key rotation, which used the
same global disable/enable bracket. Moving the hand-rolled migrations to Alembic was
not done (not needed for the release step).

Original scope:
- `init_db()` runs migrations on every app start, and the RLS bracket
  (`disable_for_migrations()` → `enable_and_force()`) turns RLS **off globally**
  while it runs. With more than one instance (or a rolling restart), instance B
  starting up disables RLS while instance A is serving traffic.
- Make migrations a one-shot `migrate` command run once per deploy (compose
  `run --rm app migrate`, or a release-phase job on a PaaS). App startup should only
  *verify* the schema version and refuse to start if it's behind.
- Longer term, consider moving the 120+ hand-rolled `ALTER TABLE` steps in
  `backend/db/schema.py` to versioned migrations (Alembic or numbered SQL files) so a
  deploy can say exactly which migrations it ran. Not a blocker if the one-shot
  command exists.

### 1.7 Dockerfile and image — done 2026-11-06
`Dockerfile` (multi-stage: frontend build, `uv sync --frozen --no-dev`, runtime as uid
10001 with Chromium's headless shell) and `deploy/docker-entrypoint.sh` (`web`,
`listener`, `worker`, `maintenance`, `beat`, `migrate`). `.dockerignore` is an allowlist,
since the repo directory holds personal data. Every application container in
`deploy/docker-compose.prod.yml` runs read-only with a tmpfs `/tmp`; logs were already
on stdout. `GET /api/system/live` (no I/O) and `GET /api/system/ready` (DB + Redis) are
new and unauthenticated; compose healthchecks use readiness for `app`, `/health` for the
listener and `celery inspect ping` for the workers, and Caddy waits for `app` and
`listener` to be healthy. Building it surfaced 2.1, done here too.

Smoke-tested end to end with the built image against a throwaway Postgres: `migrate` on
an empty database, then `web` (ready/live/SPA, login gate), `listener`, `worker`,
`maintenance` and `beat` (startup ticks ran on the right queues), all read-only as uid
10001. That first fresh migrate failed: an index on `jobs.user_id` had been added to
`create_tables()`, which runs before the column exists, the same mistake fixed once on
2026-10-08. Fixed, and `tests/test_fresh_database_migrate.py` now catches the class.

Original scope:
- Multi-stage: Node stage runs `npm ci && npm run build` for `frontend/`; Python
  stage installs from `uv.lock`, copies the built `dist/`, runs as non-root.
- Entrypoint commands: `web` (gunicorn), `worker` (celery), `beat`, `listener`,
  `migrate`. Set `JHA_IMAGE` in the compose file to the built tag.
- Container filesystem should be **read-only** except a tmpfs; Phase 2 removes the
  things that currently write to disk.
- Logs to stdout, not `server.log` (see 5.3).
- Add a `/api/system/health` (already exists) liveness check and a readiness check
  that verifies DB + Redis, and wire both into compose `healthcheck`.

---

## Phase 2 — Remove remaining single-machine assumptions (blocker)

### 2.1 Read-only DB role password (`backend/db/readonly_role.py:41-91`) — done 2026-11-06
`READONLY_DB_PASSWORD` (required by compose for `migrate` and `app`) is used when set;
the desktop app keeps its generated `data/` file. Two more bugs fixed with it: the
role's DSN was built from `POSTGRES_HOST`/`POSTGRES_DB`, which are empty when a
deployment gives `DATABASE_URL` whole (it is now derived from `DATABASE_URL`), and
`GRANT CONNECT` named the same empty database (it now uses `current_database()`).
Covered by `tests/test_readonly_role_deployment.py` against real Postgres.

Original problem: the voice-query role's password is generated and stored in `data/.readonly_db_password`,
then applied with `ALTER ROLE ... PASSWORD`. In containers that file doesn't persist,
so **every new container mints a new password and breaks every other running
instance's voice queries**. Move it to a required secret (`READONLY_DB_PASSWORD`) or
use Neon's role management; the app should never `ALTER ROLE` at runtime.

### 2.2 Local filesystem writes — done 2026-11-06 (except Wellfound, see 2.5)
`APP_MODE=desktop|hosted` (`backend/app_mode.py`; 2.7's flag, added here because every
item below needs it) tells a hosted deployment from the desktop app; compose sets
`hosted`, an unknown value stops the web, listener and worker processes at startup.
What hosted mode does with each item:

- Cover letters, pitches and tailored resumes (one route) are sent to the browser as a
  download; any `folder` in the request is ignored and never stored, and the
  server-side folder picker answers 404. The three modals share one
  `saveDocument()` helper (`frontend/src/utils/saveFolderUtils.ts`) that handles both.
- Import capture dumps, the LLM prompt log and the import-repair findings doc are not
  written (the findings stay in `import_repair_history` and the run's result).
- The read-only role password must come from `READONLY_DB_PASSWORD` (2.1); there is no
  file fallback.
- The setup wizard doesn't report on prompt files: nobody edits files on the server.
  (The defaults were already in code; the "written on first run" below no longer
  happens.)
- Audit result: the remaining `DATA_DIR`/`UPLOADS_DIR`/`CREDENTIALS_DIR`/`BASE_DIR`
  uses are the legacy migration bridges in `schema.py`, the desktop-only read-only
  password file, and `WELLFOUND_SESSION_FILE` -- which goes with 2.5, since Wellfound
  needs a visible browser and can't run on a server at all.

Original list. Each of these assumes a persistent local disk owned by one user:
- `backend/scanning/import_dump.py` — raw capture dumps to a local directory. Either
  disable in production or send to object storage with a retention policy (these
  contain other sites' page content, so a retention policy matters).
- `config.DEFAULT_COVER_LETTER_PROMPT_PATH` / `DEFAULT_FIT_SCORER_PROMPT_PATH` —
  written to `BASE_DIR` on first run. Ship the defaults in the image (read-only) and
  keep any per-user overrides in the DB.
- `config.WELLFOUND_SESSION_FILE` (`credentials/wellfound_session.json`) — a shared
  session file. Per-user DB row, or drop the feature in hosted mode (see 2.5).
- `routes/cover_letter.py` — saves the generated letter to `cover_letter_folder` (a
  per-user setting) or a `folder_override` from the request, i.e. **a path on the
  server that the user chooses**: an arbitrary file write in hosted mode. Hosted mode
  must offer the letter as a download only. (The read-only container filesystem from
  1.7 already blocks the write, but the feature should not try.)
- `routes/jobs.py` `_write_repair_findings_doc` — the import-issue repair writes its
  findings into the repository's `docs/import_repair_findings.md`, so in hosted mode
  one account's findings would overwrite another's in a shared file. The same data is
  already in `import_repair_history`; show it from there (or return it with the run's
  result) and stop writing the file. Under the read-only filesystem the write fails
  and is logged; the run itself still succeeds.
- `backend/tasks.py` LLM prompt log (`tmp/jha_llm_prompts.log`) — already tolerates the
  write failing (logs at debug); remove or move to a debug flag.
- `uploads/`, `data/`, `credentials/` in general — after the above, audit with
  `grep -rn "DATA_DIR\|UPLOADS_DIR\|CREDENTIALS_DIR\|BASE_DIR /"` and confirm the only
  remaining uses are the one-time legacy migration bridges in `schema.py`.

### 2.3 Candidate-profile file sync
`backend/candidate_profile_file_sync.py` watches a path on the server's disk
(`candidate_profile_sync_path` setting) with `watchdog`. On a server, a user's
`F:/Documents/Resume/...` doesn't exist. In hosted mode: hide the setting, don't start
the watcher, and make in-app editing (or upload of a `.md`) the only path. Keep the
watcher for the desktop build.

### 2.4 Ollama SSRF (`backend/ai_provider.py:310-318`)
`ollama_base_url` is a **per-user** setting and the call passes `allow_loopback=True`.
On a server, loopback is the app container itself — a user can point it at
`http://127.0.0.1:8765` or `:5004` and make the server call its own internal
endpoints. Also, a user's local Ollama is unreachable from the cloud anyway. In
hosted mode: disable the Ollama provider (or allow only public HTTPS URLs with
loopback denied).

### 2.5 Server-side browser automation
- `greenhouse_searcher.py:301` and `wellfound_scraper.py` launch Chromium with
  `headless=False` (Wellfound documents that headless always gets a CAPTCHA). There's
  no display on a server.
- Decide per source (Phase 0, item 3). The extension-capture path already exists for
  exactly this class of site (Shipt/Workday, Wellfound) — routing these to the
  extension in hosted mode is the consistent answer.
- Any remaining headless Playwright needs the Chromium binaries in the worker image
  (large) and its own memory budget; consider a separate `scraper` worker queue so a
  browser can't starve normal tasks.
- Server-side scraping also means **all users' traffic comes from one IP** — expect
  rate limiting/blocks from job boards much sooner than on a desktop.

### 2.6 Voice query tenant isolation — verify
`routes/voice.py` runs LLM-generated SQL as the read-only role with
`app.current_user_id` set via `set_config`. Confirm `backend/sql_guard.py` rejects
function calls like `set_config(...)` / `current_setting(...)` and `SET` statements,
so generated SQL can't switch the GUC to another user's id. Add a contract test
against real Postgres that tries it.

### 2.7 Desktop-only config defaults
- `GOOGLE_OAUTH_REDIRECT_URI`, `APP_BASE_URL`, listener URLs must all be derived from
  the public domain (`https://<APP_DOMAIN>/...`). Per the "auto-selected runtime
  values" rule, the app should fail fast at startup if `APP_ENV=production` and any
  of these still points at `localhost`.
- `routes/auth.py:_app_url_on_redirect_host()` falls back to the Vite dev port when
  `FRONTEND_DIST` is missing — in production a missing dist should be a startup
  error, not a fallback.
- ~~Add an `APP_MODE=hosted|desktop` flag~~ — done with 2.2 (`backend/app_mode.py`,
  `is_hosted()`); 2.3, 2.4 and 2.5 key off it. The
  Greenhouse browser login (`routes/greenhouse.py`, opens a visible browser on the
  server) should be off in hosted mode too.

---

## Phase 3 — External services and approvals (blocker for other users)

### 3.1 Google OAuth verification — the long pole
The app requests `gmail.modify` and `gmail.send` (`config.py:233`). Both are
**restricted scopes**. For an app used by people outside your Google Workspace:
- While the OAuth consent screen is in **Testing**, you can add up to 100 named test
  users; refresh tokens expire after 7 days, so users must reconnect weekly. Fine for
  an invite-only beta.
- To go **Production**, Google requires app verification *and* an annual
  third-party security assessment (CASA) for restricted scopes. Expect weeks and
  some cost. You'll need: a verified domain, a public homepage, a privacy policy
  that meets the Google API Services User Data Policy's **Limited Use** requirements,
  and a demo video of each scope in use.
- Reduce the burden where possible: does the app genuinely need `gmail.modify`
  (labelling/moving processed emails) for hosted users, or could hosted mode use
  `gmail.readonly` (`READ_ONLY_MODE` already exists) plus the extension? `readonly`
  is still restricted, but dropping `send` and `modify` shrinks the assessment.
  Worth deciding before applying.

### 3.2 Transactional email
Registration, password reset and email change all depend on SMTP, which defaults to
local MailHog. Set up a real provider (Postmark, SES, Resend), a sending subdomain
with SPF/DKIM/DMARC, and set the SMTP system settings. Without this, nobody can
finish registering.

### 3.3 Domain, DNS, TLS
Register the domain; point `APP_DOMAIN`, `listener.`, `listener-ws.` at the VM.
Caddy handles certificates. Add CAA records for Let's Encrypt.

### 3.4 Browser extension distribution
- Publish to the Chrome Web Store (and optionally Edge/Firefox). The manifest's
  `host_permissions` include `https://*/*` — the store review will ask you to justify
  it; write that justification up front.
- Default `listenerUrl` (`background.js:701`, currently `http://jha-listener.localhost`)
  should become the hosted URL in the store build, with the localhost value kept for
  dev builds.
- The extension needs its own privacy disclosure in the store listing.
- Tampermonkey userscripts: decide whether they're supported for hosted users at all.

---

## Phase 4 — Data, backups and operations (blocker for other users)

### 4.1 Database
- A dedicated Neon project (or branch) for production, separate from the one you use
  day to day. Never point the desktop app at production data.
- Confirm Neon's point-in-time restore window on the chosen plan and **do one test
  restore** before launch.
- Size the connection pool for gunicorn workers × threads + Celery concurrency +
  listener, against Neon's connection limit (use Neon's pooled endpoint).

### 4.2 Key and secret custody
- Back up `SETTINGS_ENCRYPTION_KEY` separately from the DB (a lost key makes every
  stored token/API key/resume unreadable).
- Use a real secrets store (the VM provider's, 1Password, Doppler, or cloud secrets
  manager) to populate `deploy/.env.production`. `deploy/README.md` notes `*_FILE`
  secret support as a small follow-up — do it here.

### 4.3 Logging, errors, monitoring
- Logs to stdout in the standard format (filename:lineno), collected by the host or a
  log service. Retire `server.log` in production.
- Error tracking (Sentry or similar) for Flask + Celery, with PII scrubbing — job
  descriptions, emails and resumes must not end up in error payloads.
- Uptime check on the public URL; alert on Celery queue depth and worker liveness.

### 4.4 Deploy pipeline
CI is currently disabled for cost and replaced by the local pre-push gate
(`scripts/check.py`). For deploys you need *something* that builds the image from a
known commit: either re-enable a minimal GitLab job that builds and pushes the image
on tagged releases, or a documented local `build → push → ssh deploy` script. Either
way: tests must pass on the exact commit that's deployed, and a deploy must run the
`migrate` step before starting new containers.

### 4.5 Admin tooling
You'll need, minimally: list accounts, disable/lock an account, resend a
verification email, see an account's Celery failures. Today that's ad-hoc scripts
(`scripts/reset_login_account.py`). An admin role + a small admin page beats SSH-ing
into production — and per the "no direct DB writes to live data" rule, you shouldn't
be fixing users' data by hand.

### 4.6 Registration controls
Registration is fully open. For the beta, add an invite code or email allowlist
(system setting) so random signups can't consume your Google test-user slots or
scraping budget. Also: the placeholder `unclaimed@localhost` account that the first
registration claims must **not** exist in the production DB — make sure a fresh prod
DB doesn't seed it, or the first stranger to sign up owns the seed data.

---

## Phase 5 — Legal and product (blocker for strangers, not for friends)

- **Privacy policy and Terms of Service.** Required for Google verification and the
  Chrome Web Store anyway. The privacy policy must cover Gmail data (Limited Use),
  resumes, AI-provider data flow (users' content goes to Anthropic/OpenAI under *their*
  keys), retention, and deletion.
- **Scraping exposure.** Hosted, server-side scraping of LinkedIn and other boards is
  a different legal and ToS posture than a personal desktop tool. The extension model
  (user's own browser, user's own session) is the safer default for hosted users.
- **Onboarding.** A new user currently needs to: verify email, connect Gmail, add an
  AI key, write a candidate profile, install the extension and paste a token. A setup
  checklist page will matter more than any feature.
- **Data export/deletion** already exist — verify they cover every table added since
  they were written (a test that enumerates tables with `user_id` and asserts each is
  in the export and is deleted would keep this true going forward).
- **Billing** — out of scope until you decide to charge; nothing here depends on it.

---

## Phase 6 — Go-live gate

Before inviting anyone:

- [ ] Fresh production DB built by the `migrate` command only (no desktop data, no
      placeholder account)
- [ ] Two test accounts: confirm neither can see the other's jobs, contacts, settings,
      voice-query results, SSE events, or extension captures (including a capture sent
      with A's token while logged into B's browser)
- [ ] SSE acceptance test from 1.2 passes on the deployed stack
- [ ] Kill a gunicorn worker and the Celery worker mid-import; confirm progress and
      status recover and nothing runs twice
- [ ] Rolling restart with traffic: confirm RLS is never disabled (1.6)
- [ ] Register → verify email → reset password → delete account, end to end, with
      real email delivery
- [ ] Gmail connect works on the production domain (consent screen in Testing with
      beta users listed)
- [ ] Extension from the store build connects to the hosted listener and WebSocket
- [ ] Restore-from-backup drill done once
- [ ] Error tracking receives a test error; uptime alert fires on a test outage
- [ ] `pip-audit` / `npm audit` clean (the gate already runs these)

---

## Suggested order

1. Phase 0 decisions (a conversation, not code)
2. 1.1 app factory → 1.6 migrate command → 1.5 Redis task registry → 1.4 beat → 1.2/1.3
   process split → 1.7 image. Each is independently testable on the desktop.
3. Phase 2 items, behind the `hosted` mode flag so the desktop build is unaffected
4. Start Google verification paperwork (3.1) **in parallel with step 2** — it's the
   longest wait and doesn't need the code finished
5. Phases 3.2–3.4 and 4 while waiting on Google
6. Invite-only beta with Google in Testing mode
7. Phase 5 and Google Production verification before opening signup
