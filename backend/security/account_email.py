# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-07
"""The one canonical form of an account email address, shared by registration, login,
password reset, email change and registration invites -- an invite only matches the
sign-up it was meant for if both sides normalize identically. A leaf module (no project
imports) so routes and services can both use it."""

from __future__ import annotations


def normalize_email(raw: str) -> str:
    return raw.strip().casefold()


def is_plausible_email(email: str) -> bool:
    """Not meant to catch every malformed address -- SMTP delivery is the real
    validator. Just enough to reject obviously-not-an-email input before spending a
    database write and an SMTP round trip on it."""
    local, _, domain = email.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".")
