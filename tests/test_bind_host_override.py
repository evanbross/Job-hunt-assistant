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

# ai: claude-sonnet-5 | 2026-09-29
"""Regression tests for the code-review finding that a production
docker-compose deployment's FLASK_HOST/JOB_IMPORT_LISTENER_HOST env vars are
silently ignored once system_settings already has a persisted 127.0.0.1 row
(from prior desktop/local use) -- both env vars are only first-run bootstrap
defaults, not runtime overrides. config.FLASK_BIND_HOST_OVERRIDE and
config.JOB_IMPORT_LISTENER_HOST_OVERRIDE exist specifically to win
unconditionally over that persisted value."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

import app as app_module
import backend.scanning.import_listener as import_listener_module
import config


@contextmanager
def _fake_repos():
    yield {"conn": object()}


def test_resolve_flask_bind_uses_persisted_host_with_no_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "get_repos", _fake_repos)
    monkeypatch.setattr(
        app_module,
        "get_setting",
        lambda name, user_id, repos: {"flask_host": "127.0.0.1", "flask_port": "5004"}[name],
    )
    monkeypatch.setattr(config, "FLASK_BIND_HOST_OVERRIDE", "")

    assert app_module._resolve_flask_bind() == ("127.0.0.1", 5004)


def test_resolve_flask_bind_override_wins_over_persisted_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulates a DB migrated from desktop/local use, already holding
    # 127.0.0.1 -- exactly the case a container deployment must be able to
    # override.
    monkeypatch.setattr(app_module, "get_repos", _fake_repos)
    monkeypatch.setattr(
        app_module,
        "get_setting",
        lambda name, user_id, repos: {"flask_host": "127.0.0.1", "flask_port": "5004"}[name],
    )
    monkeypatch.setattr(config, "FLASK_BIND_HOST_OVERRIDE", "0.0.0.0")

    assert app_module._resolve_flask_bind() == ("0.0.0.0", 5004)


def test_job_import_listener_config_uses_persisted_host_with_no_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_listener_module, "get_repos", _fake_repos)
    monkeypatch.setattr(
        import_listener_module,
        "get_setting",
        lambda name, user_id, repos: "127.0.0.1",
    )
    monkeypatch.setattr(
        import_listener_module, "get_setting_bool", lambda name, user_id, repos, default: True
    )
    monkeypatch.setattr(
        import_listener_module,
        "get_setting_int",
        lambda name, user_id, repos, default: (
            8765 if name == "job_import_listener_port" else default
        ),
    )
    monkeypatch.setattr(config, "JOB_IMPORT_LISTENER_HOST_OVERRIDE", "")

    result = import_listener_module.get_job_import_listener_config()

    assert result["host"] == "127.0.0.1"


def test_job_import_listener_config_override_wins_over_persisted_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_listener_module, "get_repos", _fake_repos)
    monkeypatch.setattr(
        import_listener_module,
        "get_setting",
        lambda name, user_id, repos: "127.0.0.1",
    )
    monkeypatch.setattr(
        import_listener_module, "get_setting_bool", lambda name, user_id, repos, default: True
    )
    monkeypatch.setattr(
        import_listener_module,
        "get_setting_int",
        lambda name, user_id, repos, default: (
            8765 if name == "job_import_listener_port" else default
        ),
    )
    monkeypatch.setattr(config, "JOB_IMPORT_LISTENER_HOST_OVERRIDE", "0.0.0.0")

    result = import_listener_module.get_job_import_listener_config()

    assert result["host"] == "0.0.0.0"
