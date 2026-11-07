# Production deployment reference

`docker-compose.prod.yml` and `Caddyfile` describe the parts of a production
deployment that are pure infrastructure: the reverse proxy, the message broker,
and how they are wired together. They are **not** a complete deployment: read
"What is not included" before relying on them.

## Shape

```
internet ──443──> caddy ──edge──> app, listener ──backend──> redis <──backend── worker, maintenance, beat
                    │               │                                            │
                    └──egress───────┴──────────────────egress────────────────────┘
                (Let's Encrypt)   (Postgres, AI providers, Gmail)
```

| Network | Members | Purpose |
|---|---|---|
| `edge` | caddy, app, listener | The only path from the internet to the app |
| `backend` (`internal: true`) | app, listener, worker, maintenance, beat, redis | Broker traffic. Containers here have **no route to the internet**, and nothing outside can reach them |
| `egress` | caddy, app, listener, worker, maintenance (and the one-shot `migrate`) | Outbound calls only; no ports are published on it. `beat` is not on it: it only talks to Redis |

Only Caddy publishes ports (80, 443). Redis and the workers are unreachable from
outside by construction, not merely firewalled.

## What is different from `../infra` (the shared dev stack), and why

| | Dev stack | Production |
|---|---|---|
| Published ports | Loopback only | Caddy 80/443 only |
| Caddy admin API | On loopback, Origin-checked | **Off** (`admin off`) |
| Redis | Optional password, plain | Password **and** TLS, internal network |
| MailHog / MinIO | Present | Not present |
| Secrets | `../infra/.env` | Required variables; source from a secrets manager |

## Setting it up

```bash
docker build -t registry.example.com/job-hunt-assistant:1.0.0 .   # the repo's Dockerfile; push it
cp deploy/.env.production.example deploy/.env.production   # fill in every value (JHA_IMAGE = that tag)
cp deploy/.env.migrate.example deploy/.env.migrate         # the schema owner's DB URL
python scripts/generate_redis_tls.py                       # once: writes deploy/secrets/redis-tls/

# Every deploy, in this order:
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production run --rm migrate
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production up -d
```

Compose refuses to start if `APP_DOMAIN`, `ACME_EMAIL`, `REDIS_PASSWORD`,
`READONLY_DB_PASSWORD` or `JHA_IMAGE` is unset.
`deploy/.env.production`, `deploy/.env.migrate` and `deploy/secrets/` are git-ignored.

### The application image

`APP_MODE: hosted` (set on every application service) is what makes the app behave as
a multi-user server rather than the desktop app (`backend/app_mode.py`): documents are
downloads, not files in a server folder; no debug dumps or logs go to disk; the
read-only DB role's password must be the `READONLY_DB_PASSWORD` secret.

One image (the repository's `Dockerfile`) runs every application process; the compose
`command` names which, and `deploy/docker-entrypoint.sh` turns that into the real
command: `web` (gunicorn), `listener`, `worker`, `maintenance`, `beat`, `migrate`.

* **Build stages:** the frontend is built with `npm ci && npm run build`; the Python
  dependencies are installed from `uv.lock` (no dev group); the runtime stage gets those
  plus the source, and runs as an unprivileged user (uid 10001).
* **Read-only:** every application container runs with a read-only root filesystem and
  a tmpfs at `/tmp` (compose `read_only`/`tmpfs`). The image is compiled at build time
  and writes nothing else. A feature that still tries to write to the app directory
  (plan 2.2: import capture dumps and the import-repair findings doc, which log the
  failure and carry on; cover letters saved to a server folder, which report it) fails
  rather than leaving files in a container.
* **Build context is an allowlist** (`.dockerignore`): this directory also holds personal
  data (`credentials/`, `data/`, `uploads/`, exports), so everything is excluded and only
  what runs is let back in. A new top-level file stays out until it's listed there.
* **No browser on the server** (plan 2.5). Sites that need a real browser (Wellfound,
  which DataDome only passes with a visible window; logged-in boards such as
  my.greenhouse.io) are captured by the browser extension from the user's own browser;
  a hosted email scan queues Wellfound jobs for it.
* **Logs** go to stdout/stderr from every process (`docker compose logs`).
* **Health:** `GET /api/system/live` (the process answers; checks nothing else, so a
  database outage doesn't restart every container) and `GET /api/system/ready` (the
  database and Redis answer). Compose uses readiness for `app`, the listener's
  `/health` for `listener`, and `celery inspect ping` for the workers; Caddy only starts
  once `app` and `listener` are healthy. On a platform with separate probes, point
  liveness at `/live` and readiness at `/ready`. Both are reachable without a session.

### Database roles and migrations

The app uses two Postgres roles (`config.py`, `backend/db/app_role.py`):

| Role | Configured in | Used by | Row-level security |
|---|---|---|---|
| Owner (owns every table) | `DATABASE_OWNER_URL`, `.env.migrate` only | the `migrate` step, the `maintenance` worker | Exempt: migrations, backfills, account deletion and retention cross accounts |
| Runtime | `DATABASE_URL` | `app`, `listener`, `worker` | Always applies |

`migrate` (`python -m backend.db.migrate`) migrates as the owner, creates or updates the
runtime role from `DATABASE_URL`'s user and password, grants it data access only, and
records which code version migrated the database. The app (`wsgi.py`) refuses to start
if that record doesn't match its own code, or if `DATABASE_URL` turns out to be the
owner, a superuser, or a `BYPASSRLS` role. So: run `migrate` with the new image first,
then roll the app; an app container from a version that was never migrated will not
start.

RLS is never switched off. Before 2026-11-06, migrations switched it off for every
connection while they ran; now the owner is exempt by design and nothing else ever is.

**Switching an existing database over (once):** the first `migrate` after this change
relaxes `FORCE ROW LEVEL SECURITY` (which used to apply RLS to the owner too). Any app
still connected as the owner would become RLS-exempt from that moment, so stop the
old app, run `migrate`, then start the new one with its runtime-role `DATABASE_URL`.

### Scheduled work

`beat` (`celery -A backend.celery_app beat`) queues three ticks
(`backend/celery_app.py`'s `beat_schedule`), and once more each as it starts so nothing
that fell due while it was down waits a full interval:

| Tick | Every | Runs on | Does |
|---|---|---|---|
| Career-site scan | `CAREER_SITE_SCAN_INTERVAL_MINUTES` (1440) | `worker` | Starts a scan for each account with a site due |
| JD parser health check | 15 minutes | `worker` | Starts a run for each account whose configured time has passed |
| Data lifecycle sweep | `DATA_LIFECYCLE_SWEEP_INTERVAL_HOURS` (24) | `maintenance` | Deletes accounts past their grace period; prunes old log rows |

Run exactly one `beat`. A tick only decides; whether a run is already going for an
account is recorded in Redis (`backend/active_tasks.py`), shared by every process, so
a scheduled run never starts on top of one a user started, whichever process took
either. The sweep refuses to run as a role row-level security applies to (it would
otherwise silently delete nothing), which is why it has its own worker holding the
owner's URL instead of the ordinary `worker`. On Linux, run the
`chown`/`chmod` command the certificate script prints so the Redis private key is
readable only by the Redis user.

Generate the application secrets with `python scripts/manage_secrets.py generate`
(see `docs/secrets-and-key-rotation.md`); `REDIS_PASSWORD` can be any long random
URL-safe string.

### Redis TLS in the app

The app and workers connect with `REDIS_URL=rediss://:<password>@redis:6379/0` and
verify the server certificate against `REDIS_TLS_CA_FILE` (the compose file sets
both). A `rediss://` URL without a CA file is an error, not a silent downgrade to
unverified TLS. The certificate is issued for the host name `redis`.

## Client addresses and rate limiting

Login, registration, password reset and the extension listener throttle by client
address (and by account), so the app has to know the real address behind Caddy. Two
settings make that safe:

* Caddy has a fixed address on the `edge` network (`172.28.0.2`) and the app is given
  `TRUSTED_PROXIES=172.28.0.2`. Only a connection from that address may say which client
  address and scheme a request had (`X-Forwarded-For` / `X-Forwarded-Proto`); the same
  header from anyone else, including a caller who reaches the app directly, is ignored.
  If you change the subnet or the address, change both together.
* Caddy replaces any `X-Forwarded-For` sent by a client with the address it actually saw.
  If you put another proxy or CDN in front of Caddy, that proxy must be listed in Caddy's
  `servers { trusted_proxies ... }` block, and `TRUSTED_PROXIES` stays as the address of
  Caddy (the last hop the app can vouch for), not the CDN's.

Counters are kept in the same Redis (`jha:rl:*` keys), so every app process enforces one
limit. If Redis is unreachable the app logs a warning and falls back to per-process
counters until it returns; it neither locks users out nor stops limiting.

Not built: per-tenant AI cost quotas. Users bring their own AI provider keys, so the
spend that could run away is theirs, not the operator's. Revisit this if the service ever
supplies AI access itself.

## Browser extension

The extension talks to two endpoints, exposed as `listener.<APP_DOMAIN>` (HTTP) and
`listener-ws.<APP_DOMAIN>` (WebSocket). Set the extension's listener URL to
`https://listener.<APP_DOMAIN>`; it derives the WebSocket host by inserting `-ws` after
the first label, which matches the Caddyfile.

Both are served by the `listener` service (`python -m web.extension_listener`), its own
process rather than part of the web app, so a gunicorn worker restart never drops the
extension and only one process binds the ports. It refuses to start if it would bind
anything other than the configured ports (8765/8766), because Caddy's upstreams are
fixed: on the desktop the listener falls back to a free port and re-registers with the
dev Caddy, but in a hosted deployment that would look healthy while every extension
request failed.

Imports the extension sends are tracked in Redis (`backend/scanning/import_progress.py`,
`jha:import:*` keys), so the web app's `/api/import/status` sees an import the listener
process queued.

## What is not included

1. **A registry and CI build.** The `Dockerfile` builds the image (see "The application
   image" above), but building, scanning and pushing it is manual. A pipeline that does
   it per tag is a follow-up.

   **The server must handle long-lived responses.** Each open tab keeps one
   `/api/events` server-sent-events stream open for as long as the page is open
   (`routes/events.py`), and each stream holds a server thread the whole time. A server
   with a small fixed thread pool is the wrong choice: Waitress, for example, defaults
   to 4 threads, so four open tabs would take every thread and every other API request
   would wait. The dev server only gets away with it because `threaded=True` starts a
   new thread per connection with no cap. Use one of:

   * **Gunicorn `gthread` workers** (chosen, `gunicorn.conf.py`), with
     `GUNICORN_WORKERS × GUNICORN_THREADS` sized for the most streams you expect at
     once (one per open tab, across all users) plus headroom for ordinary requests.
     This is a hard ceiling: once every thread holds a stream, further requests wait
     until a tab closes.
   * **Gunicorn `gevent` workers**, where a stream costs a greenlet, not an OS thread,
     so the ceiling goes away. Not chosen yet: it needs monkey-patching, so first check
     that psycopg2 (it needs a green wait callback such as `psycogreen`), the
     connection pool, and the Redis pub/sub subscription all yield under gevent rather
     than blocking the worker. Revisit when open tabs approach the thread budget.

   Acceptance check for the image: with streams open on most (not all) of the
   `workers × threads` capacity (several browser tabs, or `curl -N` against the
   endpoint), ordinary `/api/...` requests still answer promptly, and a change in one
   tab still reaches the others.
2. **Reading secrets from files.** Secrets are environment variables. Supporting the
   `*_FILE` convention (Docker/Kubernetes secrets) is a small follow-up.
3. **Database provisioning, backups, monitoring, and log shipping.**
