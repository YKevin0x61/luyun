#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运维两类推送（票 10）：磁盘 / 内存水位、「系统更新 + 冷备结果」。

缝隙沿用 spec「Testing Decisions」第 1/2 条（订阅求解与出站状态机；假时钟 + 假发送器）：
驱动的是 30 秒企微调度循环每轮调用的那一个入口（``OpsEventWatcher.poll_once``），断言的
是**外部行为** —— 落了几行出站、正文说了什么、重启后重读同一份状态会不会又发一条 ——
不测 SQL 形状与内部调用顺序。

水位信号由**假读数**注入（真磁盘 / 真内存的等级没法用假时钟推动）；更新与冷备**不注入**：
测试写真的状态文件（``update_job.json`` / ``cold_backup_status.json``），断言进程读到终态
后补推、同一版本 / 同一次备份只推一次。
"""

import asyncio
import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from config import settings
from database import CHINA_TZ, DatabaseManager
from services import backup_service
from services import wecom_ops_events as ops_module
from services import wecom_push_service as wecom_push_service_module
from services.disk_guard import disk_guard
from services.memory_manager import memory_manager
from services.release_update import (
    STAGE_FAILED,
    STAGE_QUEUED,
    STAGE_SUCCEEDED,
    UpdateJobState,
)
from services.release_update.job_state import FileJobStateStore
from services.wecom_ops_events import (
    DISK_SIGNAL,
    MEMORY_SIGNAL,
    OpsEventWatcher,
    WatermarkReading,
    read_disk_watermark,
    read_memory_watermark,
)
from services.wecom_outbox import WeComOutbox
from services.wecom_push_service import (
    encrypt_webhook_url,
    mask_webhook_url,
    wecom_push_service,
)
from services.wecom_push_topics import TOPIC_RESOURCE_WATERMARK, TOPIC_UPDATE_BACKUP

URL_A = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=ops-key-0000"
URL_B = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=ops-key-1111"

SILENCE_HOURS = 6


class FakeSender:
    """假发送器：记下每条消息（沿用 tests/test_wecom_outbox.py 的同款）。"""

    def __init__(self):
        self.sent = []

    async def send_text(self, webhook_url, content):
        self.sent.append((webhook_url, content))
        return True, "ok"

    async def send_image(self, webhook_url, image_bytes):
        self.sent.append((webhook_url, "<image>"))
        return True, "ok"


class FlakyOutbox:
    """第一次入队抛异常，之后照常：用来钉「入队失败下一轮还要再试」。"""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    async def enqueue_topic(self, db, topic_id, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("数据库忙")
        return await self._inner.enqueue_topic(db, topic_id, **kwargs)


class OpsEventTestCase(unittest.IsolatedAsyncioTestCase):
    """公共夹具：假时钟 + 假发送器 + 临时 data / backups 目录。"""

    async def asyncSetUp(self):
        self._old_dir = settings.DATABASE_DIR
        self._tmpdir = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self._tmpdir.name, "data")
        self.cold_dir = os.path.join(self._tmpdir.name, "backups")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.cold_dir, exist_ok=True)
        settings.DATABASE_DIR = self.data_dir
        # 冷备状态文件的落点由 BACKUP_DIR 环境变量优先决定（get_cold_backup_dir），
        # 与部署脚本同一条路径；用 patch.dict 保证测试结束后不残留。
        self._backup_env = patch.dict(os.environ, {"BACKUP_DIR": self.cold_dir})
        self._backup_env.start()
        self.db = DatabaseManager()
        self.assertTrue(await self.db.connect())
        self.clock = datetime(2026, 5, 2, 21, 30, tzinfo=CHINA_TZ)
        self.sender = FakeSender()
        self.outbox = WeComOutbox(sender=self.sender, now=lambda: self.clock, gap_seconds=0)
        self.levels = {DISK_SIGNAL: "ok", MEMORY_SIGNAL: "ok"}
        self.watcher = self.new_watcher()

    async def asyncTearDown(self):
        await self.db.close()
        self._backup_env.stop()
        settings.DATABASE_DIR = self._old_dir
        self._tmpdir.cleanup()

    def new_watcher(self, signals=DISK_SIGNAL):
        """新建一个轮询器 —— 模拟「进程重启」（进程内状态全丢，只剩落库的标记）。"""
        wanted = {signals} if isinstance(signals, str) else set(signals)
        return OpsEventWatcher(
            outbox=self.outbox,
            now=lambda: self.clock,
            readings=lambda: {
                signal: WatermarkReading(
                    signal=signal, level=self.levels[signal], detail=f"{signal} 读数"
                )
                for signal in wanted
            },
        )

    async def _channel(self, name, url):
        return await self.db.wecom_webhook_create({
            "name": name,
            "webhook_url_encrypted": encrypt_webhook_url(url),
            "webhook_url_masked": mask_webhook_url(url),
            "enabled": True,
            "notes": "",
        })

    async def _subscribe(self, channel_id, topic_id):
        return await self.db.wecom_subscription_upsert({
            "topic_id": topic_id,
            "target_channel_id": channel_id,
        })

    async def _rows(self, topic_id=None):
        return await self.db.wecom_outbox_recent(topic_id=topic_id)

    def _write_update_state(self, **overrides):
        state = UpdateJobState(
            stage=STAGE_QUEUED,
            target_tag="v1.4.0",
            previous_ref="v1.3.2",
            previous_tag="v1.3.2",
            started_at="2026-05-02T21:20:00+08:00",
        )
        state = replace(state, **overrides)
        FileJobStateStore().write(state)
        return state

    def _write_cold_status(self, **overrides):
        """按 ``write_cold_backup_status`` 那份状态的形状造一份（含失败运行）。"""
        payload = {
            "ran_at": "2026-05-02T03:00:05+08:00",
            "ok": True,
            "archive": None,
            "archive_name": None,
            "ts": None,
            "size_bytes": None,
            "sha256": None,
            "checksum_ok": False,
            "contents": [],
            "consistency": None,
            "error": None,
        }
        payload.update(overrides)
        path = backup_service.cold_status_path()
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload


class OpsWatermarkTest(OpsEventTestCase):
    async def test_crossing_from_normal_to_warning_enqueues_one_delivery(self):
        channel = await self._channel("运维群", URL_A)
        await self._subscribe(channel, TOPIC_RESOURCE_WATERMARK)
        self.levels[DISK_SIGNAL] = "warning"

        await self.watcher.poll_once(self.db)
        sent = await self.outbox.dispatch_pending(self.db)

        self.assertEqual(sent, 1)
        self.assertEqual(len(self.sender.sent), 1)
        url, text = self.sender.sent[0]
        self.assertEqual(url, URL_A)
        self.assertIn("磁盘", text)
        self.assertIn("告警", text)

    async def test_the_same_level_is_not_repeated_within_the_silence_window(self):
        """抖动（告警 → 正常 → 告警）在同一等级上不刷屏：只留首条告警与一条恢复。"""
        channel = await self._channel("运维群", URL_A)
        await self._subscribe(channel, TOPIC_RESOURCE_WATERMARK)
        self.levels[DISK_SIGNAL] = "warning"
        await self.watcher.poll_once(self.db)

        self.clock += timedelta(hours=1)
        self.levels[DISK_SIGNAL] = "ok"
        await self.watcher.poll_once(self.db)
        self.clock += timedelta(minutes=10)
        self.levels[DISK_SIGNAL] = "warning"
        await self.watcher.poll_once(self.db)

        await self.outbox.dispatch_pending(self.db)
        texts = [text for _, text in self.sender.sent]
        self.assertEqual(len(texts), 2, texts)
        self.assertIn("告警", texts[0])
        self.assertIn("恢复正常", texts[1])
        self.assertEqual(len(await self._rows(TOPIC_RESOURCE_WATERMARK)), 2)

    async def test_the_same_level_alerts_again_after_the_silence_window(self):
        """静默期是「不重复」而不是「不再报」：6 小时之后再进同一等级仍要通知。"""
        channel = await self._channel("运维群", URL_A)
        await self._subscribe(channel, TOPIC_RESOURCE_WATERMARK)
        self.levels[DISK_SIGNAL] = "warning"
        await self.watcher.poll_once(self.db)

        self.clock += timedelta(hours=SILENCE_HOURS, minutes=1)
        self.levels[DISK_SIGNAL] = "ok"
        await self.watcher.poll_once(self.db)
        self.clock += timedelta(minutes=1)
        self.levels[DISK_SIGNAL] = "warning"
        await self.watcher.poll_once(self.db)

        await self.outbox.dispatch_pending(self.db)
        texts = [text for _, text in self.sender.sent]
        self.assertEqual(len(texts), 3, texts)
        self.assertIn("告警", texts[2])

    async def test_no_recovery_notice_when_no_alert_was_delivered(self):
        """没告过警就不报恢复：静默期内被压掉的告警，回来时也**不发**恢复。"""
        channel = await self._channel("运维群", URL_A)
        await self._subscribe(channel, TOPIC_RESOURCE_WATERMARK)

        self.levels[DISK_SIGNAL] = "ok"
        await self.watcher.poll_once(self.db)  # 首轮：正常，什么都不发

        self.levels[DISK_SIGNAL] = "warning"
        await self.watcher.poll_once(self.db)
        self.clock += timedelta(hours=1)
        self.levels[DISK_SIGNAL] = "ok"
        await self.watcher.poll_once(self.db)  # 恢复
        self.clock += timedelta(minutes=10)
        self.levels[DISK_SIGNAL] = "warning"
        await self.watcher.poll_once(self.db)  # 静默期内：不发
        self.clock += timedelta(minutes=10)
        self.levels[DISK_SIGNAL] = "ok"
        await self.watcher.poll_once(self.db)  # 没有在告警状态上：不发恢复

        await self.outbox.dispatch_pending(self.db)
        texts = [text for _, text in self.sender.sent]
        self.assertEqual(len(texts), 2, texts)
        self.assertIn("告警", texts[0])
        self.assertIn("恢复正常", texts[1])

    async def test_a_restart_inside_the_silence_window_does_not_repeat_the_alert(self):
        """进程重启不重发：等级没变，落库的状态里已经记着「这一级通知过了」。"""
        channel = await self._channel("运维群", URL_A)
        await self._subscribe(channel, TOPIC_RESOURCE_WATERMARK)
        self.levels[DISK_SIGNAL] = "warning"
        await self.watcher.poll_once(self.db)

        self.watcher = self.new_watcher()  # 重启：进程内状态全丢
        await self.watcher.poll_once(self.db)

        await self.outbox.dispatch_pending(self.db)
        self.assertEqual(len(self.sender.sent), 1)
        self.assertEqual(len(await self._rows(TOPIC_RESOURCE_WATERMARK)), 1)

    async def test_a_restart_keeps_the_silence_window_of_the_same_level(self):
        """静默期跨重启继续算：重启后同一等级再进来，仍然不重复。"""
        channel = await self._channel("运维群", URL_A)
        await self._subscribe(channel, TOPIC_RESOURCE_WATERMARK)
        self.levels[DISK_SIGNAL] = "warning"
        await self.watcher.poll_once(self.db)
        self.clock += timedelta(minutes=30)
        self.levels[DISK_SIGNAL] = "ok"
        await self.watcher.poll_once(self.db)  # 恢复

        self.watcher = self.new_watcher()  # 重启
        self.clock += timedelta(minutes=30)
        self.levels[DISK_SIGNAL] = "warning"
        await self.watcher.poll_once(self.db)  # 首条告警才过去 1 小时：静默期内

        await self.outbox.dispatch_pending(self.db)
        texts = [text for _, text in self.sender.sent]
        self.assertEqual(len(texts), 2, texts)
        self.assertIn("告警", texts[0])
        self.assertIn("恢复正常", texts[1])

    async def test_each_signal_keeps_its_own_level_and_silence(self):
        """磁盘与内存各走各的状态：内存没变时，磁盘升级到严重仍要通知。"""
        self.watcher = self.new_watcher([DISK_SIGNAL, MEMORY_SIGNAL])
        channel = await self._channel("运维群", URL_A)
        await self._subscribe(channel, TOPIC_RESOURCE_WATERMARK)
        self.levels[MEMORY_SIGNAL] = "warning"
        await self.watcher.poll_once(self.db)

        self.clock += timedelta(minutes=10)
        self.levels[DISK_SIGNAL] = "critical"
        await self.watcher.poll_once(self.db)

        await self.outbox.dispatch_pending(self.db)
        texts = [text for _, text in self.sender.sent]
        self.assertEqual(len(texts), 2, texts)
        self.assertIn("内存", texts[0])
        self.assertIn("告警", texts[0])
        self.assertIn("磁盘", texts[1])
        self.assertIn("严重", texts[1])

    async def test_a_signal_that_cannot_be_read_is_skipped(self):
        """读不到的信号不是等级（内存的 `unknown`）：不发消息，也不算恢复正常。"""
        self.watcher = self.new_watcher([DISK_SIGNAL, MEMORY_SIGNAL])
        channel = await self._channel("运维群", URL_A)
        await self._subscribe(channel, TOPIC_RESOURCE_WATERMARK)
        self.levels[MEMORY_SIGNAL] = "unknown"

        await self.watcher.poll_once(self.db)
        self.clock += timedelta(minutes=10)
        self.levels[MEMORY_SIGNAL] = "warning"
        await self.watcher.poll_once(self.db)

        await self.outbox.dispatch_pending(self.db)
        texts = [text for _, text in self.sender.sent]
        self.assertEqual(len(texts), 1, texts)
        self.assertIn("内存", texts[0])
        self.assertIn("告警", texts[0])


class OpsWatermarkSignalTest(unittest.IsolatedAsyncioTestCase):
    """水位信号读取：等级与阈值都来自既有读数（不另造一套），读不到就没有读数。"""

    def test_disk_level_comes_from_the_existing_guard(self):
        items = [{"path": "/", "used_pct": 93.2, "free_mb": 1200.0, "level": "critical"}]
        with patch.object(disk_guard, "snapshot", return_value=items), patch.object(
            disk_guard, "worst_level", return_value="critical"
        ):
            reading = read_disk_watermark()

        self.assertEqual(reading.signal, DISK_SIGNAL)
        self.assertEqual(reading.level, "critical")
        self.assertIn("/", reading.detail)
        self.assertIn("93.2", reading.detail)

    def test_disk_without_a_readable_partition_is_not_a_reading(self):
        with patch.object(disk_guard, "snapshot", return_value=[]):
            self.assertIsNone(read_disk_watermark())

    def test_memory_level_comes_from_the_existing_manager(self):
        with patch.object(
            memory_manager, "check_memory_pressure", return_value="high"
        ), patch.object(
            memory_manager, "get_memory_usage", return_value={"rss_mb": 812.0}
        ):
            reading = read_memory_watermark()

        self.assertEqual(reading.signal, MEMORY_SIGNAL)
        self.assertEqual(reading.level, "high")
        self.assertIn("812", reading.detail)
        self.assertIn("512", reading.detail)  # 阈值取自 memory_thresholds，不是另写一套

    def test_memory_unknown_is_not_a_reading(self):
        with patch.object(memory_manager, "check_memory_pressure", return_value="unknown"):
            self.assertIsNone(read_memory_watermark())


class OpsSchedulerLoopTest(OpsEventTestCase):
    """驱动方式：挂在既有 30 秒企微调度循环上，**不新增常驻 task**。

    这里只钉「循环每轮会调它」：端到端（入队 → 派发 → 发送记录）由上面那批直接驱动
    ``poll_once`` 的用例覆盖。测试端刻意**不**在循环写库的当口查库、也不在写的中途把
    它打断 —— 取消正好落在一次写事务中间时，同一条连接上排队的等锁没有超时上限
    （PG 后端的 ``_TaskGuard``），测试会假死。
    """

    async def test_the_resident_wecom_loop_drives_the_ops_events(self):
        poll = AsyncMock(return_value=0)

        with patch.object(
            ops_module.ops_event_watcher, "poll_once", new=poll
        ), patch.object(
            wecom_push_service_module, "SCHEDULER_INTERVAL_SECONDS", 0.05
        ):
            task = asyncio.create_task(wecom_push_service.scheduler_loop(self.db))
            try:
                await self._wait_for_calls(poll)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        poll.assert_awaited()

    async def _wait_for_calls(self, mock, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if mock.await_count:
                return
            await asyncio.sleep(0.02)
        self.fail(f"既有调度循环没有在 {timeout}s 内调用运维事件轮询")


class OpsUpdateResultTest(OpsEventTestCase):
    """系统更新结果：状态文件里的终态由**进程读文件**补推，同一版本只推一次。"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # 水位信号不参与这一组：读数注入成空映射（真磁盘的等级不该影响结果推送）。
        self.watcher = self.new_watcher(signals=())
        self.channel = await self._channel("运维群", URL_A)
        await self._subscribe(self.channel, TOPIC_UPDATE_BACKUP)

    async def test_a_terminal_update_result_is_pushed_with_its_version(self):
        self._write_update_state(
            stage=STAGE_FAILED,
            message="pip sync failed",
            error="dependency resolution failed",
            finished_at="2026-05-02T21:25:00+08:00",
        )

        await self.watcher.poll_once(self.db)
        await self.outbox.dispatch_pending(self.db)

        self.assertEqual(len(self.sender.sent), 1)
        url, text = self.sender.sent[0]
        self.assertEqual(url, URL_A)
        self.assertIn("系统更新失败", text)
        self.assertIn("v1.4.0", text)
        self.assertIn("dependency resolution failed", text)

    async def test_a_successful_update_is_pushed_with_the_version_tag(self):
        self._write_update_state(
            stage=STAGE_SUCCEEDED,
            finished_at="2026-05-02T21:25:00+08:00",
        )

        await self.watcher.poll_once(self.db)
        await self.outbox.dispatch_pending(self.db)

        _, text = self.sender.sent[0]
        self.assertIn("系统更新成功", text)
        self.assertIn("v1.4.0", text)

    async def test_the_same_version_is_not_pushed_again_after_a_restart(self):
        self._write_update_state(
            stage=STAGE_FAILED,
            error="dependency resolution failed",
            finished_at="2026-05-02T21:25:00+08:00",
        )
        await self.watcher.poll_once(self.db)

        self.watcher = self.new_watcher(signals=())  # 重启后重新读同一份状态文件
        await self.watcher.poll_once(self.db)
        await self.watcher.poll_once(self.db)

        self.assertEqual(len(await self._rows(TOPIC_UPDATE_BACKUP)), 1)

    async def test_the_same_version_with_a_different_outcome_is_a_new_delivery(self):
        """同一 tag 重跑得出不同结果（失败 → 成功）不是同一件事：再推一条。"""
        self._write_update_state(
            stage=STAGE_FAILED, error="boom", finished_at="2026-05-02T21:25:00+08:00"
        )
        await self.watcher.poll_once(self.db)

        self._write_update_state(
            stage=STAGE_SUCCEEDED, finished_at="2026-05-02T22:05:00+08:00"
        )
        self.watcher = self.new_watcher(signals=() )  # 重启后读到的是新的终态
        await self.watcher.poll_once(self.db)

        await self.outbox.dispatch_pending(self.db)
        texts = [text for _, text in self.sender.sent]
        self.assertEqual(len(texts), 2, texts)
        self.assertIn("系统更新失败", texts[0])
        self.assertIn("系统更新成功", texts[1])

    async def test_a_failed_enqueue_is_retried_on_the_next_round(self):
        """结果是一次事件：入队那一下失败（不是零订阅）不能就此丢掉，下一轮再试。"""
        flaky = FlakyOutbox(self.outbox)
        self.watcher = OpsEventWatcher(
            outbox=flaky, now=lambda: self.clock, readings=lambda: {}
        )
        self._write_update_state(
            stage=STAGE_FAILED, error="boom", finished_at="2026-05-02T21:25:00+08:00"
        )

        await self.watcher.poll_once(self.db)
        self.assertEqual(await self._rows(TOPIC_UPDATE_BACKUP), [])

        await self.watcher.poll_once(self.db)
        self.assertEqual(len(await self._rows(TOPIC_UPDATE_BACKUP)), 1)

    async def test_an_in_progress_job_is_not_pushed(self):
        self._write_update_state(stage=STAGE_QUEUED, finished_at=None)

        await self.watcher.poll_once(self.db)

        self.assertEqual(await self._rows(TOPIC_UPDATE_BACKUP), [])

    async def test_a_missing_state_file_pushes_nothing(self):
        await self.watcher.poll_once(self.db)

        self.assertEqual(await self._rows(TOPIC_UPDATE_BACKUP), [])

    async def test_a_result_nobody_subscribes_to_is_not_delivered(self):
        """零订阅：一条不发（订阅是收件人的唯一来源），也不因此反复重试。"""
        await self.db.wecom_subscription_delete_for(
            TOPIC_UPDATE_BACKUP, target_channel_id=self.channel
        )
        self._write_update_state(stage=STAGE_FAILED, finished_at="2026-05-02T21:25:00+08:00")

        await self.watcher.poll_once(self.db)
        await self.watcher.poll_once(self.db)
        await self.outbox.dispatch_pending(self.db)

        self.assertEqual(await self._rows(TOPIC_UPDATE_BACKUP), [])
        self.assertEqual(self.sender.sent, [])


class OpsColdBackupResultTest(OpsEventTestCase):
    """冷备结果：读 ``cold_backup_status.json``，同一次备份（时间戳）只推一次。"""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.watcher = self.new_watcher(signals=())
        self.channel = await self._channel("运维群", URL_A)
        await self._subscribe(self.channel, TOPIC_UPDATE_BACKUP)

    def _real_cold_run(self, *, ts="20260501_030000", size_bytes=1234):
        """真冷备任务写状态文件的那条路径（写出来的形状必须能被读回来）。"""
        archive = Path(self.cold_dir) / ts / "cold_backup.tar.gz"
        return backup_service.write_cold_backup_status(
            ok=True,
            archive=archive,
            manifest={"archive_bytes": size_bytes, "archive_sha256": "ab" * 32},
        )

    async def test_a_cold_backup_run_is_pushed_once(self):
        self._real_cold_run()

        await self.watcher.poll_once(self.db)
        await self.outbox.dispatch_pending(self.db)

        self.assertEqual(len(self.sender.sent), 1)
        url, text = self.sender.sent[0]
        self.assertEqual(url, URL_A)
        self.assertIn("冷备完成", text)
        self.assertIn("20260501_030000", text)

    async def test_the_same_backup_is_not_pushed_again_after_a_restart(self):
        self._real_cold_run()
        await self.watcher.poll_once(self.db)

        self.watcher = self.new_watcher(signals=())
        await self.watcher.poll_once(self.db)

        self.assertEqual(len(await self._rows(TOPIC_UPDATE_BACKUP)), 1)

    async def test_a_new_backup_timestamp_is_a_new_delivery(self):
        self._real_cold_run(ts="20260501_030000")
        await self.watcher.poll_once(self.db)
        self._real_cold_run(ts="20260502_030000")
        await self.watcher.poll_once(self.db)

        await self.outbox.dispatch_pending(self.db)
        texts = [text for _, text in self.sender.sent]
        self.assertEqual(len(texts), 2, texts)
        self.assertIn("20260501_030000", texts[0])
        self.assertIn("20260502_030000", texts[1])

    async def test_a_failed_run_is_pushed_with_its_reason(self):
        """失败运行没有产物（ts 为空）：退回运行时刻做引用，同样只推一次。"""
        self._write_cold_status(
            ok=False,
            error="pg_dump exited with code 1",
            ran_at="2026-05-02T03:00:05+08:00",
        )

        await self.watcher.poll_once(self.db)
        self.watcher = self.new_watcher(signals=())
        await self.watcher.poll_once(self.db)
        await self.outbox.dispatch_pending(self.db)

        self.assertEqual(len(self.sender.sent), 1)
        _, text = self.sender.sent[0]
        self.assertIn("冷备失败", text)
        self.assertIn("pg_dump exited with code 1", text)

    async def test_a_missing_status_file_pushes_nothing(self):
        await self.watcher.poll_once(self.db)

        self.assertEqual(await self._rows(TOPIC_UPDATE_BACKUP), [])


if __name__ == "__main__":
    unittest.main()
