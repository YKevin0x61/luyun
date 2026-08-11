#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DatabaseManager 的企业微信推送职责：webhook / 推送任务 / 推送日志。
"""

import logging
from datetime import datetime
from typing import List, Dict, Optional, Any

from db_core.utils import CHINA_TZ

logger = logging.getLogger(__name__)


class _WecomRepoMixin:
    """企业微信 webhook、推送任务与推送日志的增删改查。"""

    async def wecom_webhooks_all(self, include_disabled: bool = True) -> List[Dict]:
        try:
            tdb = self.table("wecom_push_webhooks")
            sql = """SELECT id, name, webhook_url_encrypted, webhook_url_masked,
                            enabled, notes, created_at, updated_at
                     FROM wecom_push_webhooks"""
            params: List[Any] = []
            if not include_disabled:
                sql += " WHERE enabled = ?"
                params.append(1)
            sql += " ORDER BY updated_at DESC, id DESC"
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(sql, params)
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取企微 webhook 失败: {e}")
            return []

    async def wecom_webhook_get(self, webhook_id: int) -> Optional[Dict]:
        try:
            tdb = self.table("wecom_push_webhooks")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, name, webhook_url_encrypted, webhook_url_masked,
                              enabled, notes, created_at, updated_at
                       FROM wecom_push_webhooks WHERE id = ?""",
                    (webhook_id,),
                )
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ 获取企微 webhook 详情失败: {e}")
            return None

    async def wecom_webhook_create(self, item: Dict[str, Any]) -> int:
        try:
            now = datetime.now(CHINA_TZ).isoformat()
            tdb = self.table("wecom_push_webhooks")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO wecom_push_webhooks
                       (name, webhook_url_encrypted, webhook_url_masked, enabled, notes, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item["name"],
                        item["webhook_url_encrypted"],
                        item.get("webhook_url_masked", ""),
                        1 if item.get("enabled", True) else 0,
                        item.get("notes", ""),
                        now,
                        now,
                    ),
                )
                row_id = cursor.lastrowid
            await tdb.commit()
            return row_id or 0
        except Exception as e:
            logger.error(f"❌ 创建企微 webhook 失败: {e}")
            return 0

    async def wecom_webhook_update(self, webhook_id: int, item: Dict[str, Any]) -> bool:
        try:
            existing = await self.wecom_webhook_get(webhook_id)
            if not existing:
                return False
            name = item.get("name", existing["name"])
            encrypted_url = item.get("webhook_url_encrypted", existing["webhook_url_encrypted"])
            masked_url = item.get("webhook_url_masked", existing["webhook_url_masked"])
            enabled = item.get("enabled", bool(existing["enabled"]))
            notes = item.get("notes", existing.get("notes", ""))
            now = datetime.now(CHINA_TZ).isoformat()
            tdb = self.table("wecom_push_webhooks")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """UPDATE wecom_push_webhooks
                       SET name = ?, webhook_url_encrypted = ?, webhook_url_masked = ?,
                           enabled = ?, notes = ?, updated_at = ?
                       WHERE id = ?""",
                    (name, encrypted_url, masked_url, 1 if enabled else 0, notes, now, webhook_id),
                )
            await tdb.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 更新企微 webhook 失败: {e}")
            return False

    async def wecom_webhook_delete(self, webhook_id: int) -> bool:
        try:
            tdb = self.table("wecom_push_webhooks")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute("DELETE FROM wecom_push_webhooks WHERE id = ?", (webhook_id,))
            await tdb.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 删除企微 webhook 失败: {e}")
            return False

    async def wecom_jobs_all(self, include_disabled: bool = True) -> List[Dict]:
        try:
            tdb = self.table("wecom_push_jobs")
            sql = """SELECT id, name, webhook_id, push_type, schedule_time,
                            date_range_mode, station, enabled, last_sent_date,
                            notes, created_at, updated_at
                     FROM wecom_push_jobs"""
            params: List[Any] = []
            if not include_disabled:
                sql += " WHERE enabled = ?"
                params.append(1)
            sql += " ORDER BY schedule_time ASC, id ASC"
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(sql, params)
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取企微推送任务失败: {e}")
            return []

    async def wecom_job_get(self, job_id: int) -> Optional[Dict]:
        try:
            tdb = self.table("wecom_push_jobs")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, name, webhook_id, push_type, schedule_time,
                              date_range_mode, station, enabled, last_sent_date,
                              notes, created_at, updated_at
                       FROM wecom_push_jobs WHERE id = ?""",
                    (job_id,),
                )
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ 获取企微推送任务详情失败: {e}")
            return None

    async def wecom_job_create(self, item: Dict[str, Any]) -> int:
        try:
            now = datetime.now(CHINA_TZ).isoformat()
            tdb = self.table("wecom_push_jobs")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO wecom_push_jobs
                       (name, webhook_id, push_type, schedule_time, date_range_mode,
                        station, enabled, last_sent_date, notes, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item["name"],
                        int(item["webhook_id"]),
                        item.get("push_type", "sales_report_text"),
                        item["schedule_time"],
                        item.get("date_range_mode", "today"),
                        item.get("station", ""),
                        1 if item.get("enabled", True) else 0,
                        item.get("last_sent_date", ""),
                        item.get("notes", ""),
                        now,
                        now,
                    ),
                )
                row_id = cursor.lastrowid
            await tdb.commit()
            return row_id or 0
        except Exception as e:
            logger.error(f"❌ 创建企微推送任务失败: {e}")
            return 0

    async def wecom_job_update(self, job_id: int, item: Dict[str, Any]) -> bool:
        try:
            existing = await self.wecom_job_get(job_id)
            if not existing:
                return False
            now = datetime.now(CHINA_TZ).isoformat()
            tdb = self.table("wecom_push_jobs")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """UPDATE wecom_push_jobs
                       SET name = ?, webhook_id = ?, push_type = ?, schedule_time = ?,
                           date_range_mode = ?, station = ?, enabled = ?,
                           last_sent_date = ?, notes = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        item.get("name", existing["name"]),
                        int(item.get("webhook_id", existing["webhook_id"])),
                        item.get("push_type", existing["push_type"]),
                        item.get("schedule_time", existing["schedule_time"]),
                        item.get("date_range_mode", existing["date_range_mode"]),
                        item.get("station", existing.get("station", "")),
                        1 if item.get("enabled", bool(existing["enabled"])) else 0,
                        item.get("last_sent_date", existing.get("last_sent_date", "")),
                        item.get("notes", existing.get("notes", "")),
                        now,
                        job_id,
                    ),
                )
            await tdb.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 更新企微推送任务失败: {e}")
            return False

    async def wecom_job_delete(self, job_id: int) -> bool:
        try:
            tdb = self.table("wecom_push_jobs")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute("DELETE FROM wecom_push_jobs WHERE id = ?", (job_id,))
            await tdb.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 删除企微推送任务失败: {e}")
            return False

    async def wecom_job_mark_sent(self, job_id: int, sent_date: str) -> bool:
        return await self.wecom_job_update(job_id, {"last_sent_date": sent_date})

    async def wecom_log_add(self, item: Dict[str, Any]) -> int:
        try:
            tdb = self.table("wecom_push_logs")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO wecom_push_logs
                       (job_id, webhook_id, webhook_name, push_type, status,
                        message_bytes, error, response_text, sent_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item.get("job_id"),
                        item.get("webhook_id"),
                        item.get("webhook_name", ""),
                        item.get("push_type", ""),
                        item["status"],
                        int(item.get("message_bytes", 0) or 0),
                        item.get("error", ""),
                        item.get("response_text", ""),
                        item.get("sent_at", datetime.now(CHINA_TZ).isoformat()),
                    ),
                )
                row_id = cursor.lastrowid
            await tdb.commit()
            return row_id or 0
        except Exception as e:
            logger.error(f"❌ 写入企微推送日志失败: {e}")
            return 0

    async def wecom_logs_recent(self, limit: int = 50) -> List[Dict]:
        try:
            safe_limit = max(1, min(int(limit), 200))
            tdb = self.table("wecom_push_logs")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, job_id, webhook_id, webhook_name, push_type, status,
                              message_bytes, error, response_text, sent_at
                       FROM wecom_push_logs
                       ORDER BY sent_at DESC, id DESC LIMIT ?""",
                    (safe_limit,),
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取企微推送日志失败: {e}")
            return []
