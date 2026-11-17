# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

# ai: claude-opus-5-5 | 2026-11-16
"""resolve_tracking_url against a real HTTP server, so urllib's own redirect
handling (and safe_urlopen's redirect_log) is what's under test, not a fake.

Reproduces the Wellfound digest failure of 2026-11-16: the tracking link 303s
to the posting (/jobs?job_listing_slug=...), which bounces a logged-out fetch to
/login?after_sign_in=.... Keeping the login URL lost every job's URL and JD.
Only the outbound IP-range check is switched off, so the loopback server is
reachable; the redirect handler still runs it on every hop.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request

import pytest

import backend.utils.url_safety as url_safety
from backend.email.email_parser import resolve_tracking_url
from backend.utils.url_safety import safe_urlopen

_POSTING = "/jobs?job_listing_slug=5040873-software-engineer-5-tv-ads"
_LOGIN = "/login?after_sign_in=%2Fjobs%3Fjob_listing_slug%3D5040873-software-engineer-5-tv-ads"


class _WellfoundLikeHandler(BaseHTTPRequestHandler):
    """/track -> posting -> login wall (403 under /forbidden-login, else 200);
    /plain -> posting that answers 200 itself."""

    def _respond(self) -> None:
        path = self.path
        if path.startswith("/track"):
            self._redirect(303, _POSTING)
        elif path.startswith("/blocked-track"):
            self._redirect(303, "/blocked" + _POSTING)
        elif path.startswith("/jobs"):
            self._redirect(302, _LOGIN)
        elif path.startswith("/blocked/jobs"):
            self._redirect(302, "/forbidden" + _LOGIN)
        elif path.startswith("/plain"):
            self._redirect(302, "/posting/123")
        elif path.startswith("/forbidden"):
            self._status(403)
        else:
            self._status(200)

    def _redirect(self, code: int, location: str) -> None:
        self.send_response(code)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _status(self, code: int) -> None:
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = _respond
    do_HEAD = _respond

    def log_message(self, *_args: object) -> None:
        return None


@pytest.fixture
def base_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setattr(url_safety, "validate_outbound_url", lambda *_a, **_k: None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _WellfoundLikeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_redirect_log_records_every_hop_in_order(base_url: str) -> None:
    hops: list[str] = []
    with safe_urlopen(Request(f"{base_url}/track/abc"), timeout=5, redirect_log=hops) as resp:
        assert resp.url == f"{base_url}{_LOGIN}"
    assert hops == [f"{base_url}{_POSTING}", f"{base_url}{_LOGIN}"]


def test_sign_in_wall_resolves_to_the_page_it_guards(base_url: str) -> None:
    assert resolve_tracking_url(f"{base_url}/track/abc") == f"{base_url}{_POSTING}"


def test_sign_in_wall_that_refuses_the_fetch_still_resolves_to_the_posting(base_url: str) -> None:
    # The HTTPError path: the login page itself answers 403.
    assert resolve_tracking_url(f"{base_url}/blocked-track/abc") == f"{base_url}/blocked{_POSTING}"


def test_chain_without_a_sign_in_wall_resolves_to_its_final_url(base_url: str) -> None:
    assert resolve_tracking_url(f"{base_url}/plain/abc") == f"{base_url}/posting/123"
