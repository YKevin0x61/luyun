#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运营日报的**发送时**渲染（ADR 0144）。

出站行里只有冻结的营业日与勾选的模块，图和正文都在发送时现算。这一组钉住四件事：

① 按行里冻结的营业日取数，而不是「服务器此刻是哪天」——补发跨过 06:00 时再解析一次
   「今天」会把日报算成空白的一天；
② 出图失败（没有中文字体 / 图超限 / 画图抛异常）退回文字版，当天不能一条都不发；
③ 参数里带 `text` 的行（事件侧）原样发出，不被日报渲染接管；
④ 缺 `business_date` 的行当场报错，由派发路径落终态留下原因，而不是发一条空消息。
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from services import wecom_daily_ops as daily_ops_module
from services.wecom_daily_ops import DailyOpsReport, DailyOpsReportRenderer
from services.wecom_outbox import RenderedDelivery
from services.wecom_push_topics import TOPIC_DAILY_OPS_REPORT


def _report() -> DailyOpsReport:
    return DailyOpsReport(
        business_date="2026-10-13",
        weekday="周二",
        orders={"orders": 1544, "items": 1544, "dishes": 140, "delta_pct": 17.0, "peak_hour": 11, "peak_orders": 304},
        empty_reason={"wastage": "暂无报损记录"},
    )


class DailyOpsReportRendererTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.renderer = DailyOpsReportRenderer(object())

    async def _render(self, params):
        collect = AsyncMock(return_value=_report())
        with patch.object(daily_ops_module, "collect_report", new=collect):
            delivery = await self.renderer.render({"topic_id": TOPIC_DAILY_OPS_REPORT}, params)
        return delivery, collect

    async def test_row_renders_the_frozen_business_day_as_an_image(self):
        delivered, collect = await self._render({"business_date": "2026-10-13", "modules": ["orders"]})

        self.assertIsInstance(delivered, RenderedDelivery)
        self.assertEqual(collect.await_args.kwargs["business_date"], "2026-10-13")
        self.assertEqual(collect.await_args.kwargs["modules"], ["orders"])
        if delivered.image_bytes is None:
            # 本机没有中文字体时走文字降级——那是设计好的路径，不是失败。
            self.assertIn("【运营日报】2026年10月13日 周二", delivered.text)
        else:
            self.assertTrue(delivered.image_bytes.startswith(b"\x89PNG"))

    async def test_image_failure_falls_back_to_text_instead_of_failing_the_row(self):
        """出图抛异常时退回文字：宁可难看，也不能让当天一条都不发。"""
        collect = AsyncMock(return_value=_report())
        with patch.object(daily_ops_module, "collect_report", new=collect), patch.object(
            daily_ops_module, "render_png", side_effect=RuntimeError("boom")
        ):
            delivery = await self.renderer.render(
                {"topic_id": TOPIC_DAILY_OPS_REPORT},
                {"business_date": "2026-10-13", "modules": ["orders"]},
            )

        self.assertIsNone(delivery.image_bytes)
        self.assertIn("【运营日报】2026年10月13日 周二", delivery.text)

    async def test_row_with_text_is_sent_verbatim(self):
        """事件侧（参数里已经有正文）的行原样发出，不去查任何数据。"""
        collect = AsyncMock(return_value=_report())
        with patch.object(daily_ops_module, "collect_report", new=collect):
            delivery = await self.renderer.render(
                {"topic_id": TOPIC_DAILY_OPS_REPORT}, {"text": "【运营日报】手动补发"}
            )

        self.assertEqual(delivery.text, "【运营日报】手动补发")
        collect.assert_not_awaited()

    async def test_row_without_a_frozen_business_day_fails_loud(self):
        with self.assertRaises(ValueError) as ctx:
            await self.renderer.render({"topic_id": TOPIC_DAILY_OPS_REPORT}, {"modules": ["orders"]})

        self.assertIn("business_date", str(ctx.exception))
