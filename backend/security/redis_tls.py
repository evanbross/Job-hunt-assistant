# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""TLS options for the Celery broker / result backend.

A `rediss://` URL turns on TLS, but Celery's default for it does not verify the
server certificate, which defeats most of the point. This returns options that
require a valid certificate chain, and refuses to proceed without a CA file to
check it against rather than silently falling back to no verification."""

from __future__ import annotations

import ssl
from typing import Any
from urllib.parse import urlparse


# ai: claude-sonnet-5 | 2026-09-26
def redis_ssl_options(redis_url: str, ca_file: str) -> dict[str, Any] | None:
    """Celery `*_use_ssl` options for *redis_url*, or None for a plain redis:// URL."""
    if urlparse(redis_url).scheme.lower() != "rediss":
        return None
    if not ca_file.strip():
        raise ValueError(
            "REDIS_URL uses rediss:// but REDIS_TLS_CA_FILE is not set. Refusing to connect "
            "without verifying the Redis server certificate."
        )
    return {"ssl_cert_reqs": ssl.CERT_REQUIRED, "ssl_ca_certs": ca_file.strip()}
