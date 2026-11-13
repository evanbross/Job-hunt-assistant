# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""WSGI entry point for a hosted deployment:

    gunicorn -c gunicorn.conf.py wsgi:app

Each gunicorn worker imports this module and gets its own app (gunicorn.conf.py keeps
preload_app off, so no database connection is ever shared across a fork). Before it
serves anything, prepare_for_serving() validates the secrets, refuses to start against
a database this version has not migrated or with a database role that row-level
security would not apply to, and installs the cookie and proxy settings.

What this deliberately does not do, unlike the desktop launcher (app.py): run
migrations (`python -m backend.db.migrate` is a separate deploy step), start the
browser-extension servers, the schedulers, a Celery worker, or Vite. Those are
separate processes in a hosted deployment (docs/cloud-deployment-readiness-plan.md).

Added 2026-11-06 (plan item 1.2).
"""

from __future__ import annotations

from backend.error_tracking import init_error_tracking
from backend.logging_setup import configure_logging
from web import create_app
from web.serving import prepare_for_serving

configure_logging()
init_error_tracking("web")  # ai: claude-opus-5-5 | 2026-11-08 -- before the app, so its errors report

app = create_app()
prepare_for_serving(app)
