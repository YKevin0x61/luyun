# -*- coding: utf-8 -*-
"""Tests for Docker vs systemd Update Job deploy mode."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import httpx
import pytest

from services.release_update import deploy_mode
from services.release_update.job_adapters import DockerMainServiceAdapter
from services.release_update.oneshot import DockerDetachedJobStarter, build_oneshot_starter


@pytest.fixture()
def clean_mode_env(monkeypatch):
    monkeypatch.delenv("LUYUN_DEPLOY_MODE", raising=False)
    monkeypatch.delenv("LUYUN_DOCKER_CONTAINER", raising=False)
    monkeypatch.delenv("LUYUN_DOCKER_SOCK", raising=False)
    monkeypatch.delenv("LUYUN_UPDATE_JOB_CMD", raising=False)
    monkeypatch.delenv("LUYUN_MAIN_SERVICE_CMD", raising=False)


def test_resolve_forced_docker(clean_mode_env, monkeypatch):
    monkeypatch.setenv("LUYUN_DEPLOY_MODE", "docker")
    assert deploy_mode.resolve_deploy_mode() == "docker"


def test_resolve_forced_systemd(clean_mode_env, monkeypatch):
    monkeypatch.setenv("LUYUN_DEPLOY_MODE", "systemd")
    assert deploy_mode.resolve_deploy_mode() == "systemd"


def test_resolve_auto_dockerenv(clean_mode_env, monkeypatch, tmp_path):
    monkeypatch.setattr(deploy_mode, "in_docker_runtime", lambda: True)
    assert deploy_mode.resolve_deploy_mode() == "docker"


def test_resolve_auto_systemctl(clean_mode_env, monkeypatch):
    monkeypatch.setattr(deploy_mode, "in_docker_runtime", lambda: False)
    monkeypatch.setattr(deploy_mode.shutil, "which", lambda name: "/usr/bin/systemctl")
    assert deploy_mode.resolve_deploy_mode() == "systemd"


def test_build_oneshot_docker(clean_mode_env, monkeypatch):
    monkeypatch.setenv("LUYUN_DEPLOY_MODE", "docker")
    assert isinstance(build_oneshot_starter(), DockerDetachedJobStarter)


def test_docker_starter_requires_container(clean_mode_env, monkeypatch, tmp_path):
    monkeypatch.setenv("LUYUN_DEPLOY_MODE", "docker")
    sock = tmp_path / "docker.sock"
    sock.write_text("")
    monkeypatch.setenv("LUYUN_DOCKER_SOCK", str(sock))
    starter = DockerDetachedJobStarter()
    with pytest.raises(RuntimeError, match="LUYUN_DOCKER_CONTAINER"):
        starter.start()


def test_docker_starter_spawns_detached(clean_mode_env, monkeypatch, tmp_path):
    monkeypatch.setenv("LUYUN_DOCKER_CONTAINER", "luyun-order")
    sock = tmp_path / "docker.sock"
    sock.write_text("")
    monkeypatch.setenv("LUYUN_DOCKER_SOCK", str(sock))

    fake_log = tmp_path / "update_job.log"
    monkeypatch.setattr(
        "services.release_update.oneshot.job_log_path",
        lambda: fake_log,
    )
    script = tmp_path / "run_update_job.py"
    script.write_text("# noop\n")
    monkeypatch.setattr(
        "services.release_update.oneshot._update_job_script",
        lambda: script,
    )
    monkeypatch.setattr(
        "services.release_update.oneshot._project_root",
        lambda: tmp_path,
    )

    pops = []

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.pid = 4242
            pops.append((args, kwargs))

    monkeypatch.setattr(
        "services.release_update.oneshot.subprocess.Popen",
        FakePopen,
    )
    DockerDetachedJobStarter(python_bin="python3").start()
    assert len(pops) == 1
    assert pops[0][1]["start_new_session"] is True


def test_docker_restart_via_sock(clean_mode_env, monkeypatch, tmp_path):
    sock = tmp_path / "docker.sock"
    sock.write_text("")
    adapter = DockerMainServiceAdapter(
        container="luyun-order",
        sock_path=str(sock),
    )

    class FakeResp:
        status_code = 204
        text = ""

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url):
            assert url == "/containers/luyun-order/restart"
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setattr(httpx, "HTTPTransport", lambda **k: object())
    adapter.restart()


def test_docker_restart_requires_container(clean_mode_env, tmp_path):
    sock = tmp_path / "docker.sock"
    sock.write_text("")
    adapter = DockerMainServiceAdapter(container="", sock_path=str(sock))
    with pytest.raises(RuntimeError, match="LUYUN_DOCKER_CONTAINER"):
        adapter.restart()
