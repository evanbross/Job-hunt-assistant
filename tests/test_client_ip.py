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

from __future__ import annotations

import pytest
from flask import Flask, jsonify, request

from backend.security.client_ip import (
    TrustedProxyMiddleware,
    parse_trusted_proxies,
    resolve_client_address,
    resolve_scheme,
)

# ai: claude-sonnet-5 | 2026-09-26

PROXY = "172.30.0.2"
TRUSTED = parse_trusted_proxies("172.30.0.0/24")


# ─── parsing the setting ────────────────────────────────────────────────────────


def test_an_empty_setting_trusts_nobody() -> None:
    assert parse_trusted_proxies("") == ()
    assert parse_trusted_proxies(" , ") == ()


def test_addresses_and_networks_may_be_mixed() -> None:
    networks = parse_trusted_proxies("10.0.0.5, 172.30.0.0/24 ,fd00::/8")

    assert [str(n) for n in networks] == ["10.0.0.5/32", "172.30.0.0/24", "fd00::/8"]


@pytest.mark.parametrize("raw", ["not-an-ip", "10.0.0.0/33", "0.0.0.0/0", "::/0"])
def test_unusable_entries_fail_at_startup_rather_than_being_ignored(raw: str) -> None:
    with pytest.raises(ValueError, match="TRUSTED_PROXIES"):
        parse_trusted_proxies(raw)


# ─── resolving the client address ───────────────────────────────────────────────


def test_with_no_trusted_proxy_the_header_is_ignored() -> None:
    assert resolve_client_address("203.0.113.7", "1.2.3.4", ()) == "203.0.113.7"


def test_a_direct_caller_cannot_forge_its_address() -> None:
    """The app may be reachable without the proxy: an untrusted peer's header is just text."""
    assert resolve_client_address("203.0.113.7", "1.2.3.4", TRUSTED) == "203.0.113.7"


def test_the_real_client_is_taken_from_a_trusted_proxy() -> None:
    assert resolve_client_address(PROXY, "203.0.113.7", TRUSTED) == "203.0.113.7"


def test_entries_the_client_supplied_are_not_believed() -> None:
    """The client sent `X-Forwarded-For: 1.2.3.4`; the proxy appended who it saw."""
    assert resolve_client_address(PROXY, "1.2.3.4, 203.0.113.7", TRUSTED) == "203.0.113.7"


def test_a_chain_of_trusted_proxies_is_walked_from_the_right() -> None:
    assert resolve_client_address(PROXY, "203.0.113.7, 172.30.0.9", TRUSTED) == "203.0.113.7"


def test_the_walk_stops_at_the_first_untrusted_hop() -> None:
    assert resolve_client_address(PROXY, "9.9.9.9, 203.0.113.7, 172.30.0.9", TRUSTED) == "203.0.113.7"


def test_garbage_in_the_header_falls_back_to_the_last_address_we_can_vouch_for() -> None:
    assert resolve_client_address(PROXY, "203.0.113.7, banana", TRUSTED) == PROXY


def test_a_trusted_proxy_that_sends_no_header_is_reported_as_itself() -> None:
    assert resolve_client_address(PROXY, None, TRUSTED) == PROXY


def test_ipv4_mapped_ipv6_peers_match_ipv4_networks() -> None:
    assert resolve_client_address("::ffff:172.30.0.2", "203.0.113.7", TRUSTED) == "203.0.113.7"


def test_a_missing_peer_never_raises() -> None:
    assert resolve_client_address(None, "203.0.113.7", TRUSTED) == "unknown"


# ─── the scheme ─────────────────────────────────────────────────────────────────


def test_the_forwarded_scheme_is_used_only_from_a_trusted_proxy() -> None:
    assert resolve_scheme(PROXY, "https", TRUSTED, "http") == "https"
    assert resolve_scheme("203.0.113.7", "https", TRUSTED, "http") == "http"
    assert resolve_scheme(PROXY, "https", (), "http") == "http"


def test_a_nonsense_forwarded_scheme_is_ignored() -> None:
    assert resolve_scheme(PROXY, "javascript", TRUSTED, "http") == "http"


# ─── the middleware, end to end through Flask ───────────────────────────────────


@pytest.fixture
def client():
    flask_app = Flask(__name__)

    @flask_app.route("/whoami", methods=["POST"])
    def whoami():
        return jsonify(address=request.remote_addr, scheme=request.scheme)

    flask_app.wsgi_app = TrustedProxyMiddleware(flask_app.wsgi_app, TRUSTED)  # type: ignore[method-assign]
    return flask_app.test_client()


def test_behind_the_proxy_flask_sees_the_real_client_and_scheme(client) -> None:
    response = client.post(
        "/whoami",
        headers={"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"},
        environ_overrides={"REMOTE_ADDR": PROXY},
    )

    assert response.get_json() == {"address": "203.0.113.7", "scheme": "https"}


def test_reached_directly_the_headers_change_nothing(client) -> None:
    response = client.post(
        "/whoami",
        headers={"X-Forwarded-For": "1.2.3.4", "X-Forwarded-Proto": "https"},
        environ_overrides={"REMOTE_ADDR": "203.0.113.7"},
    )

    assert response.get_json() == {"address": "203.0.113.7", "scheme": "http"}
