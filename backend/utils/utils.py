# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# LICENSE: Personal Use Only
# This source code is made available for personal, non-commercial use only.
# You may read, study, and run this code for personal purposes.
# You may NOT:
#   - Use this code, in whole or in part, in any commercial product or service
#   - Sell, sublicense, or otherwise monetize this code or derivatives of it
#   - Redistribute this code without explicit written permission from ZombieTech LLC
#
# No warranty is provided, express or implied. Use at your own risk.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-sonnet-5 | 2026-08-13
"""Generic, no-domain-owner helpers shared across every blueprint.

This file used to hold every cross-blueprint helper regardless of domain
(scanning, scoring, email, cover letter). Those have been split out into
their own domain packages (see backend/utils/company_urls.py,
backend/scanning/, backend/scoring/, backend/email/, backend/cover_letter/).
What remains here is genuinely domain-less: DB connection handling, generic
type coercion, AI-provider error formatting, and the CORS/CSRF guard — used
by nearly every route file, owned by none of them. Keeping this file free of
domain imports is deliberate: several domain packages already import from
this module, so it must stay a leaf to avoid import cycles (see
backend/utils/url_safety.py and backend/utils/company_urls.py for the same
pattern applied to other shared helpers).
"""

from __future__ import annotations

import re
from collections.abc import Generator
from contextlib import contextmanager
from urllib.parse import urlparse

import config
from backend.ai_provider import AIProviderError
from backend.db import open_repositories

ACTIVE_JOB_STATUSES: frozenset[str] = frozenset(
    {
        "pending_review",
        "triage",
        "possible_duplicate",
    }
)

FIT_PANEL_STATUSES: frozenset[str] = frozenset(
    {
        "pending_review",
        "triage",
        "skipped",
    }
)


@contextmanager
def get_repos() -> Generator[dict, None, None]:
    """Open a DB connection and yield repos; always closes the connection on exit."""
    repos = open_repositories(config.DATABASE_URL)
    try:
        yield repos
    finally:
        repos["conn"].close()


def parse_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    raise ValueError("Invalid boolean value")


def parse_positive_int(value: object, *, default: int, field_name: str) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return parsed


def safe_error_message(exc: BaseException, fallback: str) -> str:
    if isinstance(exc, AIProviderError):
        return str(exc) or fallback
    return fallback


def ai_error_http_status(exc: BaseException, fallback_status: int = 500) -> int:
    if isinstance(exc, AIProviderError):
        status_code = exc.status_code
        if status_code == 429:
            return 429
        if isinstance(status_code, int) and 400 <= status_code <= 599:
            return 502
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return 429
    if isinstance(status_code, int) and 400 <= status_code <= 599:
        return 502
    return fallback_status


def is_trusted_api_write_origin(
    source: str | None, self_origin: str, cors_origins: list[str]
) -> bool:
    """True if *source* (an Origin or Referer header value) is safe to accept a write from.

    *source* is same-origin (matches *self_origin*) or explicitly CORS-allowlisted.
    A missing *source* is always trusted: non-browser clients (tests, curl, internal
    tooling) never send a forgeable Origin/Referer, so there is nothing to check, and
    a malicious cross-origin browser request always sets one of the two.
    """
    if not source:
        return True
    parsed = urlparse(source)
    source_origin = f"{parsed.scheme}://{parsed.netloc}"
    return source_origin == self_origin or source_origin in cors_origins


def normalize_stage_status(raw_status: object) -> str:
    status = str(raw_status or "").strip()
    normalized = re.sub(r"[^a-z0-9]+", "_", status.lower()).strip("_")
    aliases = {
        "applied": "applied",
        "rejected": "rejected",
        "employer_rejected": "rejected",
        "waiting": "waiting_call",
        "waiting_for_call": "waiting_call",
        "waiting_call": "waiting_call",
        "waiting_for_2nd": "waiting_second",
        "waiting_2nd": "waiting_second",
        "waiting_for_second": "waiting_second",
        "waiting_second": "waiting_second",
        "need_to_do_eval": "eval_1",
        "eval": "eval_1",
        "eval_test": "eval_1",
        "eval_1": "eval_1",
        "eval_2": "eval_2",
        "eval_3": "eval_3",
        "job_was_filled": "job_filled",
        "job_filled": "job_filled",
        "filled": "job_filled",
        "ghosted": "ghosted",
    }
    return aliases.get(normalized, normalized)
