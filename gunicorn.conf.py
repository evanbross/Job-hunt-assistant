# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Gunicorn settings for `gunicorn -c gunicorn.conf.py wsgi:app` (Linux only; gunicorn
does not run on Windows, where the desktop launcher app.py uses Flask's own server).

Why gthread: every open browser tab holds one /api/events server-sent-events stream for
as long as the page is open (routes/events.py), and each stream occupies a worker thread
the whole time. Capacity is therefore WORKERS x THREADS concurrent connections, streams
included -- size it for the most tabs you expect open at once across all users, plus
headroom for ordinary requests. A small fixed pool (Waitress's default of 4 threads, or
gunicorn's sync workers) would let a handful of open tabs starve every other request.
See deploy/README.md for the acceptance check.

Added 2026-11-06 (docs/cloud-deployment-readiness-plan.md 1.2).
"""

from __future__ import annotations

import os

bind = f"0.0.0.0:{os.environ.get('JHA_HTTP_PORT', '5004')}"

worker_class = "gthread"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "32"))

# Off on purpose: with preload the app (and its connection pool) would be built once in
# the master and shared by every forked worker.
preload_app = False

# Under gthread this is how long a worker may go without checking in with the master,
# not a per-request limit, so long-lived SSE streams are unaffected by it.
timeout = 60
# How long a worker gets to finish in-flight requests on reload/shutdown. Open SSE
# streams never "finish", so this is also how long a deploy waits on them.
graceful_timeout = 20
keepalive = 5

# Client address and scheme come from backend/security/client_ip.py's
# TrustedProxyMiddleware (TRUSTED_PROXIES), installed by web.serving. Gunicorn must not
# apply its own, differently configured X-Forwarded-* handling on top of it.
forwarded_allow_ips = ""

accesslog = "-"
errorlog = "-"
