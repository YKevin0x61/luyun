#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后台「数据库凭据」接口：鉴权、二次确认、错误映射、不泄露密码。"""

import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from api.security import verify_admin_token
from services.pg_credentials import PasswordResetError

ENDPOINT = "/api/admin/db-credentials"


class DbCredentialsApiTest(unittest.TestCase):
    def setUp(self):
        from main import app

        self.app = app
        self.app.dependency_overrides[verify_admin_token] = lambda: True
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()

    def test_status_requires_admin(self):
        self.app.dependency_overrides.clear()
        with mock.patch(
            "api.security.auth_service.validate_session_id", return_value=False
        ), mock.patch(
            "api.security.auth_service.validate_api_token", return_value=False
        ), mock.patch(
            "api.security.auth_service.is_initialized", return_value=True
        ):
            resp = self.client.get(ENDPOINT)
        self.assertEqual(resp.status_code, 401, "未鉴权不得读取连接信息")

    def test_status_returns_masked_dsn(self):
        from config import settings

        with mock.patch.object(
            settings, "POSTGRES_DSN", "postgresql://luyun:s3cret@localhost:5432/luyun"
        ), mock.patch.dict(os.environ, {"LUYUN_POSTGRES_DSN": ""}):
            resp = self.client.get(ENDPOINT)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("backend", body)
        self.assertIn("env_file", body)
        self.assertEqual(body["password_length"], 6)
        self.assertIn("***", body["dsn"])
        self.assertNotIn("s3cret", resp.text, "响应里不得出现明文密码")
        self.assertNotIn("password", body, "不得返回明文密码字段")

    def test_reset_requires_correct_admin_password(self):
        with mock.patch(
            "api.db_credentials.auth_service.get_admin_username", return_value="admin"
        ), mock.patch(
            "api.db_credentials.auth_service.authenticate", return_value=None
        ), mock.patch(
            "api.db_credentials.reset_database_password"
        ) as reset:
            resp = self.client.post(f"{ENDPOINT}/reset", json={"confirm_password": "wrong"})
        self.assertEqual(resp.status_code, 403)
        reset.assert_not_called()

    def test_reset_success_returns_no_plaintext_password(self):
        result = {
            "ok": True,
            "user": "luyun",
            "host": "postgres",
            "database": "luyun",
            "password_length": 32,
            "env_file": "/srv/luyun/app/deploy/env.production",
            "restart_triggered": False,
            "restart_error": None,
        }
        with mock.patch(
            "api.db_credentials.auth_service.get_admin_username", return_value="admin"
        ), mock.patch(
            "api.db_credentials.auth_service.authenticate", return_value={"username": "admin"}
        ), mock.patch(
            "api.db_credentials.reset_database_password", return_value=dict(result)
        ) as reset, mock.patch(
            "api.db_credentials._restart_after_response"
        ):
            resp = self.client.post(f"{ENDPOINT}/reset", json={"confirm_password": "right"})
        self.assertEqual(resp.status_code, 200)
        reset.assert_awaited_once_with(actor="admin", restart=False)
        body = resp.json()
        self.assertTrue(body["restart_scheduled"])
        self.assertNotIn("password", body)
        self.assertNotIn("dsn", body)

    def test_reset_schedules_restart_after_response(self):
        """重启必须安排在响应之后。

        原来重启同步跑在请求处理里：容器一重启，反代就把 502 页面返回给浏览器，
        用户看到的是 openresty 的 HTML 而不是「重置成功」，也分不清到底成没成。
        """
        payload = {
            "ok": True,
            "user": "luyun",
            "host": "postgres",
            "database": "luyun",
            "password_length": 32,
            "env_file": "/srv/luyun/app/deploy/env.production",
            "restart_triggered": False,
            "restart_error": None,
        }
        with mock.patch(
            "api.db_credentials.auth_service.get_admin_username", return_value="admin"
        ), mock.patch(
            "api.db_credentials.auth_service.authenticate", return_value={"username": "admin"}
        ), mock.patch(
            "api.db_credentials.reset_database_password", return_value=dict(payload)
        ), mock.patch(
            "api.db_credentials._restart_after_response"
        ) as restart_task:
            resp = self.client.post(f"{ENDPOINT}/reset", json={"confirm_password": "right"})
        self.assertEqual(resp.status_code, 200)
        restart_task.assert_awaited_once()

    def test_reset_maps_service_error_to_400(self):
        with mock.patch(
            "api.db_credentials.auth_service.get_admin_username", return_value="admin"
        ), mock.patch(
            "api.db_credentials.auth_service.authenticate", return_value={"username": "admin"}
        ), mock.patch(
            "api.db_credentials.reset_database_password",
            side_effect=PasswordResetError("env_override", "环境变量优先级更高，写文件不生效"),
        ):
            resp = self.client.post(f"{ENDPOINT}/reset", json={"confirm_password": "right"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("优先级", resp.json()["detail"])

    def test_reset_requires_confirmation_field(self):
        resp = self.client.post(f"{ENDPOINT}/reset", json={})
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
