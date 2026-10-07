#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运维事件推送（票 10）：磁盘 / 内存水位、「系统更新 + 冷备结果」。

两类内容的触发点都不在「发消息这一侧」：

- **水位**是**状态**不是事件：等级从正常跨到告警 / 严重才投递，同一等级有静默期，
  回到正常投一条恢复通知。信号只读 ``services/disk_guard.py`` 与
  ``services/memory_manager.py`` 的现有读数与阈值，不在别处复制一套。
- **更新 / 冷备结果**都在应用进程之外执行（更新作业会重启应用，冷备由 systemd timer
  触发），推不到「结束那一刻」：作业把结果写进状态文件，进程启动后或循环里读文件补推。
  更新失败时应用通常没有被重启，所以「仍在运行的进程轮询发现终态」也要成立。

驱动方式：**挂在既有 30 秒企微调度循环上**（``wecom_push_service.scheduler_loop`` 每轮
调用 ``poll_once``），不新增常驻 task（单 worker 约束，常驻 task 清单有测试钉着）。

幂等放在两处，各有各的理由：

- 水位用 ``app_settings`` 的 ``wecom_ops_watermark_state``（等级 + 各等级最后通知时刻）：
  静默期必须**跨重启**继续算，否则每次重启都会把同一次告警再发一遍；
- 更新 / 冷备用出站表的业务引用（版本 tag / 备份时间戳）：唯一索引 ``(tenant_id,
  idempotency_key)`` 就是去重本身，重启后重读同一份状态文件不会再落一行。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Mapping, Optional, Set

from db_core.utils import CHINA_TZ
from services import backup_service
from services.disk_guard import disk_guard
from services.memory_manager import memory_manager
from services.release_update import (
    STAGE_FAILED,
    STAGE_SUCCEEDED,
    STAGE_SUCCEEDED_BUT_UNHEALTHY,
    TERMINAL_STAGES,
    UpdateJobState,
)
from services.release_update.job_state import FileJobStateStore
from services.wecom_outbox import wecom_outbox
from services.wecom_push_topics import (
    TOPIC_RESOURCE_WATERMARK,
    TOPIC_UPDATE_BACKUP,
    PushTrigger,
)

logger = logging.getLogger(__name__)

# ── 水位 ────────────────────────────────────────────────────────────────────

DISK_SIGNAL = "disk"
MEMORY_SIGNAL = "memory"

SIGNAL_LABELS: Dict[str, str] = {DISK_SIGNAL: "磁盘", MEMORY_SIGNAL: "内存"}

# 等级取值直接沿用信号侧的**原样字符串**（磁盘 ok/warning/critical，内存
# normal/warning/high/critical），不折叠成统一的几档：折叠会把内存的「偏高」吞进
# 「告警」，等级变化就少一次通知。这里只声明「哪些等级算正常」与显示名。
LEVEL_OK = "ok"
NORMAL_LEVELS = frozenset({LEVEL_OK, "normal"})
ALERT_ICON = {"critical": "🚨"}
LEVEL_LABELS: Dict[str, str] = {
    "ok": "正常",
    "normal": "正常",
    "warning": "告警",
    "high": "偏高",
    "critical": "严重",
}

# 同一等级的静默期（spec「运维类内容」：6 小时）。它的作用是**防刷屏**：等级在
# 告警与正常之间来回抖动时，同一个等级 6 小时内只发一次，恢复通知不受它限制。
WATERMARK_SILENCE_SECONDS = 6 * 3600

# 水位状态（等级 + 各等级最后通知时刻）落在 app_settings：静默期要跨重启继续算。
WATERMARK_STATE_KEY = "wecom_ops_watermark_state"


@dataclass(frozen=True)
class WatermarkReading:
    """一路水位的当前读数。

    ``level`` 是信号侧原本的等级字符串；``detail`` 是给人看的一行（路径与占用率 /
    进程内存），正文由它拼出来 —— 读数怎么来的留在各自的读取函数里。
    """

    signal: str
    level: str
    detail: str = ""


def read_disk_watermark() -> Optional[WatermarkReading]:
    """磁盘水位：等级取 ``disk_guard.worst_level()``，明细取 ``snapshot()``。

    读不到任何分区（``snapshot()`` 为空）时返回 ``None`` —— 那是「信号不可用」，
    不是「恢复正常」：把它当成正常会凭空发一条恢复通知。
    """
    items = disk_guard.snapshot()
    if not items:
        return None
    detail = "；".join(
        f"{item['path']} 已用 {item['used_pct']}%（剩余 {item['free_mb']}MB）"
        for item in items
    )
    return WatermarkReading(signal=DISK_SIGNAL, level=disk_guard.worst_level(), detail=detail)


def read_memory_watermark() -> Optional[WatermarkReading]:
    """内存水位：等级取 ``memory_manager.check_memory_pressure()``。

    ``unknown``（读内存失败）不是等级，返回 ``None`` 交给调用方跳过；阈值文案从
    ``memory_thresholds`` 取，不另写一套数字。
    """
    level = str(memory_manager.check_memory_pressure() or "").strip()
    if level not in LEVEL_LABELS:
        return None  # 'unknown'（读内存失败）不是等级：本轮跳过这一路
    usage = memory_manager.get_memory_usage()
    thresholds = memory_manager.memory_thresholds
    detail = (
        f"进程 RSS {float(usage.get('rss_mb') or 0):.0f}MB"
        f"（告警 ≥{thresholds['warning'] // (1024 * 1024)}MB、"
        f"严重 ≥{thresholds['critical'] // (1024 * 1024)}MB）"
    )
    return WatermarkReading(signal=MEMORY_SIGNAL, level=level, detail=detail)


def read_watermarks() -> Dict[str, WatermarkReading]:
    """两路水位的当前读数；读不到的那一路**不在**字典里（本轮不动它的状态）。"""
    readings: Dict[str, WatermarkReading] = {}
    for reading in (read_disk_watermark(), read_memory_watermark()):
        if reading is not None:
            readings[reading.signal] = reading
    return readings


def _format_moment(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")


def build_watermark_text(
    reading: WatermarkReading, *, recovered: bool, now: datetime
) -> str:
    """水位通知的正文：等级 + 这一路读数的明细 + 时间。"""
    label = SIGNAL_LABELS.get(reading.signal, reading.signal)
    if recovered:
        title = f"✅ {label}水位已恢复正常"
    else:
        icon = ALERT_ICON.get(reading.level, "⚠️")
        title = f"{icon} {label}水位{LEVEL_LABELS.get(reading.level, reading.level)}"
    lines = [title]
    if reading.detail:
        lines.append(reading.detail)
    lines.append(f"时间：{_format_moment(now)}")
    return "\n".join(lines)


@dataclass
class WatermarkState:
    """一路水位的进程内状态（落库的是它的 ``payload``）。"""

    observed: Optional[str] = None
    notified: str = LEVEL_OK
    notified_at: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "WatermarkState":
        stamps = payload.get("notified_at")
        return cls(
            observed=payload.get("observed"),
            notified=str(payload.get("notified") or LEVEL_OK),
            notified_at={
                str(level): str(stamp)
                for level, stamp in (stamps.items() if isinstance(stamps, dict) else ())
            },
        )

    def payload(self) -> Dict[str, Any]:
        return {
            "observed": self.observed,
            "notified": self.notified,
            "notified_at": dict(self.notified_at),
        }

    def silence_expired(self, level: str, now: datetime) -> bool:
        """这个等级距上次通知是否已过静默期（没通知过 = 已过期）。"""
        stamp = self.notified_at.get(level)
        if not stamp:
            return True
        try:
            last = datetime.fromisoformat(stamp)
        except ValueError:
            return True  # 时间戳坏了不该把这一路永久静默
        if last.tzinfo is None:
            last = last.replace(tzinfo=CHINA_TZ)
        return (now - last).total_seconds() >= WATERMARK_SILENCE_SECONDS


class OpsEventWatcher:
    """运维事件的轮询器：水位状态机 + 更新 / 冷备结果的补推。

    ``outbox`` / ``now`` / ``readings`` 可注入，测试用假发送器、假时钟与假读数驱动整套
    行为（真磁盘与真内存的等级没法用假时钟推动）。
    """

    def __init__(
        self,
        *,
        outbox: Any = None,
        now: Optional[Callable[[], datetime]] = None,
        readings: Optional[Callable[[], Mapping[str, WatermarkReading]]] = None,
    ) -> None:
        self._outbox = wecom_outbox if outbox is None else outbox
        self._now = now or (lambda: datetime.now(CHINA_TZ))
        self._readings = read_watermarks if readings is None else readings
        self._watermarks: Optional[Dict[str, WatermarkState]] = None
        # 本次进程里已经处理过的结果引用（处理 = 尝试过入队，含零订阅）：状态文件里
        # 的终态会一直躺在那里，不记一笔就会每 30 秒重试一次、每次都记一条零订阅告警。
        self._handled_results: Set[str] = set()

    # ── 入口 ──────────────────────────────────────────────────────────────

    async def poll_once(self, db) -> int:
        """跑一轮（既有 30 秒企微调度循环每轮调用），返回本轮入队的投递行数。

        两半各自兜住异常：水位读数的毛病不该让「更新 / 冷备结果」也补推不出去。
        """
        enqueued = 0
        for step in (self.poll_watermarks, self.poll_results):
            try:
                enqueued += await step(db)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("运维事件轮询失败（%s）：%s", step.__name__, exc)
        return enqueued

    # ── 水位 ──────────────────────────────────────────────────────────────

    async def poll_watermarks(self, db) -> int:
        """读两路水位，等级变化就投递；同一等级静默期内不重复，回到正常投恢复。"""
        readings = dict(self._readings() or {})
        if not readings:
            return 0
        if self._watermarks is None:
            stored = await db.settings_get_json(WATERMARK_STATE_KEY, {})
            self._watermarks = {
                str(signal): WatermarkState.from_payload(payload)
                for signal, payload in (stored.items() if isinstance(stored, dict) else ())
            }
        enqueued = 0
        changed = False
        for signal, reading in readings.items():
            level = str(reading.level or "").strip()
            if level not in LEVEL_LABELS:
                # 读不到（内存的 `unknown`）或信号侧冒出一个没见过的等级：本轮不动这一路
                # 的状态，也绝不把它当成「恢复正常」。
                logger.debug("跳过未知的水位等级：%s=%s", signal, level)
                continue
            state = self._watermarks.setdefault(str(signal), WatermarkState())
            if state.observed == level:
                continue  # 等级没变：静默期管的正是「同一等级不重复」
            state.observed = level
            changed = True
            if await self._notify_watermark(db, str(signal), reading, state):
                enqueued += 1
        if changed:
            await self._save_watermarks(db)
        return enqueued

    async def _notify_watermark(
        self, db, signal: str, reading: WatermarkReading, state: WatermarkState
    ) -> bool:
        """按这一次等级变化决定发不发、发什么；真落了行才记账。"""
        moment = self._now()
        recovered = reading.level in NORMAL_LEVELS
        if recovered:
            if state.notified in NORMAL_LEVELS:
                return False  # 没告过警，谈不上恢复（避免凭空一条「已恢复」）
        elif not state.silence_expired(reading.level, moment):
            logger.info(
                "水位通知在静默期内不重复：%s %s（上次 %s）",
                signal,
                reading.level,
                state.notified_at.get(reading.level),
            )
            return False

        text = build_watermark_text(reading, recovered=recovered, now=moment)
        try:
            enqueued = await self._enqueue(
                db,
                TOPIC_RESOURCE_WATERMARK,
                params={
                    "text": text,
                    "signal": reading.signal,
                    "level": reading.level,
                },
                # 业务引用带时刻：同等级在静默期之后再次告警是**新的一次**通知，不能被
                # 上一次的幂等键挡住。
                reference=f"{reading.signal}:{reading.level}:{moment.isoformat()}",
                label=f"{SIGNAL_LABELS.get(signal, signal)}水位",
            )
        except Exception as exc:
            # 水位是**状态**：这一次没入队，下一次等级变化还会再走一遍这条路，
            # 不需要为它把 30 秒一轮的循环拖进重试状态。
            logger.error("水位通知入队失败：%s", exc)
            return False
        if not enqueued:
            return False
        state.notified = LEVEL_OK if recovered else reading.level
        if not recovered:
            state.notified_at[reading.level] = moment.isoformat()
        return True

    async def _save_watermarks(self, db) -> None:
        payload = {
            signal: state.payload() for signal, state in (self._watermarks or {}).items()
        }
        await db.settings_set_json(WATERMARK_STATE_KEY, payload)

    # ── 更新 / 冷备结果 ───────────────────────────────────────────────────

    async def poll_results(self, db) -> int:
        """读更新作业 / 冷备的状态文件，终态补推一次（同一版本 / 同一次备份只推一次）。"""
        return await self._poll_update_result(db) + await self._poll_cold_backup_result(db)

    async def _poll_update_result(self, db) -> int:
        try:
            state = FileJobStateStore().read()
        except Exception as exc:  # 状态文件读不了不该带走整条调度循环
            logger.error("读取更新作业状态失败：%s", exc)
            return 0
        if state.stage not in TERMINAL_STAGES:
            return 0
        reference = update_result_reference(state)
        if not reference or not self._mark_handled(reference):
            return 0
        delivered = await self._deliver_result(
            db,
            params={
                "text": build_update_text(state, now=self._now()),
                "result": "update",
                "version": state.target_tag or "",
                "stage": state.stage,
            },
            reference=reference,
            label="系统更新结果",
        )
        return 1 if delivered else 0

    async def _poll_cold_backup_result(self, db) -> int:
        try:
            status = backup_service.read_cold_backup_status()
        except Exception as exc:
            logger.error("读取冷备状态失败：%s", exc)
            return 0
        if not status:
            return 0
        reference = cold_backup_result_reference(status)
        if not reference or not self._mark_handled(reference):
            return 0
        delivered = await self._deliver_result(
            db,
            params={
                "text": build_cold_backup_text(status, now=self._now()),
                "result": "cold_backup",
                "backup_ts": str(status.get("ts") or ""),
            },
            reference=reference,
            label="冷备结果",
        )
        return 1 if delivered else 0

    async def _deliver_result(
        self, db, *, params: Mapping[str, Any], reference: str, label: str
    ) -> bool:
        """结果类投递：入队抛异常就把「处理过」的记号撤掉，下一轮再试。

        与水位相反：结果**是一次事件**，这一轮没入队就再也没有下一次机会了（状态文件
        里躺着的一直是同一份终态），所以这里宁可下一轮重试一次。零订阅**不算**失败
        ——一条都没有收件人不是错误，反复重试只会每 30 秒记一条同样的告警。
        """
        try:
            return await self._enqueue(
                db, TOPIC_UPDATE_BACKUP, params=params, reference=reference, label=label
            )
        except Exception as exc:
            self._handled_results.discard(reference)
            logger.error("%s入队失败（下一轮重试）：%s", label, exc)
            return False

    def _mark_handled(self, reference: str) -> bool:
        """这个结果是不是本次进程里第一次见到（见 ``_handled_results``）。"""
        if reference in self._handled_results:
            return False
        self._handled_results.add(reference)
        return True

    # ── 入队 ──────────────────────────────────────────────────────────────

    async def _enqueue(
        self, db, topic_id: str, *, params: Mapping[str, Any], reference: str, label: str
    ) -> bool:
        """走统一出站入队，返回「真的落了行」。

        零订阅（一条都没入队）返回 False，调用方据此不记账 —— 出站自己会记一条零订阅
        告警，这里不重复记。入队抛的异常**向外传**：水位与结果对「要不要下一轮重试」
        的判断不同（见两个调用点），压在这一层就分不出来了。
        """
        outbox_ids = await self._outbox.enqueue_topic(
            db,
            topic_id,
            params=dict(params),
            trigger=PushTrigger.EVENT,
            business_reference=reference,
        )
        if not outbox_ids:
            logger.info("%s未入队：没有渠道订阅这一类内容", label)
            return False
        logger.info("%s已入队 %s 个目标渠道", label, len(outbox_ids))
        return True


def update_result_reference(state: UpdateJobState) -> str:
    """更新结果的业务引用：**版本 tag**（补推跨重启去重靠它）+ 终态。

    终态一起进引用是有意的：同一个 tag 重跑得出不同结果（失败 → 成功）不是同一件事，
    该再推一条；而同一份状态文件被反复读到（循环每 30 秒读一次、重启后再读）引用逐字
    相同，唯一索引会把第二行挡掉。
    """
    tag = str(state.target_tag or state.previous_tag or "").strip()
    if not tag:
        tag = str(state.finished_at or state.started_at or "unknown").strip()
    return f"update:{tag}#{state.stage}"


def _result_stamp(status: Mapping[str, Any]) -> str:
    """这一次冷备运行的身份：备份时间戳（产物目录名）→ 产物名 → 运行时刻。"""
    return str(
        status.get("ts") or status.get("archive_name") or status.get("ran_at") or ""
    ).strip()


def cold_backup_result_reference(status: Mapping[str, Any]) -> str:
    """冷备结果的业务引用：同一次备份只推一次，用备份时间戳（产物目录名）做身份。

    没有归档（失败得连产物都没生成）时退回产物名或运行时刻 —— 同样是一次运行一个值。
    """
    stamp = _result_stamp(status)
    return f"cold_backup:{stamp}" if stamp else ""


def _parse_moment(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=CHINA_TZ)


def _format_size(size_bytes: Any) -> str:
    try:
        size = float(size_bytes)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return ""


def build_update_text(state: UpdateJobState, *, now: datetime) -> str:
    """更新结果的正文明细（标题按终态定成败）。"""
    tag = str(state.target_tag or "未知版本").strip()
    if state.stage == STAGE_SUCCEEDED:
        lines = ["✅ 系统更新成功", f"版本：{tag}"]
    elif state.stage == STAGE_SUCCEEDED_BUT_UNHEALTHY:
        lines = ["⚠️ 系统更新已切换版本，但服务未恢复健康", f"版本：{tag}"]
    else:  # STAGE_FAILED（能走到这里的只有终态）
        lines = ["❌ 系统更新失败", f"版本：{tag}"]
    if state.message:
        lines.append(f"说明：{state.message}")
    if state.error:
        lines.append(f"错误：{state.error}")
    moment = _parse_moment(state.finished_at) or _parse_moment(state.started_at) or now
    lines.append(f"时间：{_format_moment(moment)}")
    return "\n".join(lines)


def build_cold_backup_text(status: Mapping[str, Any], *, now: datetime) -> str:
    """冷备结果的正文明细（成 / 败 + 产物 + 时间）。"""
    stamp = _result_stamp(status)
    if status.get("ok"):
        lines = ["✅ 冷备完成"]
    else:
        lines = ["❌ 冷备失败"]
    if stamp:
        lines.append(f"备份点：{stamp}")
    archive_name = str(status.get("archive_name") or "").strip()
    size = _format_size(status.get("size_bytes"))
    if archive_name:
        lines.append(f"归档：{archive_name}" + (f"（{size}）" if size else ""))
    if status.get("error"):
        lines.append(f"错误：{status['error']}")
    moment = _parse_moment(status.get("ran_at")) or now
    lines.append(f"时间：{_format_moment(moment)}")
    return "\n".join(lines)


# 全局单例：由企微 30 秒调度循环每轮调用（不新增常驻 task）。
ops_event_watcher = OpsEventWatcher()
