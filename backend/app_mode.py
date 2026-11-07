# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""Whether this is the desktop app or a hosted, multi-user deployment
(docs/cloud-deployment-readiness-plan.md 2.7).

APP_ENV can't answer that: the desktop app runs as "production" too. Some features
only make sense on the user's own machine -- writing a cover letter into a folder they
picked, debug dumps next to the code -- and on a server the same code would write into
a shared, read-only container, or wherever a user's request pointed it. Those check
is_hosted() and take the hosted path (a download, or nothing).

APP_MODE=desktop is the default, so the desktop app needs no setting;
deploy/docker-compose.prod.yml sets APP_MODE=hosted.
"""

from __future__ import annotations

import config

APP_MODE_DESKTOP = "desktop"
APP_MODE_HOSTED = "hosted"
_VALID_APP_MODES = frozenset({APP_MODE_DESKTOP, APP_MODE_HOSTED})


class AppModeConfigError(ValueError):
    """APP_MODE is set to something other than the known modes."""


def app_mode() -> str:
    """The normalized APP_MODE. A typo is an error, not quietly "desktop": a hosted
    deployment that fell back to desktop behaviour would write into the container."""
    value = config.APP_MODE.strip().lower() or APP_MODE_DESKTOP
    if value not in _VALID_APP_MODES:
        raise AppModeConfigError(
            f"APP_MODE must be '{APP_MODE_DESKTOP}' or '{APP_MODE_HOSTED}', not {config.APP_MODE!r}."
        )
    return value


def is_hosted() -> bool:
    return app_mode() == APP_MODE_HOSTED
