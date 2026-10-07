#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集类三份告警改按订阅投递（票 05）：对账差异 / 未映射菜品 / 采集失败。

切换前这三份内容是**一条合成消息广播给所有启用渠道**：同一个「数据质量告警」走定时任务
时只发一个群，走告警链路时发给全部群，两条路径的收件人语义互相矛盾。切换后每类内容各走
自己的 topic、只发给订阅了它的渠道，未订阅的启用渠道一条都收不到（ADR 0094）。

测试落在 spec「Testing Decisions」已批的两个缝隙上：**订阅求解（服务层）** 与
**出站状态机（假发送器）**。断言的是外部行为——出站行的内容类型与目标渠道、终态、
跳过原因、实际发出去的消息——不测 SQL 形状与内部调用顺序。
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from config import settings
from database import CHINA_TZ, DatabaseManager
from scraper.settled_reconcile import ReconcileResult
from services import data_quality_scheduler
from services.data_quality_alerts import enqueue_alert, maybe_send_data_quality_alerts
from services.scraper_health import write_health
from services.wecom_outbox import WeComOutbox
from services.wecom_push_service import encrypt_webhook_url, mask_webhook_url
from services.wecom_push_topics import (
    TOPIC_RECONCILE_DIFF,
    TOPIC_SCRAPER_FAILURE,
    TOPIC_UNMAPPED_DISH,
)

URL_A = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=reconcile-key-000"
URL_B = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=unmapped-key-0000"
URL_C = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=scraper-key-00000"


class FakeSender:
    """假发送器：记下每条消息（企微网络在测试里不存在）。"""

    def __init__(self):
        self.sent = []

    async def send_text(self, webhook_url, content):
        self.sent.append((webhook_url, content))
        return True, "ok"

    async def send_image(self, webhook_url, image_bytes):  # pragma: no cover - 本票不发图
        raise AssertionError("采集类告警不该走图片投递")


class AlertSubscriptionTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        settings.DATABASE_DIR = self._tmpdir.name
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.clock = datetime(2026, 5, 2, 22, 20, tzinfo=CHINA_TZ)
        self.sender = FakeSender()
        self.outbox = WeComOutbox(sender=self.sender, now=lambda: self.clock, gap_seconds=0)

    async def asyncTearDown(self):
        await self.db.close()
        settings.DATABASE_DIR = self._old_dir
        self._tmpdir.cleanup()

    async def _channel(self, name, url, *, enabled=True):
        return await self.db.wecom_webhook_create({
            "name": name,
            "webhook_url_encrypted": encrypt_webhook_url(url),
            "webhook_url_masked": mask_webhook_url(url),
            "enabled": enabled,
            "notes": "",
        })

    async def _subscribe(self, channel_id, topic):
        return await self.db.wecom_subscription_upsert({
            "topic_id": topic,
            "target_channel_id": channel_id,
        })

    async def _alert(self, topic, content, *, scope="2026-05-02"):
        return await enqueue_alert(
            self.db, topic, content, scope=scope, outbox=self.outbox
        )

    async def _rows(self, topic_id=None):
        return await self.db.wecom_outbox_recent(topic_id=topic_id)


class AlertTargetsAreSubscriptionsTest(AlertSubscriptionTestCase):
    async def test_each_alert_goes_to_exactly_its_own_subscribers(self):
        """目标集合恒等于该内容类型的订阅集合：未订阅的启用渠道一条都收不到。"""
        reconcile_group = await self._channel("对账群", URL_A)
        unmapped_group = await self._channel("档口群", URL_B)
        unsubscribed = await self._channel("没订告警的群", URL_C)
        await self._subscribe(reconcile_group, TOPIC_RECONCILE_DIFF)
        await self._subscribe(unmapped_group, TOPIC_UNMAPPED_DISH)

        await self._alert(TOPIC_RECONCILE_DIFF, "【数据质量告警】漏抓 3 份")
        await self._alert(TOPIC_UNMAPPED_DISH, "【档口映射提醒】未映射菜品 2 个")

        delivered = {
            (row["topic_id"], int(row["target_channel_id"]))
            for row in await self._rows()
        }
        self.assertEqual(delivered, {
            (TOPIC_RECONCILE_DIFF, reconcile_group),
            (TOPIC_UNMAPPED_DISH, unmapped_group),
        })
        self.assertNotIn(
            unsubscribed,
            {int(row["target_channel_id"]) for row in await self._rows()},
            "没订阅这类内容的启用渠道不许再收到广播",
        )
        # 发送记录按内容类型筛得出来，每条都带自己的目标渠道
        self.assertEqual(
            [
                int(row["target_channel_id"])
                for row in await self._rows(topic_id=TOPIC_UNMAPPED_DISH)
            ],
            [unmapped_group],
        )


class AlertDispatchTest(AlertSubscriptionTestCase):
    async def test_each_alert_reaches_only_its_subscribers_webhook(self):
        """一路走到假发送器：每封只进订阅了它那个群，未订阅的渠道一个字都收不到。"""
        reconcile_group = await self._channel("对账群", URL_A)
        unmapped_group = await self._channel("档口群", URL_B)
        unsubscribed = await self._channel("没订告警的群", URL_C)
        await self._subscribe(reconcile_group, TOPIC_RECONCILE_DIFF)
        await self._subscribe(unmapped_group, TOPIC_UNMAPPED_DISH)
        await self._alert(TOPIC_RECONCILE_DIFF, "【数据质量告警】漏抓 3 份")
        await self._alert(TOPIC_UNMAPPED_DISH, "【档口映射提醒】未映射菜品 2 个")

        sent = await self.outbox.dispatch_pending(self.db)

        self.assertEqual(sent, 2)
        self.assertEqual(self.sender.sent, [
            (URL_A, "【数据质量告警】漏抓 3 份"),
            (URL_B, "【档口映射提醒】未映射菜品 2 个"),
        ])
        self.assertEqual({row["status"] for row in await self._rows()}, {"sent"})
        self.assertNotIn(
            unsubscribed,
            {int(row["target_channel_id"]) for row in await self._rows()},
        )


class AlertZeroSubscriptionTest(AlertSubscriptionTestCase):
    async def test_an_alert_without_subscribers_enqueues_nothing_and_says_why(self):
        """某类内容零订阅：一条不发、原因进日志，返回形状照样可用（不是 None）。"""
        await self._channel("只订了别的", URL_A)
        await self._subscribe(
            (await self._channel("对账群", URL_B)), TOPIC_RECONCILE_DIFF
        )

        with self.assertLogs("services.data_quality_alerts", level="WARNING") as captured:
            result = await self._alert(TOPIC_UNMAPPED_DISH, "【档口映射提醒】未映射菜品 2 个")

        self.assertEqual(result.get("sent"), 0, "零订阅就是一条都没投出去")
        self.assertIn("订阅", str(result.get("reason")))
        self.assertEqual(await self._rows(), [])
        self.assertTrue(
            any("零订阅" in line for line in captured.output), captured.output
        )


class AlertDisabledChannelTest(AlertSubscriptionTestCase):
    async def test_a_disabled_channel_keeps_its_row_and_is_skipped_with_a_reason(self):
        """渠道停用不动订阅：事件侧也照样入队一行，由派发统一跳过并写明原因。

        迁移回填可能把「日报任务绑定的停用渠道」写进 `reconcile_diff` 订阅（ADR 0094
        第三条回填规则），所以事件告警与定时日报在这一点上必须同一口径：**订阅保留、
        投递跳过、原因进记录**，而不是调用点自己把停用渠道筛掉（那样记录里什么都看不到，
        页面上也看不出"本该发给它、但渠道停着"）。
        """
        disabled = await self._channel("停用的对账群", URL_A, enabled=False)
        await self._subscribe(disabled, TOPIC_RECONCILE_DIFF)

        result = await self._alert(TOPIC_RECONCILE_DIFF, "【数据质量告警】漏抓 3 份")
        self.assertEqual(result.get("sent"), 1, "停用渠道也要留下这一行，不许调用点自己筛掉")

        self.assertEqual(await self.outbox.dispatch_pending(self.db), 0)
        self.assertEqual(self.sender.sent, [], "停用的渠道一条消息都不许发出去")

        row = (await self._rows(topic_id=TOPIC_RECONCILE_DIFF))[0]
        self.assertEqual(row["status"], "skipped")
        self.assertEqual(int(row["target_channel_id"]), disabled)
        self.assertIn("停用", row["last_error"], "跳过原因要可读")


class FakeDishCatalog:
    """假菜品目录：未映射菜品清单由夹具给，不碰数据库。"""

    def __init__(self, dishes):
        self._dishes = list(dishes)

    async def unmapped_dishes(self):
        return {"dishes": list(self._dishes)}


def _reconcile_result(*, biz_date="2026-05-02", missed_qty=12.0):
    """一份超过告警阈值的对账结果（阈值 10 份 / 0.5%）。"""
    return ReconcileResult(
        biz_date=biz_date,
        pos_bill_count=120,
        pos_line_count=800,
        pos_total_qty=1000.0,
        db_total_qty=1000.0 - missed_qty,
        missed_keys=int(missed_qty),
        missed_qty=missed_qty,
        affected_bills=9,
    )


class ReconcileAlertsGoThroughTheOutboxTest(AlertSubscriptionTestCase):
    async def test_the_two_classes_go_out_separately_to_their_own_subscribers(self):
        """对账差异与未映射菜品各走各的 topic，不再拼成一条广播给所有人。"""
        diff_group = await self._channel("对账群", URL_A)
        unmapped_group = await self._channel("档口群", URL_B)
        await self._subscribe(diff_group, TOPIC_RECONCILE_DIFF)
        await self._subscribe(unmapped_group, TOPIC_UNMAPPED_DISH)

        alert = await maybe_send_data_quality_alerts(
            self.db,
            _reconcile_result(),
            dish_catalog=FakeDishCatalog(["新菜A", "新菜B"]),
            outbox=self.outbox,
        )

        self.assertEqual(alert.get("sent"), 2, "两类内容各一行，一行一个订阅目标")
        rows = await self._rows()
        self.assertEqual(
            {(row["topic_id"], int(row["target_channel_id"])) for row in rows},
            {
                (TOPIC_RECONCILE_DIFF, diff_group),
                (TOPIC_UNMAPPED_DISH, unmapped_group),
            },
        )
        texts = {row["topic_id"]: json.loads(row["params_json"])["text"] for row in rows}
        self.assertIn("对账", texts[TOPIC_RECONCILE_DIFF])
        self.assertNotIn("档口映射", texts[TOPIC_RECONCILE_DIFF], "两类内容不再合并成一条")
        self.assertIn("档口映射", texts[TOPIC_UNMAPPED_DISH])

    async def test_nothing_over_threshold_and_no_unmapped_dish_enqueues_nothing(self):
        alert = await maybe_send_data_quality_alerts(
            self.db,
            _reconcile_result(missed_qty=0.0),
            dish_catalog=FakeDishCatalog([]),
            outbox=self.outbox,
        )

        self.assertEqual(alert.get("sent"), 0)
        self.assertEqual(await self._rows(), [])


class AlertIdempotencyTest(AlertSubscriptionTestCase):
    async def test_the_same_alert_for_the_same_scope_is_enqueued_once(self):
        """巡检 5 分钟一轮、对账被手工重跑：同一天同一段文案只投递一次（不刷屏）。"""
        group = await self._channel("档口群", URL_A)
        await self._subscribe(group, TOPIC_UNMAPPED_DISH)
        content = "【档口映射提醒】未映射菜品 2 个"

        first = await self._alert(TOPIC_UNMAPPED_DISH, content)
        second = await self._alert(TOPIC_UNMAPPED_DISH, content)

        self.assertEqual(len(await self._rows()), 1)
        self.assertEqual(first.get("outbox_ids"), second.get("outbox_ids"))


class UnmappedWatchdogTriggerTest(AlertSubscriptionTestCase):
    """未映射菜品巡检（事件触发点）也走同一个出站入口：按 `unmapped_dish` 订阅入队。"""

    async def _check(self, *, now=None):
        return await data_quality_scheduler.check_unmapped_dishes(
            self.db,
            dish_catalog=FakeDishCatalog(["新菜A", "新菜B"]),
            outbox=self.outbox,
            now=now or self.clock,
        )

    async def test_the_watchdog_enqueues_only_for_the_topic_subscribers(self):
        group = await self._channel("档口群", URL_A)
        await self._subscribe(group, TOPIC_UNMAPPED_DISH)
        await self._channel("别的群", URL_B)  # 启用，但没订这类内容

        result = await self._check()

        self.assertEqual(result.get("sent"), 1)
        rows = await self._rows(topic_id=TOPIC_UNMAPPED_DISH)
        self.assertEqual([int(row["target_channel_id"]) for row in rows], [group])

    async def test_the_watchdog_keeps_its_min_interval(self):
        """最小间隔不变：刚提醒过就跳过这一轮，不重复入队。"""
        group = await self._channel("档口群", URL_A)
        await self._subscribe(group, TOPIC_UNMAPPED_DISH)
        write_health({
            "last_unmapped_alert_at": (self.clock - timedelta(hours=1)).isoformat()
        })

        result = await self._check()

        self.assertEqual(result.get("sent"), 0)
        self.assertEqual(await self._rows(), [])


class ScraperFailureAlertTriggerTest(AlertSubscriptionTestCase):
    """采集失败（事件触发点）同样按 `scraper_failure` 的订阅入队。"""

    async def test_the_scraper_health_alert_goes_out_by_subscription(self):
        import main as main_module

        group = await self._channel("运维群", URL_C)
        await self._subscribe(group, TOPIC_SCRAPER_FAILURE)
        await self._channel("别的群", URL_A)

        with patch.object(main_module, "db_manager", self.db):
            await main_module._send_scraper_health_alert("【爬虫健康告警】连续失败 3 次")

        rows = await self._rows(topic_id=TOPIC_SCRAPER_FAILURE)
        self.assertEqual([int(row["target_channel_id"]) for row in rows], [group])
        self.assertIn(
            "连续失败 3 次",
            json.loads(rows[0]["params_json"])["text"],
            "正文要进参数（发送时才渲染）",
        )


if __name__ == "__main__":
    unittest.main()
