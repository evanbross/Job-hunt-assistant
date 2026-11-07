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
cp deploy/.env.backup.example deploy/.env.backup           # the backup bucket and age public keys
python scripts/generate_redis_tls.py                       # once: writes deploy/secrets/redis-tls/

# Every deploy, in this order (a backup first, so a bad migration can be undone):
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production run --rm maintenance backup run
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production run --rm migrate
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production up -d
```

Compose refuses to start if `APP_DOMAIN`, `ACME_EMAIL`, `REDIS_PASSWORD`,
`READONLY_DB_PASSWORD` or `JHA_IMAGE` is unset.
`deploy/.env.production`, `deploy/.env.migrate`, `deploy/.env.backup` and `deploy/secrets/`
are git-ignored.

### First login and who can sign up

The first `migrate` against an empty database creates the bootstrap admin account,
`admin@localhost`, and prints its one-time password in that command's output (it is not
stored anywhere). To choose it instead, set `BOOTSTRAP_ADMIN_PASSWORD` (12+ characters)
in `deploy/.env.migrate` before that first run. Logging in as it forces you to set a real
email and password before anything else works.

Registration is **invite-only** from the start (`registration_invite_only`, on by
default), so strangers can't sign up before you have looked at the install. Invite people
under Settings > System > Invites, then tell them to register with that address; each
invite is used up when its account is created. Turn invite-only off in System > General
to open sign-ups to anyone.

### The application image

`APP_MODE: hosted` (set on every application service) is what makes the app behave as
a multi-user server rather than the desktop app (`backend/app_mode.py`): documents are
downloads, not files in a server folder; no debug dumps or logs go to disk; the
read-only DB role's password must be the `READONLY_DB_PASSWORD` secret.

The public address is `https://<APP_DOMAIN>`: compose sets `APP_BASE_URL` from
`APP_DOMAIN` for the app and the worker, and everything users are sent to derives from
it -- links in account emails, the Google OAuth redirect URI
(`https://<APP_DOMAIN>/api/auth/google/callback`, which must be registered on the Google
OAuth client) and the extension's listener URL. The app and worker refuse to start if it
is not https on a real domain (localhost, `*.localhost` and IP addresses are rejected),
and the app refuses to start without the built frontend (`backend/public_urls.py`).
Settings that only configure a desktop install -- the app base URL, the server and
listener bind addresses and ports -- are hidden, since the deployment fixes them.

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

`beat` (`celery -A backend.celery_app beat`) queues four ticks
(`backend/celery_app.py`'s `beat_schedule`), and once more each as it starts so nothing
that fell due while it was down waits a full interval:

| Tick | Every | Runs on | Does |
|---|---|---|---|
| Career-site scan | `CAREER_SITE_SCAN_INTERVAL_MINUTES` (1440) | `worker` | Starts a scan for each account with a site due |
| JD parser health check | 15 minutes | `worker` | Starts a run for each account whose configured time has passed |
| Data lifecycle sweep | `DATA_LIFECYCLE_SWEEP_INTERVAL_HOURS` (24) | `maintenance` | Deletes accounts past their grace period; prunes old log rows |
| Database backup | 1 hour (backs up once the last success is `BACKUP_INTERVAL_HOURS` old) | `maintenance` | An encrypted off-site dump ("Backups and restore" below) |

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

### Backups and restore

There are two layers, because they fail differently:

1. **Neon's point-in-time restore** (the provider's). It restores the database to any
   moment inside the plan's history window, and is the first thing to reach for after a
   bad deploy or a mistaken delete. Check the window on your plan.
2. **Off-site dumps** (`backend/db/backup.py`), for what PITR can't cover: the Neon
   project or account lost, or damage noticed after the window. The `maintenance` worker
   takes one every `BACKUP_INTERVAL_HOURS` (24), and every deploy takes one before it
   migrates. Each is a `pg_dump --format=custom --schema=public` taken as the schema owner,
   encrypted with `age` to the public keys in `BACKUP_AGE_RECIPIENTS`, and streamed to an
   S3-compatible bucket as `<prefix>YYYY/MM/jha-<UTC time>-<schema>.dump.age`. Nothing is
   written to disk, and a dump that fails partway is aborted rather than stored as a short
   file. Every run is recorded in the `backup_runs` table (`backup status` lists them); a
   failure is logged as an error and retried at the next hourly tick.

Postgres is the only state a hosted install keeps: documents live in it, Redis holds only
in-flight work and rate-limit counters, and Caddy's certificates get reissued. So one dump
is a whole backup.

**Setting it up** (`deploy/.env.backup`, copied from `.env.backup.example`).
`uv run python scripts/setup_backup_bucket.py r2` (or `b2`, `s3`) does most of this
interactively: it creates the bucket and retention rule with a one-time admin key,
generates the age key pair, checks that the server key can write, and writes
`deploy/.env.backup`.

* Use a bucket at a different provider or account from the database. Give its key the
  narrowest access the provider allows: ideally write-only, with no delete (AWS:
  `s3:PutObject` + `s3:AbortMultipartUpload`; B2: `writeFiles`). Set retention as the
  bucket's lifecycle rule (e.g. 35 days), and turn on object lock where it's offered, so a
  leaked key can't delete recent backups. The app never deletes or lists backups.
* Run `age-keygen -o backup-identity.txt` on **your own machine** (or `docker run --rm
  --entrypoint age-keygen <image>`). Put the public key it prints (`age1...`) in
  `BACKUP_AGE_RECIPIENTS`. Add a second recipient whose identity is sealed somewhere else,
  so losing one key doesn't lose every backup. The server holds only public keys: it can
  write backups but never read one.
* `maintenance` refuses to start unless `backup check-config` passes: the settings are
  complete, and `pg_dump`, `pg_restore` and `age` are present.
* The image's `pg_dump` must be at least the server's major version, so build with
  `--build-arg PG_CLIENT_MAJOR=<Neon's version>` (default 17). `DATABASE_OWNER_URL` must
  be Neon's **direct** endpoint, not the pooled one, because pg_dump needs a session.

**What must be kept, and where.** A backup is useless without two secrets, and both must
be kept **apart from the server and the bucket**: for example, in a password manager plus
a sealed offline copy.

| Secret | Without it |
|---|---|
| The age identity file(s) | The backups can't be decrypted |
| `SETTINGS_ENCRYPTION_KEY` (and any `_FALLBACKS`) | A restored database's API keys, Google tokens and resumes can't be decrypted |

`verify` (below) proves that the key you hold actually opens a backup. Keep the other
production secrets (`FLASK_SECRET_KEY`, the database passwords) in the same place. They
can be replaced, but replacing them signs everyone out and needs a new runtime login.

**Restoring.** Do this once as a drill before launch, then periodically. Restore into a
**new, empty** database (a fresh Neon database or branch), never one in use; the command
refuses a database that has tables. From any machine with Docker, with the identity file
in the current directory:

```bash
IMAGE=registry.example.com/job-hunt-assistant:1.0.0
# 1. Restore. Reading the bucket needs a separate read key, not the server's write key.
docker run --rm --env-file deploy/.env.backup -e BACKUP_S3_ACCESS_KEY_ID=<read key> \
  -e BACKUP_S3_SECRET_ACCESS_KEY=<read secret> -v "$PWD/backup-identity.txt:/id.txt:ro" \
  $IMAGE backup restore --latest --identity /id.txt --target-url "$RESTORE_OWNER_URL"
# 2. Migrate it. This creates the runtime role's grants (the dump carries no roles or grants).
docker run --rm -e DATABASE_OWNER_URL="$RESTORE_OWNER_URL" -e DATABASE_URL="$RESTORE_APP_URL" \
  -e READONLY_DB_PASSWORD=... $IMAGE migrate
# 3. Verify: migrated by this image, row-level security armed, every secret decrypts.
docker run --rm -e SETTINGS_ENCRYPTION_KEY=... $IMAGE backup verify --database-url "$RESTORE_OWNER_URL"
```

`verify` prints each table's row count, to compare with production. Then point
`DATABASE_OWNER_URL` and `DATABASE_URL` at the restored database and deploy as usual. For
an older backup use `--key <object key>` (from `backup list`); for a downloaded copy use
`--file`.

Restoring doesn't depend on this code: the object is a plain age-encrypted pg_dump
archive.

```bash
aws s3 cp "s3://<bucket>/<key>" - --endpoint-url "<endpoint>" \
  | age --decrypt --identity backup-identity.txt \
  | pg_restore --no-owner --no-privileges --exit-on-error --single-transaction --dbname "$RESTORE_OWNER_URL"
```

The archive contains the `public` schema itself, so first drop the new database's empty
`public` schema with `DROP SCHEMA public;`. `backup restore` does this for you.

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
`https://listener.<APP_DOMAIN>` (Settings > Browser Extension shows users this URL); it
derives the WebSocket host by inserting `-ws` after the first label, which matches the
Caddyfile.

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
3. **Database provisioning, monitoring, and log shipping.** (Backups: "Backups and
   restore" above.)
