#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""常驻后台 task 的数目必须可核对，文档口径必须与代码一致（PERF-06）。

缺陷背景（`.scratch/project-review-2026-09-22` PERF-06）：文档（`deploy/luyun.service`、
`deploy/README.md`、`CONTEXT.md`）把「7 个常驻后台循环」当成"必须单 worker"的论据，而
lifespan 实际起的常驻 task 还要加上内存监控 / 内存清理 / 磁盘守护 / realtime Redis 订阅 /
日志落库消费者。数字失真的后果是判断依据失真：真要解除单 worker 时按"7 个"评估会漏掉
三分之一。

修法：`main.py` 把业务循环收进显式清单（`_resident_tasks`，`_start_resident_task()` 是
唯一入口），辅助 task 按各组件自己的只读访问器记账，启动时按清单长度输出。本文件钉：

1. 计数常量自洽；
2. 业务循环确实都经由 `_start_resident_task` 登记（不再散落裸 `create_task`）；
3. 文档里不再出现对不上的口径（`7 个常驻`，以及把 7 说成全部的说法）。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 文档里必须与代码一致的三处（PERF-06 的验收面）。
DOC_FILES = (
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "CONTEXT.md",
    REPO_ROOT / "deploy" / "luyun.service",
    REPO_ROOT / "deploy" / "README.md",
    REPO_ROOT / "deploy" / "docker-compose.yml",
    REPO_ROOT / "deploy" / "env.production.example",
)


class ResidentTaskAccountingTest(unittest.TestCase):
    def test_counts_are_self_consistent(self):
        import main as main_module

        self.assertEqual(
            main_module.RESIDENT_TASK_TOTAL,
            main_module.BUSINESS_LOOP_TASK_COUNT + main_module.AUXILIARY_RESIDENT_TASK_COUNT,
        )
        self.assertEqual(main_module.RESIDENT_TASK_TOTAL, 12)
        # 断言**内容集合**而不只是数量：标签是注册与关闭共用的键，少一个/写错一个字
        # 都会让某个常驻 task 在关闭时被当成业务循环 cancel，或反过来漏登记。
        self.assertEqual(
            set(main_module._AUXILIARY_TASK_LABELS),
            {
                "内存监控",
                "内存清理",
                "磁盘守护",
                "realtime Redis 订阅",
                "日志落库消费者",
            },
        )

    def test_auxiliary_labels_come_from_one_source(self):
        """辅助 task 的标签只许有一份字面量：注册清单与关闭用的集合必须同源。"""
        import main as main_module

        registry = main_module._AUXILIARY_RESIDENT_TASKS
        self.assertEqual(len(registry), main_module.AUXILIARY_RESIDENT_TASK_COUNT)
        self.assertEqual(
            {label for label, _ in registry},
            set(main_module._AUXILIARY_TASK_LABELS),
            "注册清单与 _AUXILIARY_TASK_LABELS 不是同一组标签——迟早各改各的",
        )
        for label, handle in registry:
            self.assertTrue(callable(handle), f"{label} 的句柄访问器不可调用：{handle!r}")

    def test_every_lifespan_task_goes_through_the_registry(self):
        """lifespan 里不许有裸 `asyncio.create_task` —— 漏登记的循环不会被关闭，也不计数。"""
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        lifespan_start = source.index("async def lifespan(")
        lifespan_end = source.index("\n# 创建FastAPI应用", lifespan_start)
        body = source[lifespan_start:lifespan_end]

        bare = [
            line.strip()
            for line in body.splitlines()
            if "asyncio.create_task(" in line
        ]
        self.assertEqual(
            bare,
            [],
            f"lifespan 里还有裸 create_task（应走 _start_resident_task 登记）：{bare}",
        )

        # 反向：登记入口自己必须是唯一的 create_task 站点。
        registry = source[source.index("def _start_resident_task(") : lifespan_start]
        self.assertIn("asyncio.create_task(coro)", registry)

    def test_business_loops_are_all_registered_by_label(self):
        """7 个业务循环的标签都在 lifespan 里出现过（登记就会带上标签）。"""
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        for label in (
            "卫生逾期调度",
            "卫生图片补全",
            "卫生图片维护",
            "企微推送调度",
            "餐厅爬虫采集",
            "日终对账调度",
            "未映射菜品巡检",
        ):
            self.assertIn(f'"{label}"', source, f"业务循环 {label} 没走登记入口")

    def test_docs_do_not_claim_seven_is_everything(self):
        """`7 个常驻` 这个失真口径必须消失；提到 7 时必须同时给出辅助 task 的口径。"""
        for path in DOC_FILES:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "7 个常驻",
                text,
                f"{path.name} 仍在说「7 个常驻」（把辅助 task 漏掉了）",
            )
            for match in re.finditer(r"7 个业务循环", text):
                window = text[max(0, match.start() - 200) : match.end() + 200]
                self.assertTrue(
                    "辅助" in window or "12 个" in window,
                    f"{path.name} 提到「7 个业务循环」但同一段没给辅助 task 的口径：{window!r}",
                )


if __name__ == "__main__":
    unittest.main()
