#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""后台重置 PostgreSQL 密码：DSN 解析/重建、密码生成、env.production 渲染。

这些是编排里最容易出错、也最该被锁死的纯逻辑：DSN 里密码含特殊字符时必须重新
percent-encode，否则写回的 DSN 应用读不了；密码字符集必须是 DSN 安全的，否则同
样问题；env.production 里除 POSTGRES_DSN 之外的行必须原样保留。
"""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from config import settings
from services.pg_credentials import (
    PasswordResetError,
    build_dsn,
    generate_password,
    parse_dsn,
    render_env_production,
    reset_database_password,
)

try:
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None


def pg_available() -> bool:
    if asyncpg is None:
        return False

    async def probe() -> bool:
        try:
            conn = await asyncpg.connect("postgresql://localhost:5432/luyun", timeout=2)
        except Exception:
            return False
        await conn.close()
        return True

    try:
        return asyncio.run(probe())
    except Exception:
        return False


class ParseDsnTest(unittest.TestCase):
    def test_full_dsn(self):
        parts = parse_dsn("postgresql://luyun:s3cret@postgres:5432/luyun")
        self.assertIsNotNone(parts)
        self.assertEqual(parts.user, "luyun")
        self.assertEqual(parts.password, "s3cret")
        self.assertEqual(parts.host, "postgres")
        self.assertEqual(parts.port, "5432")
        self.assertEqual(parts.database, "luyun")

    def test_dsn_without_credentials(self):
        parts = parse_dsn("postgresql://localhost:5432/luyun")
        self.assertIsNotNone(parts)
        self.assertEqual(parts.user, "")
        self.assertEqual(parts.password, "")
        self.assertEqual(parts.host, "localhost")

    def test_percent_encoded_password_is_decoded(self):
        parts = parse_dsn("postgresql://u:p%40ss%2Fw0rd@h:5432/d")
        self.assertEqual(parts.password, "p@ss/w0rd")

    def test_default_port_when_missing(self):
        parts = parse_dsn("postgresql://u:p@h/d")
        self.assertEqual(parts.port, "5432")

    def test_garbage_returns_none(self):
        self.assertIsNone(parse_dsn("not-a-dsn"))
        self.assertIsNone(parse_dsn(""))


class BuildDsnTest(unittest.TestCase):
    def test_special_chars_are_re_encoded(self):
        parts = parse_dsn("postgresql://u:old@h:5432/d")
        dsn = build_dsn(parts, "p@ss/w0rd")
        # 写回的 DSN 必须能被自己解析回同一明文密码
        self.assertEqual(parse_dsn(dsn).password, "p@ss/w0rd")
        self.assertIn("%40", dsn)
        self.assertIn("%2F", dsn)

    def test_round_trip_for_plain_password(self):
        parts = parse_dsn("postgresql://luyun:old@postgres:5432/luyun")
        dsn = build_dsn(parts, "NewPass123")
        self.assertEqual(dsn, "postgresql://luyun:NewPass123@postgres:5432/luyun")


class GeneratePasswordTest(unittest.TestCase):
    def test_length_and_charset_are_dsn_safe(self):
        import string

        for _ in range(50):
            pwd = generate_password()
            self.assertEqual(len(pwd), 32)
            self.assertTrue(set(pwd) <= set(string.ascii_letters + string.digits), pwd)

    def test_custom_length(self):
        self.assertEqual(len(generate_password(16)), 16)

    def test_is_random(self):
        self.assertNotEqual(generate_password(), generate_password())


class RenderEnvProductionTest(unittest.TestCase):
    SAMPLE = (
        "DEBUG=false\n"
        "ADMIN_API_KEY=keep-me\n"
        "POSTGRES_DSN=postgresql://luyun:old@postgres:5432/luyun\n"
        "RESTAURANT_BASE_URL=https://example.test\n"
    )

    def test_replaces_dsn_line_and_keeps_others(self):
        out = render_env_production(self.SAMPLE, "postgresql://luyun:new@postgres:5432/luyun")
        self.assertIn("ADMIN_API_KEY=keep-me\n", out)
        self.assertIn("RESTAURANT_BASE_URL=https://example.test\n", out)
        self.assertIn("POSTGRES_DSN=postgresql://luyun:new@postgres:5432/luyun\n", out)
        self.assertNotIn("old@postgres", out)
        lines = [line for line in out.splitlines() if line.startswith("POSTGRES_DSN=")]
        self.assertEqual(len(lines), 1, "POSTGRES_DSN 只能有一行")

    def test_appends_when_missing(self):
        out = render_env_production("DEBUG=false\n", "postgresql://luyun:new@postgres:5432/luyun")
        self.assertIn("POSTGRES_DSN=postgresql://luyun:new@postgres:5432/luyun\n", out)
        self.assertTrue(out.startswith("DEBUG=false\n"))

    def test_missing_trailing_newline_is_handled(self):
        out = render_env_production("DEBUG=false", "postgresql://u:p@h:5432/d")
        self.assertIn("POSTGRES_DSN=postgresql://u:p@h:5432/d", out)
        self.assertIn("DEBUG=false", out)


@unittest.skipUnless(pg_available(), "PostgreSQL 不可用，跳过重置编排测试")
class ResetPasswordOrchestrationTest(unittest.IsolatedAsyncioTestCase):
    """用临时角色验证编排，绝不动 luyun 自己的密码。"""

    ROLE = "pgcred_reset_probe"
    OLD = "OldPass123"

    async def asyncSetUp(self):
        self.admin = await asyncpg.connect("postgresql://localhost:5432/luyun")
        await self.admin.execute(f"DROP ROLE IF EXISTS {self.ROLE}")
        await self.admin.execute(f"CREATE ROLE {self.ROLE} LOGIN PASSWORD '{self.OLD}'")
        self.dsn = f"postgresql://{self.ROLE}:{self.OLD}@localhost:5432/luyun"
        self._tmp = tempfile.TemporaryDirectory()
        self.env_path = Path(self._tmp.name) / "env.production"
        self.env_path.write_text(
            f"DEBUG=false\nPOSTGRES_DSN={self.dsn}\nADMIN_API_KEY=keep-me\n", encoding="utf-8"
        )

    async def asyncTearDown(self):
        await self.admin.execute(f"DROP ROLE IF EXISTS {self.ROLE}")
        await self.admin.close()
        self._tmp.cleanup()

    def _patches(self, *, verify=None, apply_spy=None):
        stack = [
            mock.patch.object(settings, "DATABASE_BACKEND", "postgres"),
            mock.patch.object(settings, "POSTGRES_DSN", self.dsn),
            mock.patch.dict(os.environ, {"LUYUN_POSTGRES_DSN": ""}),
            mock.patch("services.pg_credentials.env_production_path", return_value=self.env_path),
            mock.patch("services.pg_credentials._restart_application"),
        ]
        if verify is not None:
            stack.append(mock.patch("services.pg_credentials._verify_dsn_connects", verify))
        if apply_spy is not None:
            stack.append(mock.patch("services.pg_credentials._apply_password", apply_spy))
        return stack

    async def test_success_updates_env_and_restarts(self):
        patches = self._patches()
        for p in patches:
            p.start()
        try:
            result = await reset_database_password(actor="admin")
        finally:
            for p in reversed(patches):
                p.stop()

        self.assertTrue(result["ok"])
        self.assertTrue(result["restart_triggered"])
        self.assertEqual(result["password_length"], 32)

        text = self.env_path.read_text(encoding="utf-8")
        self.assertIn("ADMIN_API_KEY=keep-me", text, "其他变量必须原样保留")
        new_dsn = next(
            line[len("POSTGRES_DSN="):] for line in text.splitlines() if line.startswith("POSTGRES_DSN=")
        )
        new_password = parse_dsn(new_dsn).password
        self.assertNotEqual(new_password, self.OLD)

        # 新密码真的能用
        conn = await asyncpg.connect(new_dsn)
        await conn.close()

        # 接口响应里不能出现明文密码
        self.assertNotIn(new_password, json.dumps(result))
        self.assertNotIn(self.OLD, json.dumps(result))

    async def test_verify_failure_rolls_back_and_keeps_file(self):
        calls: list[str] = []
        from services.pg_credentials import _apply_password as real_apply

        async def spy(conn, username, password):
            calls.append(password)
            return await real_apply(conn, username, password)

        async def boom(_dsn):
            raise RuntimeError("模拟新密码连不上")

        patches = self._patches(verify=boom, apply_spy=spy)
        for p in patches:
            p.start()
        try:
            with self.assertRaises(PasswordResetError) as ctx:
                await reset_database_password(actor="admin")
        finally:
            for p in reversed(patches):
                p.stop()

        self.assertEqual(ctx.exception.code, "verify_failed")
        # 先改成新密码，失败后必须改回旧密码
        self.assertEqual(len(calls), 2, f"应有「改新 + 回滚旧」两次 ALTER，实际 {calls}")
        self.assertEqual(calls[1], self.OLD, "回滚必须用旧密码")
        # 文件没被动过
        self.assertIn(self.dsn, self.env_path.read_text(encoding="utf-8"))

    async def test_env_override_blocks_reset(self):
        with mock.patch.object(settings, "DATABASE_BACKEND", "postgres"), mock.patch.dict(
            os.environ, {"LUYUN_POSTGRES_DSN": self.dsn}
        ), mock.patch(
            "services.pg_credentials.env_production_path", return_value=self.env_path
        ):
            with self.assertRaises(PasswordResetError) as ctx:
                await reset_database_password(actor="admin")
        self.assertEqual(ctx.exception.code, "env_override")
        self.assertIn("优先级", str(ctx.exception))

    async def test_sqlite_backend_is_rejected(self):
        with mock.patch.object(settings, "DATABASE_BACKEND", "sqlite"):
            with self.assertRaises(PasswordResetError) as ctx:
                await reset_database_password(actor="admin")
        self.assertEqual(ctx.exception.code, "not_postgres")

    async def test_dsn_without_password_is_rejected(self):
        with mock.patch.object(settings, "DATABASE_BACKEND", "postgres"), mock.patch.object(
            settings, "POSTGRES_DSN", f"postgresql://{self.ROLE}@localhost:5432/luyun"
        ), mock.patch.dict(os.environ, {"LUYUN_POSTGRES_DSN": ""}):
            with self.assertRaises(PasswordResetError) as ctx:
                await reset_database_password(actor="admin")
        self.assertEqual(ctx.exception.code, "password_unknown")


if __name__ == "__main__":
    unittest.main()
