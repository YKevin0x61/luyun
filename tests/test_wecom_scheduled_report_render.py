#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报表类内容的**发送时**渲染（回归：定时日报每一条都以「正文渲染失败」落终态）。

现场：2026-10-11 22:10:06 的「每日销售报表 → 沟通群」在发送记录里是 `failed`、
0 字节、「正文渲染失败：参数里没有正文（text）」。原因是报表类的出站行里只有**冻结的
营业日**（正文按 ADR 0095 在发送时现算），而统一出站默认只认参数里的 `text` —— 内容
类型没登记渲染器就落到默认文本渲染，于是每一条都当场落终态、且不会重试。

这一组用例钉住两件事：

① 登记之后按**行里冻结的营业日**现算正文，而不是「服务器此刻是哪天」——定时类失败会在
   当天晚些补发一次，补发跨过 06:00 时再解析一次「今天」就会把日报算成空白的一天；
② 双触发的内容类型（对账差异告警）里，**事件侧**那些正文已经在参数里的行原样发出，
   不能被报表渲染接管。
"""

import json
import unittest
from unittest.mock import AsyncMock, patch

from services import wecom_push_service as wecom_push_service_module
from services.wecom_outbox import RenderedDelivery, WeComOutbox
from services.wecom_push_service import RenderedMessage, ScheduledReportRenderer
from services.wecom_push_topics import (
    TOPIC_HYGIENE_PHOTO,
    TOPIC_RECONCILE_DIFF,
    TOPIC_SALES_REPORT,
)

SAMPLE_REPORT = {
    "date_range": {"start": "2026-05-01", "end": "2026-05-01"},
    "summary": {"total_orders": 3, "total_dishes": 4, "unique_dishes": 1, "covered_rules": 0},
    "dish_sales": [{"dish_name": "虾饺", "station": "shulong", "qty": 4}],
    "semi_finished": [],
}


class _FakeReports:
    def __init__(self):
        self.calls = []

    async def compute_sales_report(self, start_date, end_date, station=None):
        self.calls.append((start_date, end_date, station))
        return SAMPLE_REPORT


class _FakeDb:
    """渲染器只碰报表查询与固定菜品表这两处，不需要整库。"""

    def __init__(self):
        self.reports = _FakeReports()

    async def report_dishes_all(self):
        return []


class ScheduledReportRendererTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = _FakeDb()
        self.renderer = ScheduledReportRenderer(self.db)

    async def test_sales_report_row_renders_the_frozen_business_day(self):
        """正文按行里的 `business_date` 算，区间端点是那一天（不是「今天」）。"""
        delivery = await self.renderer.render(
            {"topic_id": TOPIC_SALES_REPORT},
            {"schedule_time": "21:30", "station": "", "business_date": "2026-05-01"},
        )

        self.assertIsInstance(delivery, RenderedDelivery)
        self.assertIn("【销售报表】2026-05-01", delivery.text)
        self.assertEqual(self.db.reports.calls, [("2026-05-01", "2026-05-01", None)])

    async def test_station_param_reaches_the_report_query(self):
        """档口是任务参数：只推某个档口时查询要带上它，空串 = 全部。"""
        await self.renderer.render(
            {"topic_id": TOPIC_SALES_REPORT},
            {"station": "shulong", "business_date": "2026-05-01"},
        )

        self.assertEqual(self.db.reports.calls, [("2026-05-01", "2026-05-01", "shulong")])

    async def test_event_row_of_a_dual_trigger_topic_is_sent_verbatim(self):
        """对账差异告警的事件侧：正文由触发点算好放在参数里，原样发出、不去查报表。"""
        text = "【数据质量告警】2026-05-01 对账\n漏抓 2 份"

        delivery = await self.renderer.render({"topic_id": TOPIC_RECONCILE_DIFF}, {"text": text})

        self.assertEqual(delivery.text, text)
        self.assertEqual(self.db.reports.calls, [])

    async def test_reconcile_diff_schedule_row_uses_the_frozen_business_day(self):
        """对账差异的定时侧（原「数据质量日报」）同样要按冻结营业日现算。"""
        rendered = RenderedMessage(content="【数据质量日报】2026-05-01", byte_length=30)
        with patch.object(
            wecom_push_service_module.wecom_push_service,
            "build_data_quality_message",
            new=AsyncMock(return_value=rendered),
        ) as mocked:
            delivery = await self.renderer.render(
                {"topic_id": TOPIC_RECONCILE_DIFF}, {"business_date": "2026-05-01"}
            )

        self.assertEqual(delivery.text, "【数据质量日报】2026-05-01")
        self.assertEqual(mocked.await_args.kwargs["business_date"], "2026-05-01")

    async def test_row_without_a_frozen_business_day_fails_loud(self):
        """没有冻结营业日的行不静默发空消息：当场报错，由派发路径落终态留下原因。"""
        with self.assertRaises(ValueError) as ctx:
            await self.renderer.render({"topic_id": TOPIC_SALES_REPORT}, {"station": ""})

        self.assertIn("business_date", str(ctx.exception))

    async def test_unknown_topic_is_refused_instead_of_sending_something_else(self):
        with self.assertRaises(ValueError) as ctx:
            await self.renderer.render({"topic_id": "hygiene_reminder"}, {})

        self.assertIn("hygiene_reminder", str(ctx.exception))


class OutboxDefaultRenderTest(unittest.IsolatedAsyncioTestCase):
    """没登记渲染器的内容类型会落到默认文本渲染 —— 这条失败就是线上那条记录。"""

    async def test_a_report_row_without_a_renderer_fails_the_text_fallback(self):
        outbox = WeComOutbox(sender=None)
        row = {
            "topic_id": TOPIC_SALES_REPORT,
            "params_json": json.dumps(
                {"schedule_time": "21:30", "station": "", "business_date": "2026-05-01"},
                ensure_ascii=False,
            ),
        }

        with self.assertRaises(ValueError) as ctx:
            await outbox._render(row)

        self.assertEqual(str(ctx.exception), "参数里没有正文（text）")


class RendererWiringTest(unittest.TestCase):
    """接线：报表类渲染器必须在**启动期**真的挂上（`main.py` 的 lifespan）。

    渲染器写好了却没登记，与没写是同一种故障 —— 而且只有到点发日报那一刻才发现。
    """

    def test_lifespan_registers_the_report_renderers(self):
        from fastapi.testclient import TestClient

        import main as main_module
        from services.wecom_outbox import wecom_outbox

        with TestClient(main_module.app):
            registered = set(wecom_outbox._renderers)

        self.assertLessEqual(
            {TOPIC_SALES_REPORT, TOPIC_RECONCILE_DIFF, TOPIC_HYGIENE_PHOTO}, registered
        )


if __name__ == "__main__":
    unittest.main()
