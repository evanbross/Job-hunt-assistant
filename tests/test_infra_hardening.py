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

import importlib.util
import ipaddress
import socket
import ssl
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.security.client_ip import parse_trusted_proxies
from backend.security.redis_tls import redis_ssl_options

# ai: claude-sonnet-5 | 2026-09-26

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY = REPO_ROOT / "deploy"


# ─── Redis TLS options ──────────────────────────────────────────────────────────


def test_a_plain_redis_url_needs_no_tls_options() -> None:
    assert redis_ssl_options("redis://localhost:16379/0", "") is None


def test_a_rediss_url_requires_certificate_verification() -> None:
    options = redis_ssl_options("REDISS://:pw@redis:6379/0", " /tls/ca.crt ")

    assert options == {"ssl_cert_reqs": ssl.CERT_REQUIRED, "ssl_ca_certs": "/tls/ca.crt"}


@pytest.mark.parametrize("ca_file", ["", "   "])
def test_a_rediss_url_without_a_ca_file_is_refused_not_downgraded(ca_file: str) -> None:
    with pytest.raises(ValueError, match="REDIS_TLS_CA_FILE"):
        redis_ssl_options("rediss://:pw@redis:6379/0", ca_file)


def test_the_celery_app_stays_plain_for_a_plain_url() -> None:
    from backend.celery_app import celery_app

    assert not celery_app.conf.broker_use_ssl


# ─── the certificate generator ──────────────────────────────────────────────────


@pytest.fixture
def generate_redis_tls() -> Any:
    spec = importlib.util.spec_from_file_location(
        "generate_redis_tls", REPO_ROOT / "scripts" / "generate_redis_tls.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _handshake(ca: Path, cert: Path, key: Path, server_hostname: str) -> None:
    """A real TLS handshake in-process, verifying the chain the way the app does."""
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(cert), str(key))
    client_ctx = ssl.create_default_context(cafile=str(ca))
    assert client_ctx.verify_mode == ssl.CERT_REQUIRED and client_ctx.check_hostname

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    error: list[BaseException] = []

    def serve() -> None:
        try:
            conn, _ = listener.accept()
            with server_ctx.wrap_socket(conn, server_side=True):
                pass
        except (ssl.SSLError, OSError) as exc:  # the client rejecting us is expected in one test
            error.append(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        with socket.create_connection(listener.getsockname(), timeout=5) as raw:
            with client_ctx.wrap_socket(raw, server_hostname=server_hostname):
                pass
    finally:
        thread.join(timeout=5)
        listener.close()


def test_the_generated_certificate_verifies_for_the_redis_host_name(
    generate_redis_tls: Any, tmp_path: Path
) -> None:
    ca, cert, key = generate_redis_tls.generate(tmp_path, ["redis"])

    _handshake(ca, cert, key, "redis")


def test_the_generated_certificate_is_rejected_for_any_other_host_name(
    generate_redis_tls: Any, tmp_path: Path
) -> None:
    ca, cert, key = generate_redis_tls.generate(tmp_path, ["redis"])

    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(ca, cert, key, "not-redis")


def test_a_certificate_from_a_different_ca_is_rejected(generate_redis_tls: Any, tmp_path: Path) -> None:
    _, cert, key = generate_redis_tls.generate(tmp_path / "server", ["redis"])
    other_ca, _, _ = generate_redis_tls.generate(tmp_path / "other", ["redis"])

    with pytest.raises(ssl.SSLCertVerificationError):
        _handshake(other_ca, cert, key, "redis")


def test_existing_key_material_is_never_overwritten(generate_redis_tls: Any, tmp_path: Path) -> None:
    generate_redis_tls.generate(tmp_path, ["redis"])

    with pytest.raises(FileExistsError):
        generate_redis_tls.generate(tmp_path, ["redis"])
    assert generate_redis_tls.main(["--out", str(tmp_path)]) == 1


# ─── guardrails on the production reference config ──────────────────────────────


@pytest.fixture(scope="module")
def prod_compose() -> dict[str, Any]:
    return yaml.safe_load((DEPLOY / "docker-compose.prod.yml").read_text(encoding="utf-8"))


def test_only_the_reverse_proxy_publishes_ports(prod_compose: dict[str, Any]) -> None:
    published = {name for name, svc in prod_compose["services"].items() if svc.get("ports")}
    assert published == {"caddy"}


def test_redis_is_reachable_only_from_the_internal_network(prod_compose: dict[str, Any]) -> None:
    assert prod_compose["services"]["redis"]["networks"] == ["backend"]
    assert prod_compose["networks"]["backend"]["internal"] is True
    assert "backend" not in prod_compose["services"]["caddy"]["networks"]


def test_redis_requires_tls_and_a_password(prod_compose: dict[str, Any]) -> None:
    command = " ".join(prod_compose["services"]["redis"]["command"])
    assert "--port 0" in command and "--tls-port" in command
    assert "--requirepass" in command
    assert ":?" in prod_compose["services"]["redis"]["environment"]["REDIS_PASSWORD"]


def test_the_app_and_workers_connect_to_redis_over_verified_tls(prod_compose: dict[str, Any]) -> None:
    for name in ("app", "worker"):
        environment = prod_compose["services"][name]["environment"]
        assert environment["REDIS_URL"].startswith("rediss://")
        assert environment["REDIS_TLS_CA_FILE"]
        assert environment["APP_ENV"] == "production"


def test_containers_run_without_extra_privileges(prod_compose: dict[str, Any]) -> None:
    for name, svc in prod_compose["services"].items():
        assert svc["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in svc["security_opt"], name
    assert prod_compose["services"]["redis"]["user"] == "redis"


def test_every_secret_in_the_compose_file_is_a_required_variable() -> None:
    text = (DEPLOY / "docker-compose.prod.yml").read_text(encoding="utf-8")
    for variable in ("APP_DOMAIN", "ACME_EMAIL", "REDIS_PASSWORD"):
        assert f"${{{variable}:?" in text


def test_the_production_caddyfile_disables_the_admin_api_and_sets_security_headers() -> None:
    caddyfile = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "admin off" in caddyfile
    assert "Strict-Transport-Security" in caddyfile
    assert "X-Content-Type-Options" in caddyfile
    assert "request_body" in caddyfile
    assert "microphone=(self)" in caddyfile  # the voice assistant needs it


def test_production_secrets_are_git_ignored() -> None:
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "deploy/.env.production" in ignored and "deploy/secrets/" in ignored


def test_the_app_trusts_forwarding_headers_from_exactly_the_proxy(prod_compose: dict[str, Any]) -> None:
    """TRUSTED_PROXIES must be Caddy's pinned address on the edge network: not a wider range
    (any other container on the network could forge client addresses) and not something
    that drifts from where Caddy actually sits."""
    caddy_address = prod_compose["services"]["caddy"]["networks"]["edge"]["ipv4_address"]
    subnet = ipaddress.ip_network(prod_compose["networks"]["edge"]["ipam"]["config"][0]["subnet"])

    trusted = parse_trusted_proxies(prod_compose["services"]["app"]["environment"]["TRUSTED_PROXIES"])

    assert ipaddress.ip_address(caddy_address) in subnet
    assert [str(network) for network in trusted] == [f"{caddy_address}/32"]
