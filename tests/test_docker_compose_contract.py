#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Contract tests for Docker process-shell deploy files (ADR 0011)."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY = REPO_ROOT / "deploy"
COMPOSE = DEPLOY / "docker-compose.yml"
DOCKERFILE = DEPLOY / "Dockerfile"
ENTRYPOINT = DEPLOY / "docker-entrypoint.sh"
DOCKER_UP = REPO_ROOT / "scripts" / "docker_up.sh"
ENV_EXAMPLE = DEPLOY / ".env.docker.example"


class DockerComposeContractTest(unittest.TestCase):
    def test_compose_mounts_parent_not_app_only(self):
        text = COMPOSE.read_text(encoding="utf-8")
        self.assertIn("LUYUN_HOST_PARENT", text)
        self.assertIn("/srv/luyun", text)
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock", text)
        self.assertIn("LUYUN_DEPLOY_MODE: docker", text)
        self.assertIn("RELEASE_UPDATE_REPO_DIR: /srv/luyun/app", text)
        self.assertIn("LUYUN_DOCKER_CONTAINER", text)
        # Must not bind-mount only the live app path as the volume target.
        self.assertNotRegex(
            text,
            r"(?m)^\s*-\s*\$\{LUYUN_HOST_PARENT[^}]*\}:/srv/luyun/app\s*$",
        )

    def test_dockerfile_and_entrypoint_exist(self):
        self.assertTrue(DOCKERFILE.is_file())
        self.assertTrue(ENTRYPOINT.is_file())
        self.assertTrue(DOCKER_UP.is_file())
        self.assertTrue(ENV_EXAMPLE.is_file())
        df = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn("playwright", df.lower())
        self.assertIn("docker-entrypoint.sh", df)
        ep = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn("LUYUN_APP_DIR", ep)
        self.assertIn("app.prev", ep)
        self.assertIn("--workers 1", ep)
        self.assertRegex(ep, r"(?im)^#.*parent")

    def test_docker_up_points_at_compose(self):
        text = DOCKER_UP.read_text(encoding="utf-8")
        self.assertIn("docker-compose.yml", text)
        self.assertIn(".env.docker", text)

    def test_volume_paths_do_not_rely_on_relative_defaults(self):
        """卷路径必须显式给出，不能退回相对默认值。

        现场踩过：`${LUYUN_HOST_PARENT:-./runtime}` 在 cwd 为
        /opt/luyun/deploy/runtime/app 时被解析成 runtime/app/runtime/，compose
        新建了空目录并复制了一份空 SQLite —— 应用连到了「另一个库」，而且不报错。
        """
        text = COMPOSE.read_text(encoding="utf-8")
        for var in ("LUYUN_HOST_PARENT", "LUYUN_PG_DATA", "LUYUN_REDIS_DATA"):
            self.assertNotIn(
                f"${{{var}:-./runtime", text, f"{var} 不应保留相对路径默认值"
            )
            self.assertIn(f"${{{var}:?", text, f"{var} 应改为必填（:?），缺失即报错")

    def test_docker_up_exports_absolute_volume_paths(self):
        """docker_up.sh 负责把三个卷路径绝对化后导出，compose 不再猜 cwd。"""
        text = DOCKER_UP.read_text(encoding="utf-8")
        for var in ("LUYUN_HOST_PARENT", "LUYUN_PG_DATA", "LUYUN_REDIS_DATA"):
            self.assertIn(
                f"export {var}=", text, f"docker_up.sh 应把 {var} 绝对化后导出"
            )


class ReleaseUpdateFactoryDockerWiringTest(unittest.TestCase):
    def test_factory_uses_docker_oneshot_when_mode_docker(self):
        from unittest import mock

        from services.release_update.factory import build_release_update
        from services.release_update.oneshot import DockerDetachedJobStarter

        with mock.patch(
            "services.release_update.factory.get_effective_config"
        ) as cfg, mock.patch(
            "services.release_update.factory.build_oneshot_starter",
            return_value=DockerDetachedJobStarter(),
        ) as starter, mock.patch(
            "services.release_update.factory.FileJobStateStore"
        ), mock.patch(
            "services.release_update.factory.GitHubReleasesAdapter"
        ), mock.patch(
            "services.release_update.factory.ReleaseManifestAdapter"
        ), mock.patch(
            "services.release_update.factory.DefaultPreflightEnvAdapter"
        ), mock.patch(
            "services.release_update.factory.BusinessHoursPeakAdapter"
        ):
            cfg.return_value = mock.Mock(repo="acme/luyun", token=None)
            ru = build_release_update()
            starter.assert_called_once()
            self.assertIsInstance(ru._oneshot, DockerDetachedJobStarter)


if __name__ == "__main__":
    unittest.main()
