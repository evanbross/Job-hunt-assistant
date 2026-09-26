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

"""Who is really on the other end of a request, when a reverse proxy is in front.

Behind a proxy every connection comes from the proxy's own address, so per-address
throttling would treat every user as one client. The proxy reports the real address in
`X-Forwarded-For`, but that header is just text: any client can send one. It is only
believable when the connection itself came from a proxy we operate. So:

* `TRUSTED_PROXIES` (comma-separated IPs/CIDRs) names the proxies we operate. Empty, the
  default, means no proxy is trusted and forwarding headers are ignored entirely, which is
  correct when the app is reached directly.
* A forwarding header is honoured only when the immediate peer is in that set. The Flask
  app and the extension listener may both be reachable directly as well as through the
  proxy, and direct callers must not be able to forge their address.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Callable, Iterable, Sequence
from typing import Any

logger = logging.getLogger(__name__)

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
_FORWARDED_SCHEMES = frozenset({"http", "https"})


# ai: claude-sonnet-5 | 2026-09-26
def parse_trusted_proxies(raw: str) -> tuple[Network, ...]:
    """Parse the `TRUSTED_PROXIES` setting. Raises ValueError on anything unusable.

    A prefix of /0 is refused: trusting every address is the same as trusting no one's
    claim, and it is always a misconfiguration rather than an intent."""
    networks: list[Network] = []
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError as exc:
            raise ValueError(f"TRUSTED_PROXIES entry {item!r} is not an IP address or CIDR") from exc
        if network.prefixlen == 0:
            raise ValueError(
                f"TRUSTED_PROXIES entry {item!r} would trust every address, which lets any "
                "client forge its own. List only the proxies you run."
            )
        networks.append(network)
    return tuple(networks)


def _address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _is_trusted(value: str, trusted: Iterable[Network]) -> bool:
    address = _address(value)
    if address is None:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address in network for network in trusted if network.version == address.version)


def resolve_client_address(
    peer: str | None, forwarded_for: str | None, trusted: Sequence[Network]
) -> str:
    """The address to attribute a request to.

    Starts from the connection's peer. While the current hop is a trusted proxy, moves one
    entry left in `X-Forwarded-For` (each proxy appends the address it received the request
    from, so the right-hand entries are the ones our own infrastructure wrote). Stops at the
    first hop that is not trusted, or at an entry that is not an IP address (anything left
    of that point is client-controlled)."""
    current = (peer or "").strip() or "unknown"
    if not trusted or not forwarded_for or not _is_trusted(current, trusted):
        return current
    for entry in reversed([part.strip() for part in forwarded_for.split(",")]):
        if not _is_trusted(current, trusted):
            break
        if _address(entry) is None:
            break
        current = entry
    return current


def resolve_scheme(
    peer: str | None, forwarded_proto: str | None, trusted: Sequence[Network], default: str
) -> str:
    """`http` or `https` as the client used it, taken from `X-Forwarded-Proto` only when a
    trusted proxy sent it. Needed so checks that compare the request's own origin (the CSRF
    guard) still work when TLS ends at the proxy and the app itself sees plain HTTP."""
    if not trusted or not forwarded_proto or not _is_trusted((peer or "").strip(), trusted):
        return default
    scheme = forwarded_proto.split(",")[-1].strip().lower()
    return scheme if scheme in _FORWARDED_SCHEMES else default


class TrustedProxyMiddleware:
    """WSGI middleware applying `resolve_client_address` / `resolve_scheme` to the environ,
    so `request.remote_addr` and `request.scheme` are right everywhere downstream. The
    connection's own address is kept in `jha.peer_addr`."""

    def __init__(self, wsgi_app: Callable[..., Any], trusted: Sequence[Network]) -> None:
        self._wsgi_app = wsgi_app
        self._trusted = tuple(trusted)

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> Any:
        peer = environ.get("REMOTE_ADDR")
        environ["jha.peer_addr"] = peer
        environ["REMOTE_ADDR"] = resolve_client_address(
            peer, environ.get("HTTP_X_FORWARDED_FOR"), self._trusted
        )
        environ["wsgi.url_scheme"] = resolve_scheme(
            peer, environ.get("HTTP_X_FORWARDED_PROTO"), self._trusted, environ["wsgi.url_scheme"]
        )
        return self._wsgi_app(environ, start_response)
