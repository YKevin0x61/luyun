# -*- coding: utf-8 -*-
"""Unit tests for Admin-editable GitHub Release token config."""

from __future__ import annotations

import pytest

from services import github_release_config as ghcfg


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    enc = tmp_path / "github_release.enc"
    monkeypatch.setattr(ghcfg, "_CONFIG_FILE", enc)
    from services import credentials_store

    monkeypatch.setattr(credentials_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(credentials_store, "_KEY_FILE", tmp_path / ".cred_key")
    monkeypatch.delenv("LUYUN_CRED_KEY", raising=False)
    monkeypatch.setattr("config.settings.GITHUB_REPO", "YKevin0x61/luyun", raising=False)
    monkeypatch.setattr("config.settings.GITHUB_RELEASES_TOKEN", "", raising=False)
    yield enc


def test_save_and_load_token(isolated_store):
    saved = ghcfg.save_config(token="ghp_test_token")
    assert saved.repo == "YKevin0x61/luyun"
    assert saved.token == "ghp_test_token"
    assert saved.updated_at

    loaded = ghcfg.load_stored()
    assert loaded.token == "ghp_test_token"
    assert loaded.repo == "YKevin0x61/luyun"

    status = ghcfg.public_status()
    assert status["repo"] == "YKevin0x61/luyun"
    assert status["token_configured"] is True
    assert status["stored_token"] is True
    assert "token" not in status or status.get("token") in (None, "")


def test_omit_token_keeps_previous(isolated_store):
    ghcfg.save_config(token="secret-a")
    ghcfg.save_config()  # no token → keep
    assert ghcfg.load_stored().token == "secret-a"


def test_clear_token(isolated_store):
    ghcfg.save_config(token="secret-a")
    ghcfg.save_config(clear_token=True)
    assert ghcfg.load_stored().token is None
    assert ghcfg.public_status()["stored_token"] is False


def test_env_fallback_when_not_stored(isolated_store, monkeypatch):
    monkeypatch.setattr(
        "config.settings.GITHUB_RELEASES_TOKEN",
        "env-token",
        raising=False,
    )
    eff = ghcfg.get_effective_config()
    assert eff.repo == "YKevin0x61/luyun"
    assert eff.token == "env-token"


def test_stored_overrides_env_token(isolated_store, monkeypatch):
    monkeypatch.setattr(
        "config.settings.GITHUB_RELEASES_TOKEN",
        "env-token",
        raising=False,
    )
    ghcfg.save_config(token="stored-token")
    eff = ghcfg.get_effective_config()
    assert eff.repo == "YKevin0x61/luyun"
    assert eff.token == "stored-token"


def test_repo_always_from_settings(isolated_store, monkeypatch):
    monkeypatch.setattr("config.settings.GITHUB_REPO", "Acme/Other", raising=False)
    ghcfg.save_config(token="t")
    assert ghcfg.get_effective_config().repo == "Acme/Other"
