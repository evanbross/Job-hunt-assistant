# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

from __future__ import annotations

import importlib.util
import ipaddress
import re
import socket
import ssl
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.celery_app import DEFAULT_QUEUE, MAINTENANCE_QUEUE
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
    for name in ("app", "listener", "worker", "maintenance", "beat"):  # ai: claude-opus-5-5 | 2026-11-06
        environment = prod_compose["services"][name]["environment"]
        assert environment["REDIS_URL"].startswith("rediss://")
        assert environment["REDIS_TLS_CA_FILE"]
        assert environment["APP_ENV"] == "production"


# ai: claude-opus-5-5 | 2026-11-10
def test_every_container_rotates_its_logs(prod_compose: dict[str, Any]) -> None:
    """Unrotated logs keep IP and email addresses forever and eventually fill the disk."""
    for name, svc in prod_compose["services"].items():
        logging = svc.get("logging") or {}
        assert logging.get("driver") == "json-file", name
        assert logging.get("options", {}).get("max-size"), name
        assert logging.get("options", {}).get("max-file"), name


def test_containers_run_without_extra_privileges(prod_compose: dict[str, Any]) -> None:
    for name, svc in prod_compose["services"].items():
        assert svc["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in svc["security_opt"], name
    assert prod_compose["services"]["redis"]["user"] == "redis"


def test_every_secret_in_the_compose_file_is_a_required_variable() -> None:
    text = (DEPLOY / "docker-compose.prod.yml").read_text(encoding="utf-8")
    for variable in ("APP_DOMAIN", "ACME_EMAIL", "REDIS_PASSWORD", "READONLY_DB_PASSWORD", "JHA_IMAGE"):
        assert f"${{{variable}:?" in text, variable


def test_the_production_caddyfile_disables_the_admin_api_and_sets_security_headers() -> None:
    caddyfile = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "admin off" in caddyfile
    assert "Strict-Transport-Security" in caddyfile
    assert "X-Content-Type-Options" in caddyfile
    assert "request_body" in caddyfile
    assert "microphone=(self)" in caddyfile  # the voice assistant needs it


# ai: claude-opus-5-5 | 2026-11-09
def test_production_caddy_gets_real_certificates(prod_compose: dict[str, Any]) -> None:
    """CADDY_EXTRA_GLOBAL_OPTIONS exists for the local rehearsal (local_certs). Set in
    production, browsers would get certificates from Caddy's own CA and refuse them."""
    caddyfile = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "{$CADDY_EXTRA_GLOBAL_OPTIONS}" in caddyfile
    assert "CADDY_EXTRA_GLOBAL_OPTIONS" not in prod_compose["services"]["caddy"]["environment"]
    # Text, not yaml.safe_load: the file uses compose's !override tag. A different
    # project name means the rehearsal can never reuse production's volumes.
    rehearsal = (DEPLOY / "rehearsal" / "docker-compose.rehearsal.yml").read_text(encoding="utf-8")
    rehearsal_name = re.search(r"^name: (\S+)$", rehearsal, re.MULTILINE)
    assert rehearsal_name and rehearsal_name.group(1) != prod_compose["name"]


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


# ai: claude-opus-5-5 | 2026-11-09
def test_caddys_pinned_address_is_never_handed_to_another_container(prod_compose: dict[str, Any]) -> None:
    """Caddy starts after app and listener (depends_on healthy). Docker gives them
    automatic addresses from ip_range, so it must exclude Caddy's, or app takes it,
    Caddy can't start, and TRUSTED_PROXIES would name the app itself."""
    edge = prod_compose["networks"]["edge"]["ipam"]["config"][0]
    subnet = ipaddress.ip_network(edge["subnet"])
    ip_range = ipaddress.ip_network(edge["ip_range"])
    caddy_address = ipaddress.ip_address(prod_compose["services"]["caddy"]["networks"]["edge"]["ipv4_address"])
    others = [
        name for name, svc in prod_compose["services"].items()
        if name != "caddy" and "edge" in (svc.get("networks") or [])
    ]

    assert ip_range.subnet_of(subnet)
    assert caddy_address not in ip_range
    usable = [a for a in ip_range if a not in (subnet.network_address, subnet.broadcast_address)]
    assert len(usable) >= len(others), others


# ai: claude-opus-5-5 | 2026-11-06 -- plus the maintenance worker (plan item 1.4).
def test_only_migrate_and_maintenance_get_the_schema_owners_database_url(prod_compose: dict[str, Any]) -> None:
    """The owner is exempt from row-level security, so its URL must reach only the
    one-shot migrate step and the worker that runs nothing but the cross-account
    sweep -- never a container that serves requests or runs ordinary tasks."""
    def env_files(svc: dict[str, Any]) -> list[str]:
        value = svc.get("env_file", [])
        return [value] if isinstance(value, str) else list(value)

    services = prod_compose["services"]
    holders = {name for name, svc in services.items() if ".env.migrate" in env_files(svc)}
    assert holders == {"migrate", "maintenance"}
    assert services["migrate"]["profiles"] == ["migrate"]  # never started by `up`
    assert _celery_queues(services["maintenance"]) == ["maintenance"]
    assert "edge" not in services["maintenance"]["networks"]
    for name, svc in services.items():
        assert "DATABASE_OWNER_URL" not in (svc.get("environment") or {}), name
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "deploy/.env.migrate" in ignored


def test_the_app_serves_through_gunicorn(prod_compose: dict[str, Any]) -> None:
    assert prod_compose["services"]["app"]["command"] == ["web"]  # ai: claude-opus-5-5 | 2026-11-06
    assert _entrypoint_command("web") == "exec gunicorn -c gunicorn.conf.py \"$@\" wsgi:app"


# ai: claude-opus-5-5 | 2026-11-06
def test_the_extension_endpoints_reach_the_standalone_listener(prod_compose: dict[str, Any]) -> None:
    """The listener runs as its own service (web/extension_listener.py main()); Caddy's
    listener hosts must forward there, not to the web app, which no longer runs it."""
    caddyfile = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "reverse_proxy listener:8765" in caddyfile
    assert "reverse_proxy listener:8766" in caddyfile
    assert "app:8765" not in caddyfile and "app:8766" not in caddyfile

    listener = prod_compose["services"]["listener"]
    assert listener["command"] == ["listener"]  # ai: claude-opus-5-5 | 2026-11-06
    assert _entrypoint_command("listener") == "exec python -m web.extension_listener \"$@\""
    assert "edge" in listener["networks"]
    environment = listener["environment"]
    assert environment["TRUSTED_PROXIES"] == prod_compose["services"]["app"]["environment"]["TRUSTED_PROXIES"]
    assert environment["REDIS_URL"].startswith("rediss://") and environment["REDIS_TLS_CA_FILE"]
    assert environment["APP_ENV"] == "production"
    assert "JOB_IMPORT_LISTENER_HOST_OVERRIDE" not in prod_compose["services"]["app"]["environment"]


# ai: claude-opus-5-5 | 2026-11-06 -- the commands live in the image's entrypoint now
ENTRYPOINT = DEPLOY / "docker-entrypoint.sh"


def _entrypoint_command(role: str) -> str:
    """What deploy/docker-entrypoint.sh runs for *role*, continuation lines joined."""
    script = ENTRYPOINT.read_text(encoding="utf-8")
    match = re.search(rf"^  {re.escape(role)}\)\n(.*?)\n    ;;", script, re.S | re.M)
    assert match, f"no '{role}' process in {ENTRYPOINT.name}"
    lines = [line for line in match.group(1).splitlines() if not line.strip().startswith("#")]
    return " ".join(line.strip().rstrip("\\").strip() for line in lines)


def _celery_queues(svc: dict[str, Any]) -> list[str]:
    [role] = svc["command"]
    [option] = [arg for arg in _entrypoint_command(role).split() if arg.startswith("--queues=")]
    return option.removeprefix("--queues=").split(",")


# ai: claude-opus-5-5 | 2026-11-06
def test_scheduled_work_has_one_timer_and_the_sweep_stays_off_the_ordinary_worker(
    prod_compose: dict[str, Any],
) -> None:
    """One beat (two would tick twice); the ordinary worker takes only the default
    queue, so the sweep -- which needs the owner's URL -- can only land on
    `maintenance`. Checked against the queue names celery_app.py actually uses."""
    services = prod_compose["services"]
    beats = [name for name, svc in services.items() if svc.get("command") == ["beat"]]
    assert beats == ["beat"]
    assert _entrypoint_command("beat").startswith("exec celery -A backend.celery_app beat ")
    assert services["beat"]["networks"] == ["backend"]  # Redis only
    assert _celery_queues(services["worker"]) == [DEFAULT_QUEUE]
    assert _celery_queues(services["maintenance"]) == [MAINTENANCE_QUEUE]


# ─── the application image (docs/cloud-deployment-readiness-plan.md 1.7) ────────
# ai: claude-opus-5-5 | 2026-11-06


def _image_services(prod_compose: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        name: svc
        for name, svc in prod_compose["services"].items()
        if str(svc.get("image", "")).startswith("${JHA_IMAGE")
    }


def test_every_application_process_is_one_the_image_knows(prod_compose: dict[str, Any]) -> None:
    services = _image_services(prod_compose)
    assert set(services) == {"app", "listener", "migrate", "worker", "maintenance", "beat"}
    for name, svc in services.items():
        [role] = svc["command"]
        # ai: claude-opus-5-5 | 2026-11-07 -- a start-up check may run first; the process
        # itself must still replace the shell (exec), so signals reach it.
        assert re.search(r"(^|\s)exec \S", _entrypoint_command(role)), name


# ai: claude-opus-5-5 | 2026-11-07
def test_the_maintenance_worker_refuses_to_start_without_working_backups(prod_compose: dict[str, Any]) -> None:
    """The backup bucket's credentials reach only the maintenance worker, which checks
    the backup settings before it starts -- and the check runs before the worker, not
    beside it, so `set -e` stops the container if it fails."""
    services = prod_compose["services"]
    command = _entrypoint_command("maintenance")
    assert command.startswith("python -m backend.db.backup check-config exec celery ")
    assert "set -eu" in ENTRYPOINT.read_text(encoding="utf-8")
    holders = {name for name, svc in services.items() if ".env.backup" in (svc.get("env_file") or [])}
    assert holders == {"maintenance"}
    assert _entrypoint_command("backup") == 'exec python -m backend.db.backup "$@"'
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "deploy/.env.backup" in ignored


def test_application_containers_have_a_read_only_filesystem(prod_compose: dict[str, Any]) -> None:
    """Nothing in the image may be rewritten at runtime; scratch space is a tmpfs."""
    for name, svc in _image_services(prod_compose).items():
        assert svc["read_only"] is True, name
        assert svc["tmpfs"] == ["/tmp"], name


def test_long_running_application_processes_have_health_checks(prod_compose: dict[str, Any]) -> None:
    services = _image_services(prod_compose)
    assert "/api/system/ready" in " ".join(services["app"]["healthcheck"]["test"])
    assert "8765/health" in " ".join(services["listener"]["healthcheck"]["test"])
    for name in ("worker", "maintenance"):
        assert "inspect ping" in " ".join(services[name]["healthcheck"]["test"]), name
    caddy_waits_for = prod_compose["services"]["caddy"]["depends_on"]
    assert caddy_waits_for == {
        "app": {"condition": "service_healthy"},
        "listener": {"condition": "service_healthy"},
    }


def test_the_image_runs_as_an_unprivileged_user() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    [user] = re.findall(r"^USER (\S+)$", dockerfile, re.M)
    assert not user.startswith(("0", "root"))
    assert 'ENTRYPOINT ["/bin/sh", "/app/deploy/docker-entrypoint.sh"]' in dockerfile


def test_the_build_context_is_an_allowlist() -> None:
    """The repository directory holds personal data (credentials/, data/, uploads/,
    exports): the build context excludes everything and lets back in only what runs."""
    rules = [
        line.strip()
        for line in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert rules[0] == "*"
    allowed = {rule.removeprefix("!").rstrip("/") for rule in rules if rule.startswith("!")}
    for private in ("credentials", "data", "uploads", ".env", "tmp", "dump.json"):
        assert not any(entry == private or entry.startswith(f"{private}/") for entry in allowed), private


def test_scripts_the_container_runs_keep_lf_line_endings() -> None:
    """core.autocrlf would check them out with CRLF on Windows, which sh can't run."""
    attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "deploy/docker-entrypoint.sh text eol=lf" in attributes
    assert b"\r\n" not in ENTRYPOINT.read_bytes()
