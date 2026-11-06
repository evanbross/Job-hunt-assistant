# Production deployment reference

`docker-compose.prod.yml` and `Caddyfile` describe the parts of a production
deployment that are pure infrastructure: the reverse proxy, the message broker,
and how they are wired together. They are **not** a complete deployment: read
"What is not included" before relying on them.

## Shape

```
internet ──443──> caddy ──edge──> app ──backend──> redis <──backend── worker
                    │               │                                   │
                    └──egress───────┴────────────egress─────────────────┘
                (Let's Encrypt)   (Postgres, AI providers, Gmail)
```

| Network | Members | Purpose |
|---|---|---|
| `edge` | caddy, app | The only path from the internet to the app |
| `backend` (`internal: true`) | app, worker, redis | Broker traffic. Containers here have **no route to the internet**, and nothing outside can reach them |
| `egress` | caddy, app, worker | Outbound calls only; no ports are published on it |

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
cp deploy/.env.production.example deploy/.env.production   # fill in every value
cp deploy/.env.migrate.example deploy/.env.migrate         # the schema owner's DB URL
python scripts/generate_redis_tls.py                       # once: writes deploy/secrets/redis-tls/

# Every deploy, in this order:
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production run --rm migrate
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production up -d
```

Compose refuses to start if `APP_DOMAIN`, `ACME_EMAIL`, or `REDIS_PASSWORD` is unset.
`deploy/.env.production`, `deploy/.env.migrate` and `deploy/secrets/` are git-ignored.

### Database roles and migrations

The app uses two Postgres roles (`config.py`, `backend/db/app_role.py`):

| Role | Configured in | Used by | Row-level security |
|---|---|---|---|
| Owner (owns every table) | `DATABASE_OWNER_URL`, `.env.migrate` only | the `migrate` step | Exempt: migrations and backfills cross accounts |
| Runtime | `DATABASE_URL` | `app`, `worker` | Always applies |

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
old app, run `migrate`, then start the new one with its runtime-role `DATABASE_URL`. On Linux, run the
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
`https://listener.<APP_DOMAIN>`. Note the extension currently derives the WebSocket
host by rewriting `*.localhost` names only, so hosted use needs that derivation
extended before the push channel works; HTTP polling works regardless.

## What is not included

1. **The application image.** `app.py` is a desktop launcher: it rebuilds the frontend,
   starts a Vite dev server, spawns its own Celery worker, and registers itself with the
   dev Caddy. A production image needs a real WSGI server, the extension listener and
   queue WebSocket as managed processes, the built frontend served from the image, and
   a container entrypoint that does none of the desktop work. `JHA_IMAGE` in the
   compose file is a placeholder until that exists.

   The web server part exists: `wsgi.py` (`gunicorn -c gunicorn.conf.py wsgi:app`, the
   compose `app` command) builds the app with `web.create_app()` and runs
   `web.serving.prepare_for_serving()`. It does not start the extension listener and
   WebSocket server, so `listener.` / `listener-ws.` in the Caddyfile have no upstream
   until those run as their own process (plan item 1.3), and it starts no schedulers
   (item 1.4).

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
