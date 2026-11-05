# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""The web application: the Flask app factory and what a serving process wires around
it. See web/factory.py for what belongs where."""

from web.factory import create_app

__all__ = ["create_app"]
