#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""enable_postgres.sh 的契约测试：现场踩过的坑不能再回来。

现场（云端 Docker 形态）踩到：

1. ``pg_isready`` 在 postgres 镜像首次 initdb 期间假阳性 —— 脚本以为库就绪，
   紧接着撞上 ``FATAL: the database system is shutting down``（19:09 那次
   卡了 7 分 21 秒后 die）。
2. ``pg_hba.conf`` 里还留着 ``host ... trust`` 行 —— ``POSTGRES_PASSWORD``
   形同虚设，用错密码也能连上。
3. 停机窗口里没把 WAL 合并回主库 —— 现场手工 ``cp`` 出来的备份比真库少 27 行
   （项目内备份走 SQLite backup API 不受影响，但停机时 checkpoint 一次能让主库
   文件自洽，后续怎么搬都不丢）。
"""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "deploy" / "enable_postgres.sh"


class EnablePostgresContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.text = SCRIPT.read_text(encoding="utf-8")

    def _function_body(self, name: str) -> str:
        marker = f"{name}()"
        self.assertIn(marker, self.text, f"缺少 {marker}")
        body = self.text.split(marker, 1)[1]
        return body.split("\n}", 1)[0]

    def test_readiness_uses_real_query_streak(self):
        """就绪判定必须是连续多次「实际查询成功」，而不是只看 pg_isready。"""
        body = self._function_body("wait_for_pg_ready")
        self.assertIn("pg_probe", body, "就绪等待必须基于真实查询探针")
        self.assertIn("streak", body, "需要连续成功计数，躲开 initdb 期间的假阳性窗口")
        self.assertIn("SELECT 1", self._function_body("pg_probe"))
        self.assertIn("psql", self._function_body("pg_query"))

    def test_readiness_wait_is_actually_called(self):
        """定义了就绪等待函数还不够，流程里必须真的用它。"""
        self.assertGreaterEqual(
            self.text.count("wait_for_pg_ready "), 2, "至少 docker 与 systemd 两种形态都要用"
        )

    def test_streak_resets_after_a_failure(self):
        """把 wait_for_pg_ready 拿出来真跑：探针「1 1 0 1 1 1」必须在第 6 次才就绪。

        如果失败后不把计数归零，「三次里中一次」也会被当成稳定就绪 —— 那正是
        initdb 期间假阳性的来源。
        """
        import subprocess

        body = self._function_body("wait_for_pg_ready")
        func_src = "wait_for_pg_ready()" + body + "\n}\n"
        script = (
            "set -euo pipefail\n"
            "PG_READY_STREAK=3\n"
            "sleep() { :; }\n"
            "_probe=(1 1 0 1 1 1)\n"
            "_i=0\n"
            'pg_probe() { local r="${_probe[$_i]:-0}"; _i=$((_i+1)); [[ "$r" == "1" ]]; }\n'
            + func_src
            + 'if wait_for_pg_ready 10; then echo "ready at $_i"; else echo "not-ready"; fi\n'
        )
        out = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=True
        ).stdout.strip()
        self.assertEqual(out, "ready at 6", f"连续就绪计数逻辑不对（输出 {out!r}）")

    def test_tightens_pg_hba_trust_and_reloads(self):
        """发现 host trust 行要收紧成 scram-sha-256，并 pg_reload_conf() 生效。"""
        self.assertIn("pg_hba", self.text)
        self.assertIn("scram-sha-256", self.text)
        self.assertIn("pg_reload_conf", self.text)

    def test_checkpoints_wal_before_backup(self):
        """停机后、备份前合并 WAL，避免搬走的是一份不自洽的主库文件。"""
        self.assertIn("wal_checkpoint", self.text)
        self.assertLess(
            self.text.index("wal_checkpoint"),
            self.text.index('".backup'),
            "checkpoint 必须在 .backup 之前",
        )

    def test_sed_expression_actually_rewrites_host_trust(self):
        """把脚本里的 sed 表达式抓出来真跑一遍：host trust → scram，local trust 保留。

        两种形态各有一条：systemd 分支用单引号原文（``$`` 直接写），docker 分支
        在双引号里（``$`` 必须写成 ``\\$`` 由 shell 展开）。这里对单引号那条做语义
        验证，并断言两条经各自引用方式后完全等价。
        """
        import re
        import subprocess
        import tempfile
        from pathlib import Path as _Path

        matches = re.findall(r"sed -i -E '([^']*trust[^']*)'", self.text)
        plain = [e for e in matches if "\\$" not in e]
        escaped = [e for e in matches if "\\$" in e]
        self.assertEqual(len(plain), 1, "应有一条 systemd 形态（单引号原文）表达式")
        self.assertEqual(len(escaped), 1, "应有一条 docker 形态（双引号内 \\$ 转义）表达式")

        expanded = subprocess.run(
            ["bash", "-c", f'printf %s "{escaped[0]}"'],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertEqual(expanded, plain[0], "docker 分支的 $ 转义不正确")

        # postgres:16-alpine 的默认 pg_hba 形态
        sample = (
            "local   all             all                                     trust\n"
            "host    all             all             127.0.0.1/32            trust\n"
            "host    all             all             ::1/128                 trust\n"
            "host    all             all             all                     scram-sha-256\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = _Path(tmp) / "pg_hba.conf"
            path.write_text(sample, encoding="utf-8")
            # 不带 -i：BSD/GNU sed 的 -i 参数形式不同，输出到 stdout 才是跨平台的
            result = subprocess.run(
                ["sed", "-E", plain[0], str(path)], capture_output=True, text=True, check=True
            )
        out = result.stdout
        self.assertIn("127.0.0.1/32            scram-sha-256", out)
        self.assertIn("::1/128                 scram-sha-256", out)
        self.assertNotIn("127.0.0.1/32            trust", out)
        self.assertIn(
            "local   all             all                                     trust",
            out,
            "local（socket）行不在收紧范围内",
        )


if __name__ == "__main__":
    unittest.main()
