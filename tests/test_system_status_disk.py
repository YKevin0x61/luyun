#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`/api/system/status` 的磁盘水位段。

后台「系统健康状态」页要画磁盘水位图，需要**分母**（总量 / 使用率），而
`/api/healthz` 按设计只给聚合的 free_mb（它服务探针，刻意不暴露环境细节）。
所以把磁盘水位放进 status 接口：disk_guard 本来就有 total/used/used_pct。
"""

import unittest
from unittest import mock

from fastapi.testclient import TestClient


class SystemStatusDiskTest(unittest.TestCase):
    def setUp(self):
        from main import app

        self.client = TestClient(app)

    def test_status_exposes_disk_watermark(self):
        fake_snapshot = [
            {
                "path": "/srv/luyun/data",
                "total_mb": 20480.0,
                "used_mb": 8192.0,
                "free_mb": 12288.0,
                "used_pct": 40.0,
                "level": "ok",
            }
        ]
        with mock.patch("main.disk_guard.snapshot", return_value=fake_snapshot), mock.patch(
            "main.disk_guard.worst_level", return_value="ok"
        ):
            resp = self.client.get("/api/system/status")
        self.assertEqual(resp.status_code, 200)
        disk = resp.json().get("disk")
        self.assertIsNotNone(disk, "status 必须带 disk 段供健康页作图")
        self.assertEqual(disk["level"], "ok")
        self.assertEqual(disk["worst"]["total_mb"], 20480.0)
        self.assertEqual(disk["worst"]["used_pct"], 40.0)
        self.assertIn("threshold_free_mb", disk, "要给出磁盘告警门槛，图里画阈值线")
        # 前端作图只需要这几项，多余的都算噪音
        self.assertIn("paths", disk)

    def test_status_survives_disk_probe_failure(self):
        with mock.patch("main.disk_guard.snapshot", side_effect=OSError("statvfs failed")):
            resp = self.client.get("/api/system/status")
        self.assertEqual(resp.status_code, 200, "磁盘探测失败不能拖垮整个状态接口")
        self.assertIn("error", resp.json()["disk"])

    def test_status_without_snapshot_items(self):
        """路径全读不到（例如容器里挂载变了）时不能炸。"""
        with mock.patch("main.disk_guard.snapshot", return_value=[]), mock.patch(
            "main.disk_guard.worst_level", return_value="unknown"
        ):
            resp = self.client.get("/api/system/status")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["disk"]["worst"])
        self.assertEqual(resp.json()["disk"]["paths"], [])


class ScraperHealthThresholdTest(unittest.TestCase):
    def setUp(self):
        from main import app

        self.client = TestClient(app)

    def test_scraper_health_exposes_failure_threshold(self):
        """图里要画阈值线，阈值由接口给出，避免前端写死后与配置漂移。"""
        with mock.patch("services.scraper_health.read_health", return_value={"api_failures": 1}):
            resp = self.client.get("/api/system/scraper-health")
        self.assertEqual(resp.status_code, 200)
        health = resp.json()["health"]
        self.assertEqual(health["api_failures"], 1)
        self.assertIn("api_failures_threshold", health)
        self.assertIsInstance(health["api_failures_threshold"], int)


if __name__ == "__main__":
    unittest.main()
