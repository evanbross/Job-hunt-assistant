# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-06
"""Where the server may send Ollama requests (docs/cloud-deployment-readiness-plan.md 2.4).

ollama_base_url is a per-user setting. On the desktop the user's Ollama usually runs on
the same machine, so loopback is allowed (other private ranges never are). On a hosted
server loopback is the app's own container: a user could point the URL at the
extension listener or the web app's port and have the server call its own internal
endpoints, or use the connection test to map which ports answer. A user's local
Ollama is unreachable from the cloud anyway. So hosted, the URL must be https:// at a
public address on a standard port -- the same rules every other outbound fetch follows.

Every Ollama call site (completions, the settings save, both connection tests) goes
through this module, so the rule is decided in one place.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlparse

from backend.app_mode import is_hosted
from backend.utils.url_safety import UnsafeUrlError, safe_urlopen, validate_outbound_url


def ollama_allows_loopback() -> bool:
    return not is_hosted()


def validate_ollama_base_url(url: str) -> None:
    """Raise UnsafeUrlError unless the server may send Ollama requests to *url*."""
    if is_hosted() and urlparse(url).scheme != "https":
        raise UnsafeUrlError(
            "On a hosted server, Ollama must be reached over https:// at a public address"
        )
    validate_outbound_url(url, allow_loopback=ollama_allows_loopback())


def check_ollama_connection(base_url: str, model: str) -> tuple[bool, str]:
    """Whether the Ollama at *base_url* answers and has *model*, with a message for
    the user. Was copied into the settings and setup wizard routes."""
    try:
        validate_ollama_base_url(base_url)
        request = urllib.request.Request(f"{base_url}/api/tags", method="GET")
        with safe_urlopen(request, timeout=5, allow_loopback=ollama_allows_loopback()) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except UnsafeUrlError as exc:
        return False, f"Refusing to connect: {exc}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"Could not connect to Ollama: {exc}"
    except json.JSONDecodeError:
        return False, "Connected but received an unexpected response from Ollama."

    available = [m.get("name", "") for m in body.get("models", [])]
    if model in available:
        return True, f"Connected. Model '{model}' is available."
    if available:
        return False, f"Connected, but model '{model}' was not found. Available: {', '.join(available)}"
    return False, f"Connected, but model '{model}' was not found (no models installed)."
