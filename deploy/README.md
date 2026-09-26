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
python scripts/generate_redis_tls.py                       # once: writes deploy/secrets/redis-tls/
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.production up -d
```

Compose refuses to start if `APP_DOMAIN`, `ACME_EMAIL`, or `REDIS_PASSWORD` is unset.
`deploy/.env.production` and `deploy/secrets/` are git-ignored. On Linux, run the
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

## Browser extension

The extension talks to two endpoints, exposed as `listener.<APP_DOMAIN>` (HTTP) and
`listener-ws.<APP_DOMAIN>` (WebSocket). Set the extension's listener URL to
`https://listener.<APP_DOMAIN>`. Note the extension currently derives the WebSocket
host by rewriting `*.localhost` names only, so hosted use needs that derivation
extended before the push channel works; HTTP polling works regardless.

## What is not included

1. **The application image.** `app.py` is a desktop launcher: it rebuilds the frontend,
   starts a Vite dev server, spawns its own Celery worker, and registers itself with the
   dev Caddy. A production image needs a real WSGI server (Gunicorn or Waitress), the
   extension listener and queue WebSocket as managed processes, the built frontend
   served from the image, and a container entrypoint that does none of the desktop
   work. `JHA_IMAGE` in the compose file is a placeholder until that exists.
2. **Reading secrets from files.** Secrets are environment variables. Supporting the
   `*_FILE` convention (Docker/Kubernetes secrets) is a small follow-up.
3. **Trusted-proxy handling in the app** (correct client addresses for rate limiting
   behind Caddy): item 5 in `docs/code-review-remediation-plan-2026-09-26.md`.
4. **Database provisioning, backups, monitoring, and log shipping.**
