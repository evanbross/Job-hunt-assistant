# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""The browser-extension servers as their own process (web/extension_listener.py main(),
docs/cloud-deployment-readiness-plan.md 1.3)."""

from __future__ import annotations

import signal
import threading
from types import SimpleNamespace
from typing import Any

import pytest

import web.extension_listener as extension_listener
from backend.scanning.import_progress import ImportProgress, ImportProgressState
from web.extension_listener import bound_port_problem

_CONFIG = {"port": 8765, "ws_port": 8766}


def _server(port: int) -> Any:
    return SimpleNamespace(port=port, stopped=False, stop=lambda: None)


# ─── the port rule ──────────────────────────────────────────────────────────────


def test_servers_on_their_configured_ports_are_fine() -> None:
    assert bound_port_problem(_server(8765), _server(8766), _CONFIG) is None


def test_a_fallback_port_is_refused_because_the_proxy_upstream_is_fixed() -> None:
    problem = bound_port_problem(_server(51234), _server(8766), _CONFIG)

    assert problem is not None and "(51234, 8766)" in problem and "(8765, 8766)" in problem


def test_a_server_that_did_not_start_is_refused() -> None:
    problem = bound_port_problem(None, _server(8766), _CONFIG)

    assert problem is not None and "did not start" in problem


# ─── main() ─────────────────────────────────────────────────────────────────────


class _StoppableServer:
    def __init__(self, port: int) -> None:
        self.port = port
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _patch_startup(monkeypatch: pytest.MonkeyPatch, listener_port: int) -> tuple[_StoppableServer, _StoppableServer]:
    servers = (_StoppableServer(listener_port), _StoppableServer(8766))
    monkeypatch.setattr(extension_listener, "verify_database_ready", lambda: None)
    monkeypatch.setattr(extension_listener, "start_extension_servers", lambda trusted: servers)
    monkeypatch.setattr(extension_listener, "get_job_import_listener_config", lambda: dict(_CONFIG))
    return servers


def test_main_exits_and_stops_the_servers_on_a_port_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    servers = _patch_startup(monkeypatch, listener_port=51234)

    assert extension_listener.main() == 1
    assert all(server.stopped for server in servers)


def test_main_serves_until_sigterm_then_stops_the_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    servers = _patch_startup(monkeypatch, listener_port=8765)
    handlers: dict[int, Any] = {}
    monkeypatch.setattr(extension_listener.signal, "signal", lambda sig, handler: handlers.__setitem__(sig, handler))

    result: list[int] = []
    runner = threading.Thread(target=lambda: result.append(extension_listener.main()))
    runner.start()
    while signal.SIGTERM not in handlers:  # main() has started serving
        runner.join(0.05)
    handlers[signal.SIGTERM](signal.SIGTERM, None)
    runner.join(5)

    assert result == [0]
    assert all(server.stopped for server in servers)


def test_main_refuses_weak_secrets_before_touching_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLASK_SECRET_KEY", "change-this-in-production")
    monkeypatch.setattr(extension_listener.config, "APP_ENV", "production")
    monkeypatch.setattr(
        extension_listener, "start_extension_servers", lambda trusted: pytest.fail("must not start servers")
    )

    with pytest.raises(SystemExit) as excinfo:
        extension_listener.main()
    assert excinfo.value.code == 1


# ─── what the separate process changes: progress written there is read here ─────


def test_an_import_queued_by_the_listener_is_visible_to_the_status_route(
    isolated_import_progress: ImportProgress, flask_app: Any
) -> None:
    """The listener process only writes through backend/scanning/import_progress.py;
    /api/import/status (a web worker) reads the same store. Simulated with the shared
    in-memory store here; tests/test_import_progress.py covers the Redis store itself."""
    isolated_import_progress.put(
        "from-listener",
        ImportProgressState(
            running=True, phase="filtering", progress=20, message="Filtering",
            trigger="listener", started_at="2026-11-06T10:00:00+00:00", user_id=7,
        ),
    )
    client = flask_app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["authenticated"] = True
        flask_session["user_id"] = 7
        flask_session["setup_complete_verified"] = True

    data = client.get("/api/import/status").get_json()

    assert data["trigger"] == "listener" and data["phase"] == "filtering"
