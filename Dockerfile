# syntax=docker/dockerfile:1
# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================
#
# ai: claude-opus-5-5 | 2026-11-06
# The application image (docs/cloud-deployment-readiness-plan.md 1.7). One image for
# every process; the first argument picks which (deploy/docker-entrypoint.sh):
# web | listener | worker | maintenance | beat | migrate | backup.
#
#   docker build --build-context zt-common=../infra/zt-common \n#       -t registry.example.com/job-hunt-assistant:1.0.0 .
#
# (scripts/release.py build passes the build context itself.)
#
# Built to run with a read-only root filesystem and a tmpfs at /tmp
# (deploy/docker-compose.prod.yml): nothing here expects to write anywhere else.

ARG PYTHON_IMAGE=python:3.14-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.9.4

FROM ${UV_IMAGE} AS uv

# ai: claude-opus-5-5 | 2026-11-19
# ── zt-common, the shared package in the sibling ../infra repo ─────────────────
# It's outside this build context, so the build gets it as a named context
# (--build-context zt-common=../infra/zt-common), which replaces this stage. The stage
# itself only runs when that was left out, to say so instead of failing obscurely.
FROM ${UV_IMAGE} AS zt-common
RUN echo "zt-common is missing: build with --build-context zt-common=../infra/zt-common" >&2 && exit 1

# ── The built single-page frontend, served by the web process ───────────────────
FROM ${NODE_IMAGE} AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# The skip-reason list the backend reads too (one copy, imported from ../../../shared).
COPY shared/ /src/shared/
RUN npm run build

# ── Python dependencies, exactly as uv.lock pins them (no dev group) ───────────
FROM ${PYTHON_IMAGE} AS python-deps
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /src
COPY pyproject.toml uv.lock ./
# ai: claude-opus-5-5 | 2026-11-19
# Where pyproject's ../infra/zt-common path points from /src. --no-editable installs a
# real copy into /opt/venv, since /infra isn't carried into the runtime stage.
COPY --from=zt-common pyproject.toml /infra/zt-common/pyproject.toml
COPY --from=zt-common src/ /infra/zt-common/src/
# The app runs from source (/app on PYTHONPATH), so the project itself isn't installed.
RUN uv sync --frozen --no-dev --no-install-project --no-editable

# ── Runtime ─────────────────────────────────────────────────────────────────────
FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp
COPY --from=python-deps /opt/venv /opt/venv

# No browser in the image (plan 2.5): the only Playwright user left is the desktop's
# Wellfound fetcher, which needs a visible window; hosted, the email scan queues
# Wellfound jobs for the browser extension instead. The playwright package is still
# installed (routes/scan.py imports the fetcher's module) but never launches.

# ai: claude-opus-5-5 | 2026-11-07
# pg_dump/pg_restore and age, for the off-site backups (backend/db/backup.py). pg_dump
# must be at least the server's major version, so the client comes from the PostgreSQL
# project's own repository (PGDG), added with the setup script and signing key that
# Debian's postgresql-common package ships -- so the key comes through Debian's signed
# archive, not a separate download. Set PG_CLIENT_MAJOR to the production server's version.
ARG PG_CLIENT_MAJOR=17
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates postgresql-common \
 && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
 && apt-get install -y --no-install-recommends "postgresql-client-${PG_CLIENT_MAJOR}" age \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 jha \
 && useradd --system --uid 10001 --gid jha --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin jha

WORKDIR /app
# Owned by root and not writable by the app user, on top of the read-only filesystem.
COPY config.py wsgi.py gunicorn.conf.py ./
COPY backend/ backend/
COPY routes/ routes/
COPY web/ web/
COPY shared/ shared/
COPY deploy/docker-entrypoint.sh deploy/docker-entrypoint.sh
COPY --from=frontend /src/frontend/dist frontend/dist
# Compiled now, since the running container can't write __pycache__.
RUN python -m compileall -q -j 0 /app

# ai: claude-opus-5-5 | 2026-11-08
# Which release and commit this is (scripts/release.py build sets both). Last, so a new
# release only rebuilds this layer. The revision label is how `release.py deploy` finds
# the commit whose compose file goes with the image, and errors are reported under the
# release (backend/error_tracking.py), so neither is set by hand.
ARG APP_RELEASE=""
ARG APP_REVISION=""
LABEL org.opencontainers.image.version="${APP_RELEASE}" \
      org.opencontainers.image.revision="${APP_REVISION}"
ENV SENTRY_RELEASE="${APP_RELEASE}"

USER 10001:10001
EXPOSE 5004 8765 8766
ENTRYPOINT ["/bin/sh", "/app/deploy/docker-entrypoint.sh"]
CMD ["web"]
