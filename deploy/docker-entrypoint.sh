#!/bin/sh
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
# The application image's entrypoint: the first argument names which process this
# container is (docs/cloud-deployment-readiness-plan.md 1.7). Anything after it is
# passed on to that process. Anything else runs as given (e.g. `python -c ...`).
#
#   web          gunicorn serving the app and the built frontend (gunicorn.conf.py)
#   listener     the browser extension's HTTP listener and capture-queue WebSocket
#   worker       Celery: every background task except the sweep and the backups
#   maintenance  Celery: only the cross-account sweep and the backups (needs
#                DATABASE_OWNER_URL and the BACKUP_* settings)
#   beat         Celery beat: the timer that queues scheduled work (exactly one)
#   migrate      one-shot schema migration; run once per deploy, before the rest
#   backup       the backup command line: run | status | list | restore | verify | check-config
#                (backend/db/backup.py; scheduled backups run on `maintenance`)
#
# Queue names must match backend/celery_app.py (DEFAULT_QUEUE, MAINTENANCE_QUEUE);
# tests/test_infra_hardening.py checks that they do.
set -eu

role="${1:-web}"
[ "$#" -gt 0 ] && shift

case "$role" in
  web)
    exec gunicorn -c gunicorn.conf.py "$@" wsgi:app
    ;;
  listener)
    exec python -m web.extension_listener "$@"
    ;;
  worker)
    exec celery -A backend.celery_app worker --queues=celery --pool=threads \
      --concurrency="${CELERY_CONCURRENCY:-4}" --loglevel=info "$@"
    ;;
  maintenance)
    # It also takes the scheduled backups: refuse to start without them configured.
    python -m backend.db.backup check-config
    exec celery -A backend.celery_app worker --queues=maintenance --pool=threads \
      --concurrency=1 --loglevel=info "$@"
    ;;
  beat)
    # Schedule state is scratch on the tmpfs: beat ticks everything once on start anyway.
    exec celery -A backend.celery_app beat --schedule=/tmp/celerybeat-schedule \
      --loglevel=info "$@"
    ;;
  migrate)
    exec python -m backend.db.migrate "$@"
    ;;
  backup)
    exec python -m backend.db.backup "$@"
    ;;
  *)
    exec "$role" "$@"
    ;;
esac
