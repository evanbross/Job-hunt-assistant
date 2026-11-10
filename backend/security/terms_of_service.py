# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-10
"""Which Terms of Service an account has agreed to (docs/cloud-deployment-readiness-plan.md
Phase 5).

The terms themselves are the public page at /terms
(frontend/src/components/public/TermsOfServicePage.tsx). TERMS_VERSION names the
version that page shows, and it must equal TERMS_VERSION in
frontend/src/components/public/publicSite.ts; tests/test_terms_of_service.py fails if
the two differ. Changing the terms' substance means bumping both. Every account then
has to accept the new version before it can go on using the app.

Only a hosted deployment has terms to accept: the desktop app is the user's own copy on
their own machine. Admins are exempt too, since they run the service.
"""

from __future__ import annotations

from typing import Any, Final

from backend.app_mode import is_hosted

TERMS_VERSION: Final = "2026-11-10"

ADMIN_ROLE: Final = "admin"


def terms_required() -> bool:
    """Whether accounts on this deployment must accept the terms at all."""
    return is_hosted()


def terms_acceptance_required(account: dict[str, Any]) -> bool:
    """True when *account* has not yet accepted the current TERMS_VERSION and has to."""
    if not terms_required() or account.get("role") == ADMIN_ROLE:
        return False
    return account.get("terms_accepted_version") != TERMS_VERSION
