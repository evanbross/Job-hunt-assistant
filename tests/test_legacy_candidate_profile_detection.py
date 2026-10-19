# =============================================================================
# Copyright (c) 2026 ZombieTech LLC. All rights reserved.
#
# SPDX-License-Identifier: PolyForm-Strict-1.0.0
# See LICENSE in the project root for the full terms.
#
# Licensing inquiries: license@zombietech.biz
# =============================================================================

"""Tests for config.find_legacy_candidate_profile_file(): the one-time database
migration uses this to find a candidate profile file an existing install left
in the project folder (under the default or an alternate name such as
client_profile.md) so it can be copied into the owning account."""

from __future__ import annotations

import config

# ai: claude-sonnet-5 | 2026-09-01


def test_finds_default_filename_when_present(tmp_path, monkeypatch):
    default_path = tmp_path / "candidate_profile.md"
    default_path.write_text("my profile", encoding="utf-8")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "LEGACY_CANDIDATE_PROFILE_PATH", default_path)

    assert config.find_legacy_candidate_profile_file() == default_path


def test_finds_alternate_filename_when_default_missing(tmp_path, monkeypatch):
    alt_path = tmp_path / "client_profile.md"
    alt_path.write_text("my profile", encoding="utf-8")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "LEGACY_CANDIDATE_PROFILE_PATH", tmp_path / "candidate_profile.md")
    monkeypatch.setattr(config, "LEGACY_CANDIDATE_PROFILE_ALT_FILENAMES", ("client_profile.md",))

    assert config.find_legacy_candidate_profile_file() == alt_path


def test_default_filename_wins_over_alternate(tmp_path, monkeypatch):
    default_path = tmp_path / "candidate_profile.md"
    default_path.write_text("default profile", encoding="utf-8")
    alt_path = tmp_path / "client_profile.md"
    alt_path.write_text("alt profile", encoding="utf-8")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "LEGACY_CANDIDATE_PROFILE_PATH", default_path)
    monkeypatch.setattr(config, "LEGACY_CANDIDATE_PROFILE_ALT_FILENAMES", ("client_profile.md",))

    assert config.find_legacy_candidate_profile_file() == default_path


def test_returns_none_when_nothing_found(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "LEGACY_CANDIDATE_PROFILE_PATH", tmp_path / "candidate_profile.md")
    monkeypatch.setattr(config, "LEGACY_CANDIDATE_PROFILE_ALT_FILENAMES", ("client_profile.md",))

    assert config.find_legacy_candidate_profile_file() is None
