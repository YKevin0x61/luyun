#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DatabaseManager 的推送订阅职责：订阅 / 渠道群组 / 群组成员 / 出站记录。

「谁收到什么」只有一条真相来源——``wecom_push_subscriptions``（内容类型 × 目标，
目标是推送渠道或渠道群组，见 ADR 0094）；出站记录 ``wecom_push_outbox`` 是
**一次投递一行**的队列表兼发送记录（ADR 0095）。

表由迁移 ``0016_wecom_push_subscriptions.sql`` 建立。异常一律记日志后返回空值 /
``False``，与 ``wecom_repo.py`` 的既有方法一致：调用方（页面与调度循环）拿到的是
「这次没读到 / 没写成」，而不是一个把整条请求打成 500 的异常。
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from db_core.utils import CHINA_TZ

logger = logging.getLogger(__name__)

SUB_OUTBOX_STATUS_PENDING = "pending"
SUB_OUTBOX_STATUS_SENDING = "sending"
SUB_OUTBOX_STATUS_SENT = "sent"
SUB_OUTBOX_STATUS_FAILED = "failed"
SUB_OUTBOX_STATUS_SKIPPED = "skipped"
_OUTBOX_FINISHED_STATUSES = (
    SUB_OUTBOX_STATUS_SENT,
    SUB_OUTBOX_STATUS_FAILED,
    SUB_OUTBOX_STATUS_SKIPPED,
)

# 发送记录页的页码与页大小（票 07）。页大小有上限：一页 200 行已经够在窄屏上翻，
# 再大就变成「一次请求把整张表的正文摘要都拉下来」。
OUTBOX_PAGE_SIZE_DEFAULT = 50
OUTBOX_PAGE_SIZE_MAX = 200


def _now() -> str:
    return datetime.now(CHINA_TZ).isoformat()


class _WecomSubscriptionsRepoMixin:
    """推送订阅、渠道群组与出站记录的增删改查。"""

    # ── 推送订阅 ──────────────────────────────────────────────────────────

    async def wecom_subscriptions_all(
        self, topic_id: Optional[str] = None, include_disabled: bool = True
    ) -> List[Dict]:
        """列出订阅；可按内容类型过滤。目标两列恰好有一个非空。"""
        try:
            tdb = self._connection.table("wecom_push_subscriptions")
            sql = """SELECT id, topic_id, target_channel_id, target_group_id,
                            enabled, created_at, updated_at
                     FROM wecom_push_subscriptions"""
            params: List[Any] = []
            conditions: List[str] = []
            if topic_id:
                conditions.append("topic_id = ?")
                params.append(str(topic_id))
            if not include_disabled:
                conditions.append("enabled = ?")
                params.append(1)
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
            sql += " ORDER BY topic_id ASC, id ASC"
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(sql, params)
                rows = await cursor.fetchall()
            return [self._subscription_row(dict(row)) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取推送订阅失败: {e}")
            return []

    async def wecom_subscription_upsert(self, item: Dict[str, Any]) -> int:
        """登记一条订阅并返回它的 id；同一「内容类型 × 目标」已存在时复用那一行。

        唯一索引落在 (tenant_id, topic_id, 目标列) 上，所以这里的 ``ON CONFLICT``
        与页面上的「勾选 / 取消勾选」共用同一条约束：不会因为重复勾选多出一行。
        """
        topic_id = str(item.get("topic_id") or "").strip()
        channel_id = item.get("target_channel_id")
        group_id = item.get("target_group_id")
        if not topic_id or (channel_id is None) == (group_id is None):
            raise ValueError("订阅必须且只能指定一个目标（渠道或群组）")

        try:
            now = _now()
            enabled = 1 if item.get("enabled", True) else 0
            tdb = self._connection.table("wecom_push_subscriptions")
            if channel_id is not None:
                sql = """INSERT INTO wecom_push_subscriptions
                           (topic_id, target_channel_id, enabled, created_at, updated_at)
                         VALUES (?, ?, ?, ?, ?)
                         ON CONFLICT (tenant_id, topic_id, target_channel_id)
                             WHERE target_channel_id IS NOT NULL
                         DO UPDATE SET enabled = excluded.enabled,
                                       updated_at = excluded.updated_at
                         RETURNING id"""
                params = (topic_id, int(channel_id), enabled, now, now)
            else:
                sql = """INSERT INTO wecom_push_subscriptions
                           (topic_id, target_group_id, enabled, created_at, updated_at)
                         VALUES (?, ?, ?, ?, ?)
                         ON CONFLICT (tenant_id, topic_id, target_group_id)
                             WHERE target_group_id IS NOT NULL
                         DO UPDATE SET enabled = excluded.enabled,
                                       updated_at = excluded.updated_at
                         RETURNING id"""
                params = (topic_id, int(group_id), enabled, now, now)
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(sql, params)
                row = await cursor.fetchone()
            await tdb.commit()
            return int(dict(row)["id"]) if row else 0
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"❌ 保存推送订阅失败: {e}")
            return 0

    async def wecom_subscription_delete(self, subscription_id: int) -> bool:
        try:
            tdb = self._connection.table("wecom_push_subscriptions")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM wecom_push_subscriptions WHERE id = ?",
                    (int(subscription_id),),
                )
            await tdb.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 删除推送订阅失败: {e}")
            return False

    async def wecom_subscription_for(
        self,
        topic_id: str,
        *,
        target_channel_id: Optional[int] = None,
        target_group_id: Optional[int] = None,
    ) -> Optional[Dict]:
        """按「内容类型 × 目标」取那一行订阅（没有就返回 ``None``）。

        审计用（票 11）：勾选 / 取消都要先把**改前的样子**取出来，否则「取消勾选」那条
        记录的变更前后值只能是空的。只是读一行的投影，目标列约定与
        ``wecom_subscriptions_all`` 一致。
        """
        if (target_channel_id is None) == (target_group_id is None):
            raise ValueError("取订阅必须且只能指定一个目标（渠道或群组）")
        try:
            tdb = self._connection.table("wecom_push_subscriptions")
            if target_channel_id is not None:
                sql = """SELECT id, topic_id, target_channel_id, target_group_id, enabled
                          FROM wecom_push_subscriptions
                          WHERE topic_id = ? AND target_channel_id = ?"""
                params: tuple = (str(topic_id), int(target_channel_id))
            else:
                sql = """SELECT id, topic_id, target_channel_id, target_group_id, enabled
                          FROM wecom_push_subscriptions
                          WHERE topic_id = ? AND target_group_id = ?"""
                params = (str(topic_id), int(target_group_id))
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(sql, params)
                row = await cursor.fetchone()
            return self._subscription_row(dict(row)) if row else None
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"❌ 获取推送订阅详情失败: {e}")
            return None

    async def wecom_subscription_delete_for(
        self,
        topic_id: str,
        *,
        target_channel_id: Optional[int] = None,
        target_group_id: Optional[int] = None,
    ) -> int:
        """按「内容类型 × 目标」删订阅，返回删掉的条数。

        页面上的「取消勾选」走这条路（删行），而 ``wecom_subscription_upsert`` 的
        ``enabled=False`` 是**停用**：两者都在库里，语义不同 —— 停用保留行、恢复时不用
        重配；删除是彻底取消。同一目标被两条路径命中时 keyset 恰好命中那一行。
        """
        if (target_channel_id is None) == (target_group_id is None):
            raise ValueError("删订阅必须且只能指定一个目标（渠道或群组）")
        try:
            tdb = self._connection.table("wecom_push_subscriptions")
            if target_channel_id is not None:
                sql = """DELETE FROM wecom_push_subscriptions
                          WHERE topic_id = ? AND target_channel_id = ?"""
                params: tuple = (str(topic_id), int(target_channel_id))
            else:
                sql = """DELETE FROM wecom_push_subscriptions
                          WHERE topic_id = ? AND target_group_id = ?"""
                params = (str(topic_id), int(target_group_id))
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(sql, params)
                removed = cursor.rowcount or 0
            await tdb.commit()
            return int(removed)
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"❌ 删除推送订阅失败: {e}")
            return 0

    # ── 渠道群组与成员 ────────────────────────────────────────────────────

    async def wecom_channel_groups_all(self, include_disabled: bool = True) -> List[Dict]:
        try:
            tdb = self._connection.table("wecom_channel_groups")
            sql = """SELECT id, name, enabled, notes, created_at, updated_at
                     FROM wecom_channel_groups"""
            params: List[Any] = []
            if not include_disabled:
                sql += " WHERE enabled = ?"
                params.append(1)
            sql += " ORDER BY name ASC, id ASC"
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(sql, params)
                rows = await cursor.fetchall()
            return [
                {**dict(row), "enabled": bool(dict(row).get("enabled"))} for row in rows
            ]
        except Exception as e:
            logger.error(f"❌ 获取渠道群组失败: {e}")
            return []

    async def wecom_channel_group_get(self, group_id: int) -> Optional[Dict]:
        try:
            tdb = self._connection.table("wecom_channel_groups")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, name, enabled, notes, created_at, updated_at
                       FROM wecom_channel_groups WHERE id = ?""",
                    (int(group_id),),
                )
                row = await cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            return {**item, "enabled": bool(item.get("enabled"))}
        except Exception as e:
            logger.error(f"❌ 获取渠道群组详情失败: {e}")
            return None

    async def wecom_channel_group_create(self, item: Dict[str, Any]) -> int:
        """建群组；同名已存在时返回 0（不静默复用那一行）。

        复用同名群组是**危险**的：调用方以为新建了一个空群组，实际拿到的是另一个
        群组，往里加成员、挂订阅就动到了别人的收件人。宁可返回 0 让页面提示重名。
        """
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError("群组名称不能为空")
        try:
            tdb = self._connection.table("wecom_channel_groups")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT id FROM wecom_channel_groups WHERE name = ?", (name,)
                )
                if await cursor.fetchone() is not None:
                    logger.error(f"❌ 创建渠道群组失败：同名群组已存在 {name}")
                    return 0
                now = _now()
                await cursor.execute(
                    """INSERT INTO wecom_channel_groups
                       (name, enabled, notes, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        name,
                        1 if item.get("enabled", True) else 0,
                        str(item.get("notes", "")),
                        now,
                        now,
                    ),
                )
                row_id = cursor.lastrowid
            await tdb.commit()
            return int(row_id or 0)
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"❌ 创建渠道群组失败: {e}")
            return 0

    async def wecom_channel_group_update(self, group_id: int, item: Dict[str, Any]) -> bool:
        try:
            existing = await self.wecom_channel_group_get(group_id)
            if not existing:
                return False
            name = str(item.get("name", existing["name"])).strip()
            if not name:
                raise ValueError("群组名称不能为空")
            tdb = self._connection.table("wecom_channel_groups")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """UPDATE wecom_channel_groups
                       SET name = ?, enabled = ?, notes = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        name,
                        1 if item.get("enabled", bool(existing["enabled"])) else 0,
                        str(item.get("notes", existing.get("notes", ""))),
                        _now(),
                        int(group_id),
                    ),
                )
            await tdb.commit()
            return True
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"❌ 更新渠道群组失败: {e}")
            return False

    async def wecom_channel_group_delete(self, group_id: int) -> bool:
        """删除群组：成员与指向它的订阅一起走（外键 ON DELETE CASCADE）。"""
        try:
            tdb = self._connection.table("wecom_channel_groups")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM wecom_channel_groups WHERE id = ?", (int(group_id),)
                )
            await tdb.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 删除渠道群组失败: {e}")
            return False

    async def wecom_channel_group_members(self, group_id: int) -> List[Dict]:
        try:
            tdb = self._connection.table("wecom_channel_group_members")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, group_id, channel_id, created_at
                       FROM wecom_channel_group_members
                       WHERE group_id = ? ORDER BY id ASC""",
                    (int(group_id),),
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取群组成员失败: {e}")
            return []

    async def wecom_channel_group_members_map(self) -> Dict[int, List[Dict]]:
        """一次读完所有群组的成员，返回 ``{group_id: [成员行]}``。

        页面上「每个群组有哪些渠道」与「某个渠道属于哪些群组」是同一份关系的两个方向：
        前者按群组逐条查就是 N 次往返，这里一次取全、在内存里分组。
        """
        try:
            tdb = self._connection.table("wecom_channel_group_members")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, group_id, channel_id, created_at
                       FROM wecom_channel_group_members ORDER BY group_id ASC, id ASC"""
                )
                rows = await cursor.fetchall()
            grouped: Dict[int, List[Dict]] = {}
            for row in rows:
                item = dict(row)
                grouped.setdefault(int(item["group_id"]), []).append(item)
            return grouped
        except Exception as e:
            logger.error(f"❌ 获取群组成员失败: {e}")
            return {}

    async def wecom_channel_groups_of_channel(self, channel_id: int) -> List[Dict]:
        """这个渠道属于哪些群组（渠道卡片与删除提示都要用）。"""
        try:
            tdb = self._connection.table("wecom_channel_group_members")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT g.id, g.name, g.enabled, g.notes, g.created_at, g.updated_at
                       FROM wecom_channel_group_members m
                       JOIN wecom_channel_groups g ON g.id = m.group_id
                       WHERE m.channel_id = ?
                       ORDER BY g.name ASC, g.id ASC""",
                    (int(channel_id),),
                )
                rows = await cursor.fetchall()
            return [
                {**dict(row), "enabled": bool(dict(row).get("enabled"))} for row in rows
            ]
        except Exception as e:
            logger.error(f"❌ 获取渠道所属群组失败: {e}")
            return []

    async def wecom_channel_group_add_member(self, group_id: int, channel_id: int) -> bool:
        """把渠道加进群组；重复加入是幂等的（唯一索引 + ON CONFLICT DO NOTHING）。"""
        try:
            if await self.wecom_channel_group_get(group_id) is None:
                logger.error(f"❌ 渠道群组不存在: {group_id}")
                return False
            tdb = self._connection.table("wecom_channel_group_members")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO wecom_channel_group_members
                       (group_id, channel_id, created_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT (tenant_id, group_id, channel_id) DO NOTHING""",
                    (int(group_id), int(channel_id), _now()),
                )
            await tdb.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 添加群组成员失败: {e}")
            return False

    async def wecom_channel_group_remove_member(self, group_id: int, channel_id: int) -> bool:
        try:
            tdb = self._connection.table("wecom_channel_group_members")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """DELETE FROM wecom_channel_group_members
                       WHERE group_id = ? AND channel_id = ?""",
                    (int(group_id), int(channel_id)),
                )
            await tdb.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 移除群组成员失败: {e}")
            return False

    # ── 出站记录（队列表 = 发送记录） ──────────────────────────────────────

    async def wecom_outbox_enqueue(self, item: Dict[str, Any], *, commit: bool = True) -> int:
        """登记一次投递并返回它的 id；同一幂等键已存在时返回**那一行**的 id。

        返回既有行的 id 而不是 0：调用方（验收登记、调度循环）据此判断「这封已经
        在队列里了」，而不是把它当成失败再试一遍。

        ``created_at`` 可以显式给（迁移搬历史行时要保住原来的时间），不给就取现在。

        ``attempts`` / ``last_error`` / ``finished_at`` 默认是「刚入队、还没发」的样子
        （0 / 空 / 无）。手工发送的登记（测试发送、销售报表页的「推送」）直接写终态：
        它在调用这里之前就已经发出去了，落进来的是一条**已经发生**的投递记录。

        ``commit=False`` 给「登记必须与业务事务同生共死」的调用方（验收照片就是这样）：
        这一行落进调用方的事务里，由调用方的 commit / rollback 决定它到底在不在——
        这里一句都不提交。调用方要保证自己没有把事务搞脏（缺表这类可预期的失败先探一下，
        别让一条失败语句把整个业务事务变成 aborted）。
        """
        topic_id = str(item.get("topic_id") or "").strip()
        if not topic_id:
            raise ValueError("出站记录必须带内容类型")
        target_channel_id = item.get("target_channel_id")
        if target_channel_id is None:
            raise ValueError("出站记录必须带目标渠道")
        idempotency_key = str(item.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise ValueError("出站记录必须带幂等键")

        try:
            now = _now()
            tdb = self._connection.table("wecom_push_outbox")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO wecom_push_outbox
                       (topic_id, params_json, schedule_id, target_channel_id,
                        content_summary, message_bytes, status, attempts, last_error,
                        idempotency_key, scheduled_at, created_at, finished_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                       RETURNING id""",
                    (
                        topic_id,
                        str(item.get("params_json") or "{}"),
                        item.get("schedule_id"),
                        int(target_channel_id),
                        str(item.get("content_summary", "")),
                        int(item.get("message_bytes", 0) or 0),
                        str(item.get("status", SUB_OUTBOX_STATUS_PENDING)),
                        int(item.get("attempts", 0) or 0),
                        str(item.get("last_error", "")),
                        idempotency_key,
                        item.get("scheduled_at"),
                        str(item.get("created_at") or now),
                        item.get("finished_at"),
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    await cursor.execute(
                        """SELECT id FROM wecom_push_outbox
                            WHERE tenant_id = 1 AND idempotency_key = ?""",
                        (idempotency_key,),
                    )
                    row = await cursor.fetchone()
            if commit:
                await tdb.commit()
            return int(dict(row)["id"]) if row else 0
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"❌ 登记出站记录失败: {e}")
            return 0

    async def wecom_outbox_get(self, outbox_id: int) -> Optional[Dict]:
        try:
            tdb = self._connection.table("wecom_push_outbox")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, topic_id, params_json, schedule_id, target_channel_id,
                              content_summary, message_bytes, status, attempts, last_error,
                              idempotency_key, scheduled_at, created_at, sending_at,
                              finished_at
                       FROM wecom_push_outbox WHERE id = ?""",
                    (int(outbox_id),),
                )
                row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ 获取出站记录失败: {e}")
            return None

    async def wecom_outbox_pending(self, limit: int = 20, now: Optional[str] = None) -> List[Dict]:
        """按先进先出取**已到点**的待发（调度循环每轮捞一批）。

        只捞 ``scheduled_at`` 为空或已到点的行：退避 / 补发等待中的行这一轮本来就不会发
        （派发对未到点的行是「留待下轮」），但它们**不能占住这一批的名额** —— 只按 id 取
        最旧 20 行的话，一批正在等 1/5/15 分钟退避的行会把新入队的行挡在批次之外，直到
        它们到点为止（不丢，但违背「超出的行排队、由调度循环继续发」的口径）。

        时间字段是同一格式的 ISO 文本（写入方都用 CHINA_TZ 的 ``isoformat``），可以直接
        比较，与 ``wecom_outbox_stale_sending`` 的 ``COALESCE(...) < ?`` 同一个先例。
        ``now`` 是调用方的时钟，缺省取当前时间：调度循环与测试的假时钟都应当传进来，
        不传就按墙上时钟判定。
        """
        try:
            safe_limit = max(1, min(int(limit), 200))
            cutoff = str(now or _now())
            tdb = self._connection.table("wecom_push_outbox")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, topic_id, params_json, schedule_id, target_channel_id,
                              content_summary, message_bytes, status, attempts, last_error,
                              idempotency_key, scheduled_at, created_at, sending_at,
                              finished_at
                       FROM wecom_push_outbox
                       WHERE status = ? AND COALESCE(scheduled_at, '') <= ?
                       ORDER BY id ASC LIMIT ?""",
                    (SUB_OUTBOX_STATUS_PENDING, cutoff, safe_limit),
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取待发出站记录失败: {e}")
            return []

    async def wecom_outbox_stale_sending(self, cutoff_iso: str, limit: int = 20) -> List[Dict]:
        """捞「发送中卡住」的行：进入 sending 的时刻早于 ``cutoff_iso``（兜底入口）。

        单独一条查询，**不动** ``wecom_outbox_pending`` 的语义：那条是正常派发的入口，
        只捞 pending、先进先出；把发送中的行放宽进去，既让「待发」这个名字名不副实，
        也会把没确认投递结果的行混进正常队列。

        判据是 ``sending_at``（``mark_sending`` 时写入）。迁移前就已经卡住的行没有这一
        列的值，退回 ``created_at``：入队时刻必然不晚于进入 sending 的时刻，宁可早捞
        一会儿，也好过永远停在发送中。
        """
        try:
            safe_limit = max(1, min(int(limit), 200))
            tdb = self._connection.table("wecom_push_outbox")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT id, topic_id, params_json, schedule_id, target_channel_id,
                              content_summary, message_bytes, status, attempts, last_error,
                              idempotency_key, scheduled_at, created_at, sending_at,
                              finished_at
                       FROM wecom_push_outbox
                       WHERE status = ? AND COALESCE(sending_at, created_at) < ?
                       ORDER BY id ASC LIMIT ?""",
                    (SUB_OUTBOX_STATUS_SENDING, str(cutoff_iso), safe_limit),
                )
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取卡住的出站记录失败: {e}")
            return []

    async def wecom_outbox_recent(
        self,
        limit: int = 50,
        status: Optional[str] = None,
        topic_id: Optional[str] = None,
        channel_id: Optional[int] = None,
    ) -> List[Dict]:
        """发送记录：按时间倒序，可按状态 / 内容类型 / 渠道筛选。"""
        try:
            safe_limit = max(1, min(int(limit), 200))
            tdb = self._connection.table("wecom_push_outbox")
            sql = """SELECT id, topic_id, params_json, schedule_id, target_channel_id,
                            content_summary, message_bytes, status, attempts, last_error,
                            idempotency_key, scheduled_at, created_at, sending_at,
                            finished_at
                     FROM wecom_push_outbox"""
            params: List[Any] = []
            conditions: List[str] = []
            if status:
                conditions.append("status = ?")
                params.append(str(status))
            if topic_id:
                conditions.append("topic_id = ?")
                params.append(str(topic_id))
            if channel_id is not None:
                conditions.append("target_channel_id = ?")
                params.append(int(channel_id))
            if conditions:
                sql += " WHERE " + " AND ".join(conditions)
            sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
            params.append(safe_limit)
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(sql, params)
                rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ 获取发送记录失败: {e}")
            return []

    async def wecom_outbox_page(
        self,
        *,
        page: int = 1,
        page_size: int = OUTBOX_PAGE_SIZE_DEFAULT,
        topic_id: Optional[str] = None,
        channel_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """发送记录的一页（票 07）：按时间倒序 + 内容类型 / 渠道 / 状态筛选 + 总数。

        与 ``wecom_outbox_recent`` 分开而不是改它：那条是「最近 N 条」的口径，被页面
        首屏与调度侧读着，加了 offset / 总数就换了语义。这一条专门服务于带筛选的
        记录页 —— 返回 ``{rows, total, page, page_size, pages}``。

        页大小与页码一律收进安全区间（外部输入不进 SQL 的 ``LIMIT`` / ``OFFSET``）：
        ``page_size`` 收进 1..200，``page`` 不小于 1。``total`` 是**筛选之后**的总数，
        页面据此算得出还有没有下一页。
        """
        try:
            safe_size = max(1, min(int(page_size), OUTBOX_PAGE_SIZE_MAX))
            safe_page = max(1, int(page))
            conditions: List[str] = []
            params: List[Any] = []
            if topic_id:
                conditions.append("topic_id = ?")
                params.append(str(topic_id))
            if channel_id is not None:
                conditions.append("target_channel_id = ?")
                params.append(int(channel_id))
            if status:
                conditions.append("status = ?")
                params.append(str(status))
            where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

            tdb = self._connection.table("wecom_push_outbox")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    f"SELECT COUNT(*) AS total FROM wecom_push_outbox{where}", params
                )
                counted = await cursor.fetchone()
                total = int(dict(counted)["total"]) if counted else 0
                await cursor.execute(
                    f"""SELECT id, topic_id, params_json, schedule_id, target_channel_id,
                               content_summary, message_bytes, status, attempts, last_error,
                               idempotency_key, scheduled_at, created_at, sending_at,
                               finished_at
                        FROM wecom_push_outbox{where}
                        ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
                    [*params, safe_size, (safe_page - 1) * safe_size],
                )
                rows = await cursor.fetchall()
            return {
                "rows": [dict(row) for row in rows],
                "total": total,
                "page": safe_page,
                "page_size": safe_size,
                "pages": (total + safe_size - 1) // safe_size if total else 0,
            }
        except Exception as e:
            logger.error(f"❌ 获取发送记录失败: {e}")
            return {
                "rows": [],
                "total": 0,
                "page": max(1, int(page)),
                "page_size": max(1, min(int(page_size), OUTBOX_PAGE_SIZE_MAX)),
                "pages": 0,
            }

    async def wecom_channel_last_sent(self) -> Dict[int, str]:
        """每个渠道最近一次**成功**投递的时间，``{channel_id: 时间戳}``。

        按渠道聚合（``MAX(finished_at)``）而不是「取最近 N 条成功记录再挑首条」：采样
        版本里，一个长期没发过的群，它那条成功记录早被挤出窗口，渠道卡片就把它显示成
        「从未发送」—— 而「这个地址是不是失效了」正是卡片上这一眼要回答的问题。

        没成功过的渠道**不出现在结果里**（页面据此显示「从未发送」）。历史搬入的行没
        有 ``finished_at``，退回 ``created_at``：入队时刻就是当时那次投递的时刻。
        """
        try:
            tdb = self._connection.table("wecom_push_outbox")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT target_channel_id AS channel_id,
                              MAX(COALESCE(finished_at, created_at)) AS last_sent_at
                       FROM wecom_push_outbox
                       WHERE status = ? AND target_channel_id IS NOT NULL
                       GROUP BY target_channel_id""",
                    (SUB_OUTBOX_STATUS_SENT,),
                )
                rows = await cursor.fetchall()
            return {
                int(dict(row)["channel_id"]): str(dict(row)["last_sent_at"])
                for row in rows
                if dict(row).get("last_sent_at")
            }
        except Exception as e:
            logger.error(f"❌ 聚合渠道最近发送时间失败: {e}")
            return {}

    async def wecom_outbox_mark_sending(
        self, outbox_id: int, *, sending_at: Optional[str] = None
    ) -> bool:
        """标记「发送中」，并记下进入发送的时刻。重试路径由调用方先经
        ``wecom_outbox_mark_retry`` 放回待发。

        ``sending_at`` 是兜底判据（超过阈值还没写终态就要被捞回来），由调用方给：派发
        路径传自己的时钟，测试用假时钟驱动；不给就取现在。

        **不清 ``last_error``**：这一行正在发，上一条失败的原因对排查还有价值；进程要
        是在这中间退出，兜底把行捞回来时那句错误还在（另见
        ``services/wecom_outbox.py::requeue_stale_sending``）。
        """
        return await self._outbox_set_status(
            outbox_id,
            SUB_OUTBOX_STATUS_SENDING,
            sending_at=str(sending_at or _now()),
        )

    async def wecom_outbox_mark_sent(
        self,
        outbox_id: int,
        message_bytes: Optional[int] = None,
        *,
        attempts: Optional[int] = None,
    ) -> bool:
        """标记已发（终态）。``attempts`` 是含这次成功在内的尝试次数。"""
        try:
            sets = ["status = ?", "last_error = ''"]
            params: List[Any] = [SUB_OUTBOX_STATUS_SENT]
            if message_bytes is not None:
                sets.append("message_bytes = ?")
                params.append(int(message_bytes))
            sets.append("finished_at = ?")
            params.append(_now())
            if attempts is not None:
                sets.append("attempts = ?")
                params.append(int(attempts))
            params.append(int(outbox_id))
            tdb = self._connection.table("wecom_push_outbox")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    f"UPDATE wecom_push_outbox SET {', '.join(sets)} WHERE id = ?",
                    params,
                )
            await tdb.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 标记出站记录已发失败: {e}")
            return False

    async def wecom_outbox_mark_retry(
        self,
        outbox_id: int,
        error: str,
        status: str,
        attempts: int,
        *,
        scheduled_at: Optional[str] = None,
    ) -> bool:
        """失败后放回待发（或按状态机落到别的非终态），并留下错误与尝试次数。

        ``scheduled_at`` 是**下一次尝试的时间**：事件类按退避（1 / 5 / 15 分钟），
        定时类按当天补发（约 10 分钟）。写进库里而不是只放内存里，重启后仍然看得见
        「这条什么时候再试」。
        """
        return await self._outbox_set_status(
            outbox_id,
            status,
            last_error=error,
            attempts=attempts,
            scheduled_at=scheduled_at,
        )

    async def wecom_outbox_mark_failed(
        self, outbox_id: int, error: str, attempts: Optional[int] = None
    ) -> bool:
        return await self._outbox_set_status(
            outbox_id, SUB_OUTBOX_STATUS_FAILED, last_error=error, attempts=attempts,
            finished=True,
        )

    async def wecom_outbox_mark_skipped(self, outbox_id: int, reason: str) -> bool:
        """没有收件人（渠道停用 / 零订阅）：跳过并写明原因，不重试。"""
        return await self._outbox_set_status(
            outbox_id, SUB_OUTBOX_STATUS_SKIPPED, last_error=reason, finished=True
        )

    async def _outbox_set_status(
        self,
        outbox_id: int,
        status: str,
        *,
        last_error: Optional[str] = None,
        attempts: Optional[int] = None,
        scheduled_at: Optional[str] = None,
        sending_at: Optional[str] = None,
        finished: bool = False,
    ) -> bool:
        try:
            sets = ["status = ?"]
            params: List[Any] = [str(status)]
            if last_error is not None:
                sets.append("last_error = ?")
                params.append(str(last_error)[:500])
            if attempts is not None:
                sets.append("attempts = ?")
                params.append(int(attempts))
            if scheduled_at is not None:
                sets.append("scheduled_at = ?")
                params.append(str(scheduled_at))
            if sending_at is not None:
                sets.append("sending_at = ?")
                params.append(str(sending_at))
            if finished:
                sets.append("finished_at = ?")
                params.append(_now())
            params.append(int(outbox_id))
            tdb = self._connection.table("wecom_push_outbox")
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    f"UPDATE wecom_push_outbox SET {', '.join(sets)} WHERE id = ?",
                    params,
                )
            await tdb.commit()
            return True
        except Exception as e:
            logger.error(f"❌ 更新出站记录状态失败: {e}")
            return False

    async def wecom_outbox_purge_finished_before(self, cutoff_iso: str) -> int:
        """清理过期的**终态**行，返回删除条数。

        只碰 sent / failed / skipped：待发与发送中的行不管多老都不能删——那是还没
        发出去的消息，删掉就是静默丢失。
        """
        try:
            tdb = self._connection.table("wecom_push_outbox")
            placeholders = ", ".join("?" for _ in _OUTBOX_FINISHED_STATUSES)
            async with tdb.conn.cursor() as cursor:
                await cursor.execute(
                    f"""DELETE FROM wecom_push_outbox
                         WHERE status IN ({placeholders}) AND created_at < ?""",
                    (*_OUTBOX_FINISHED_STATUSES, str(cutoff_iso)),
                )
                removed = cursor.rowcount or 0
            await tdb.commit()
            return int(removed)
        except Exception as e:
            logger.error(f"❌ 清理出站记录失败: {e}")
            return 0

    # ── 行整形 ────────────────────────────────────────────────────────────

    @staticmethod
    def _subscription_row(row: Dict[str, Any]) -> Dict[str, Any]:
        return {**row, "enabled": bool(row.get("enabled"))}
