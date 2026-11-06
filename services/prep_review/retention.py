#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货复盘的 90 天保留策略（票 07 / ADR 0130）。

三张表三种处理，理由见 `docs/adr/0130-prep-review-signals.md`：

- `prep_plan_snapshots`：近 90 天保留**全部小时行**；更早的只留 **07:00 主口径**
  那一条。复盘页的主指标只读 07:00（ADR 0129），旧钟点对结论没有增量，却把行数
  撑成 24 倍。
- `prep_plan_events`：90 天前**整行删除**。每日汇总计数（按 `plan_day` 数事件）
  是下一步，本票不新开迁移，只做删除并留给 ADR 记 TODO。
- `prep_plan_actuals`：**一行不动**。实际销量是复盘证据，和快照同期限；只降采样
  快照、删掉实际会让「多备少备的代价」这类回看永久失真。

所有删除条件是纯时间窗 + 钟点，**幂等可重入**：重复跑第二次删 0 行。失败只记日志
不抛——调用方是日志维护的常驻循环，清理失败不能拖垮日志维护或快照写入。
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

from database import CHINA_TZ

logger = logging.getLogger(__name__)

#: 原始小时快照与事件的保留天数；更早的快照只留主口径。
RETENTION_DAYS = 90

#: 复盘页的主口径钟点（师傅真正下单备货那一次，ADR 0129）。
PRIMARY_CLOCK = "07:00"

#: 时间列是 TEXT ISO8601（带 +08:00 偏移，仓库既有口径）。判断「本地钟点」时先
#: 转 timestamptz 再落到门店时区，这样即使将来有别的偏移写进来也不会看错钟点。
LOCAL_TIME_ZONE = "Asia/Shanghai"

_DELETE_SNAPSHOTS_SQL = """
DELETE FROM prep_plan_snapshots
WHERE snapshot_at::timestamptz < ?
  AND to_char(
        snapshot_at::timestamptz AT TIME ZONE '{tz}', 'HH24:MI'
      ) <> '{clock}'
""".format(tz=LOCAL_TIME_ZONE, clock=PRIMARY_CLOCK)

_DELETE_EVENTS_SQL = """
DELETE FROM prep_plan_events
WHERE at::timestamptz < ?
"""


class PrepReviewRetention:
    """备货复盘的降采样 / 过期删除。

    实例本身只多记一个「今天清过了」的日期：日志维护节拍每 6 小时经过这里，但真正
    的清理一天最多跑一次。进程重启后最多再跑一次，删除本身幂等。
    """

    def __init__(self, retention_days: int = RETENTION_DAYS) -> None:
        self._days = max(1, int(retention_days))
        self._last_run_day: Optional[date] = None

    @property
    def retention_days(self) -> int:
        return self._days

    def cutoff(self, now: Optional[datetime] = None) -> datetime:
        """保留窗口的起点：`now - retention_days`。"""
        now = now or datetime.now(CHINA_TZ)
        return now - timedelta(days=self._days)

    async def purge(self, db, now: Optional[datetime] = None) -> Dict[str, Any]:
        """跑一轮清理；**不抛异常**，返回各表的删除行数。

        `success` 只有在两张表都处理成功时才是 True。单表失败仍继续处理另一张，
        避免一张表的短暂锁等待把另一张表的清理也停掉。
        """
        now = now or datetime.now(CHINA_TZ)
        cutoff = self.cutoff(now)
        result: Dict[str, Any] = {
            "success": True,
            "cutoff": cutoff.isoformat(),
            "snapshots_deleted": 0,
            "events_deleted": 0,
        }

        snapshots = await self._delete(
            db, "prep_plan_snapshots", _DELETE_SNAPSHOTS_SQL, cutoff
        )
        result["snapshots_deleted"] = snapshots["deleted"]
        if snapshots["error"]:
            result["success"] = False
            result["snapshots_error"] = snapshots["error"]

        events = await self._delete(db, "prep_plan_events", _DELETE_EVENTS_SQL, cutoff)
        result["events_deleted"] = events["deleted"]
        if events["error"]:
            result["success"] = False
            result["events_error"] = events["error"]

        if result["snapshots_deleted"] or result["events_deleted"]:
            logger.info(
                "🧹 备货复盘清理：降采样/删除 %s 条快照、%s 条事件（%s 之前）",
                result["snapshots_deleted"],
                result["events_deleted"],
                cutoff.isoformat(),
            )
        return result

    async def _delete(
        self, db, table_name: str, sql: str, cutoff: datetime
    ) -> Dict[str, Any]:
        outcome: Dict[str, Any] = {"deleted": 0, "error": None}
        conn = None
        try:
            # 两张表都已在 `db_core/schema.py` 登记；走 `db.table()` 拿同一条主
            # 连接，表名清单仍是唯一事实来源。
            conn = db.table(table_name).conn
            async with conn.cursor() as cursor:
                await cursor.execute(sql, (cutoff,))
                outcome["deleted"] = max(0, int(cursor.rowcount or 0))
            await conn.commit()
        except Exception as exc:  # noqa: BLE001 - 清理失败只记日志，不拖垮调用方
            outcome["deleted"] = 0
            outcome["error"] = str(exc)
            logger.warning("⚠️ 备货复盘清理 %s 失败（不影响快照）: %s", table_name, exc)
            if conn is not None:
                try:
                    await conn.rollback()
                except Exception:  # noqa: BLE001 - 回滚失败没有补救手段
                    pass
        return outcome

    async def run_daily(
        self, db, now: Optional[datetime] = None
    ) -> Optional[Dict[str, Any]]:
        """每日一次的入口，挂在现有日志维护循环上（`LogStorage._run_maintenance`）。

        同一个本地自然日只跑一次；失败不记「跑过了」，下一次维护节拍会重试（删除幂等）。
        返回值：本轮执行了清理时是 `purge()` 的结果，当天已跑过时是 None。
        """
        now = now or datetime.now(CHINA_TZ)
        today = now.date()
        if today == self._last_run_day:
            return None
        try:
            result = await self.purge(db, now)
        except Exception as exc:  # noqa: BLE001 - 兜底：入口自身不能抛
            logger.warning("⚠️ 备货复盘清理入口异常（不影响快照）: %s", exc)
            return {"success": False, "error": str(exc)}
        if result.get("success"):
            self._last_run_day = today
        return result


#: 进程内单例；只记「今天跑过没有」，没有跨进程状态。
prep_review_retention = PrepReviewRetention()
