#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一出站：解析订阅 → 每目标一行投递（ADR 0094 / ADR 0095）。

所有出站（定时、卫生文字与照片、采集告警、运维事件）都走这里：

1. **订阅求解** —— 「内容类型 → 目标渠道集合」。收件人只有一条真相来源：推送订阅
   （内容类型 × 目标，目标是推送渠道或渠道群组）；
2. **入队** —— 一次投递一行落进 `wecom_push_outbox`（队列表兼发送记录）；
3. **派发** —— 由现有的 30 秒企微调度循环驱动：每渠道节流、事件退避重试、定时当天
   补发一次、保留清理。不新增常驻 task（单 worker 约束）。

正文在**发送时**渲染：行里存的是内容类型 + 参数，参数里的「今天 / 昨天」在入队时
就解析成具体营业日并冻结，补发跨过 06:00 也不会跑到另一天（ADR 0095）。

渲染器按内容类型注册（``register_renderer``）：文字类的正文由触发点算好放进参数，
图片类（验收照片）在发送时才去读采集图、专项还要拼一张前后对照图 —— 这两件事一个是
纯文本、一个要碰磁盘与图片处理，所以默认渲染器只认文本，图片那条由卫生侧注册。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Set

from config import settings
from db_core.utils import CHINA_TZ
from db_core.wecom_subscriptions_repo import SUB_OUTBOX_STATUS_PENDING
from services.wecom_push_service import (
    decrypt_webhook_url,
    expand_messages,
    resolve_report_dates,
    wecom_push_service,
)
from services.wecom_push_topics import PushTrigger, validate_params

logger = logging.getLogger(__name__)

# 出站状态值只有一处定义（`db_core/wecom_subscriptions_repo.py` 的 SUB_OUTBOX_STATUS_*）：
# 派发路径自己只用得到「待发」这一个，sending / sent / failed / skipped 都由 repo 的
# mark_* 方法设置。
STATUS_PENDING = SUB_OUTBOX_STATUS_PENDING

# 渠道停用时跳过投递的原因：订阅保留、投递跳过，记录里要写清为什么。
REASON_CHANNEL_DISABLED = "渠道已停用，本次投递跳过"

# 发送中兜底的原因（写进 last_error）：`mark_sending` 之后、写终态之前进程退出
# （崩溃 / systemd 重启 / 更新作业重启应用），这一行会永远停在 sending —— 派发只捞
# pending，于是它既不会被重发、也不会被标失败。超过阈值就按「这一次发送确实消耗了
# 一次尝试」结算，并把这句原因留给排查的人看。
REASON_SENDING_LOST = "发送中进程退出（发送结果未确认），已回到待发"
REASON_SENDING_LOST_EXHAUSTED = "发送中进程退出，重试次数已用尽"

# 一轮派发最多捞多少行：与卫生照片队列同量级，够把一轮的突发排空，又不至于让
# 一次循环长时间占着调度器。
OUTBOX_BATCH_SIZE = 20

# 节流窗口。阈值 20 条 / 分钟做成配置项（官方文档抓不到正文，不硬编数字）。
THROTTLE_WINDOW_SECONDS = 60

# 事件类：首发失败后按次数退避重试 3 次（约 1 / 5 / 15 分钟），用尽记失败。
EVENT_RETRY_BACKOFF_SECONDS = (60, 300, 900)
EVENT_MAX_ATTEMPTS = len(EVENT_RETRY_BACKOFF_SECONDS) + 1  # 首发 1 次 + 重试 3 次

# 定时类：失败在**当天晚些**补发一次（约 10 分钟），补发再失败就是终态。
SCHEDULED_RETRY_DELAY_SECONDS = 600
SCHEDULED_MAX_ATTEMPTS = 2

# 保留清理：每轮调度都跑一遍 DELETE 没必要（表小、但每次都要开写事务），
# 一个小时一次足够把保留天数落到实处。
PURGE_INTERVAL_SECONDS = 3600

# 同一投递拆成多条消息时的间隔：两条连着发容易被当成刷屏（既有 send_messages 同款）。
MESSAGE_GAP_SECONDS = 0.3


@dataclass(frozen=True)
class ResolvedTarget:
    """订阅求解出的一个目标渠道。

    ``enabled=False`` 的渠道**仍然算目标**：停用不动订阅，投递跳过并在出站记录里
    写明原因（页面上要能看出「这类内容本该发给它、但渠道停着」）。
    """

    channel_id: int
    channel_name: str
    enabled: bool = True
    skipped_reason: str = ""


@dataclass(frozen=True)
class RenderedDelivery:
    """一次投递实际要发出去的东西：一段文字**或**一张图（二选一）。

    图片存的是采集图引用，发送时才读成字节穿到这里（ADR 0095）；一条图片投递就是
    一条消息，不像文字那样按字节分块。
    """

    text: str = ""
    image_bytes: Optional[bytes] = None

    @property
    def is_image(self) -> bool:
        return self.image_bytes is not None

    @property
    def byte_length(self) -> int:
        if self.image_bytes is not None:
            return len(self.image_bytes)
        return len(self.text.encode("utf-8"))


# 渲染器：(出站行, 已解析的参数) → 这次要发的东西。事件类内容的正文由触发点算好放进
# 参数，所以默认渲染器不需要额外知识；图片类要读采集图 / 拼图，由卫生侧注册自己那条。
Renderer = Callable[[Mapping[str, Any], Mapping[str, Any]], Awaitable[RenderedDelivery]]


def _now() -> datetime:
    return datetime.now(CHINA_TZ)


def _with_previous_error(reason: str, previous: str) -> str:
    """兜底原因在前、上一条错误附在后：**保留原有错误信息**，不覆盖排查线索。"""
    return f"{reason}；上次错误：{previous}" if previous else reason


def build_idempotency_key(topic_id: str, business_reference: str, channel_id: int) -> str:
    """幂等键 = 内容类型 + 业务引用 + 目标（ADR 0095）。

    「业务引用」由触发点给：卫生文字提醒是「营业日 + 文案摘要」，照片是采集图 id。
    同一个业务事件对同一个渠道只落一行，重复触发 / 弱网重传都不会刷屏。
    """
    return f"{topic_id}:{business_reference}:{int(channel_id)}"


async def resolve_targets(db, topic_id: str) -> List[ResolvedTarget]:
    """内容类型 → 目标渠道集合（并集去重）。

    - 订阅本身停用 → 不参与求解；
    - 群组停用 → 只暂停这一组的订阅，成员自身的订阅不受影响；
    - 渠道停用 → 仍然求解出来，带跳过原因（投递时记 skipped，不是静默消失）；
    - 零订阅 → 空集合。
    """
    subscriptions = await db.wecom_subscriptions_all(topic_id=topic_id, include_disabled=False)
    if not subscriptions:
        return []

    channels = {
        int(channel["id"]): channel for channel in await db.wecom_webhooks_all()
    }
    groups = {
        int(group["id"]): group for group in await db.wecom_channel_groups_all()
    }

    channel_ids: Set[int] = set()
    for subscription in subscriptions:
        group_id = subscription.get("target_group_id")
        if group_id is None:
            channel_ids.add(int(subscription["target_channel_id"]))
            continue
        group = groups.get(int(group_id))
        if group is None or not group.get("enabled"):
            continue  # 群组停用（或已删）：这一组订阅暂停
        for member in await db.wecom_channel_group_members(int(group_id)):
            channel_ids.add(int(member["channel_id"]))

    targets: List[ResolvedTarget] = []
    for channel_id in sorted(channel_ids):
        channel = channels.get(channel_id)
        if channel is None:
            continue  # 渠道已删：订阅随外键级联走了，这里只是兜底
        enabled = bool(channel.get("enabled"))
        targets.append(ResolvedTarget(
            channel_id=channel_id,
            channel_name=str(channel.get("name") or ""),
            enabled=enabled,
            skipped_reason="" if enabled else REASON_CHANNEL_DISABLED,
        ))
    return targets


def freeze_business_date(params: Mapping[str, Any], now: datetime) -> Dict[str, Any]:
    """把参数里的「今天 / 昨天」解析成**具体营业日**并冻结（ADR 0095）。

    冻结必须发生在入队时：定时投递失败后要当天补发，补发那一刻已经过了 06:00 的话，
    再解析一次「今天」就落到下一个营业日，补发的日报成了空白的一天。冻结之后
    `date_range_mode` 从参数里消失（留着它等于留一个"以后再解析一次"的入口），
    换成渲染器直接可用的 `business_date`。
    """
    mode = params.get("date_range_mode")
    if mode is None:
        return dict(params)
    if mode not in ("today", "yesterday"):
        raise ValueError(f"未知的日期口径: {mode}")
    business_date, _ = resolve_report_dates(str(mode), now)
    frozen = {key: value for key, value in params.items() if key != "date_range_mode"}
    frozen["business_date"] = business_date
    return frozen


def default_summary(params: Mapping[str, Any]) -> str:
    """发送记录页要能一眼认出这是哪一封：取正文首行的前 200 字。"""
    text = str(params.get("text") or "").strip()
    return text.splitlines()[0][:200] if text else ""


def parse_params(row: Mapping[str, Any]) -> Dict[str, Any]:
    """出站行的参数（JSON 文本 → dict）。

    参数是一行投递的**全部内容**（正文与图片引用都在里面），所以解析不了就是这条永远
    发不出去：当场报错，由派发路径落终态并留下原因。
    """
    try:
        params = json.loads(str(row.get("params_json") or "{}"))
    except ValueError as exc:
        raise ValueError(f"参数不是合法 JSON：{exc}") from exc
    if not isinstance(params, dict):
        raise ValueError("参数必须是 JSON 对象")
    return params


def render_content(
    row: Mapping[str, Any], params: Optional[Mapping[str, Any]] = None
) -> str:
    """按出站行渲染这一次投递的正文（文本类内容的渲染口径）。

    正文在发送时渲染（ADR 0095），行里只有内容类型 + 参数。事件类内容的正文由触发点
    算好放进参数（`text`）——卫生的四类文字提醒就是这么进来的：谁在什么业务时点算出了
    什么文案，出站只负责发给订阅了这类内容的渠道。
    """
    resolved = parse_params(row) if params is None else params
    text = str(resolved.get("text") or "").strip()
    if not text:
        raise ValueError("参数里没有正文（text）")
    return text


class WeComOutbox:
    """统一出站：入队、派发、节流、重试、补发、保留清理。

    ``sender`` / ``now`` / ``gap_seconds`` 都可注入：测试用假发送器 + 假时钟驱动整套
    状态机，生产用企微发送器与墙上时钟（默认值）；图片类内容的渲染器用
    ``register_renderer`` 登记。
    """

    def __init__(
        self,
        *,
        sender: Any = None,
        now: Optional[Any] = None,
        gap_seconds: Optional[float] = None,
    ) -> None:
        self._sender = wecom_push_service if sender is None else sender
        self._now = now or _now
        self._gap_seconds = MESSAGE_GAP_SECONDS if gap_seconds is None else gap_seconds
        self._renderers: Dict[str, Renderer] = {}
        # 每渠道的滚动窗口（内存里）：重启丢掉窗口只会让节流更宽松一点，不会丢消息。
        self._recent_sends: Dict[int, List[datetime]] = {}
        self._next_purge_at: Optional[datetime] = None

    def register_renderer(self, topic_id: str, renderer: Renderer) -> None:
        """给一类内容登记渲染器（同一个 id 覆盖）。

        没登记的内容类型走默认的文本渲染（参数里的 `text`）。图片类内容必须登记：
        它的参数里只有采集图引用，没有正文。
        """
        self._renderers[str(topic_id)] = renderer

    # ── 入队 ──────────────────────────────────────────────────────────────

    async def enqueue_topic(
        self,
        db,
        topic_id: str,
        *,
        params: Mapping[str, Any],
        trigger: PushTrigger,
        business_reference: str,
        targets: Optional[Sequence[ResolvedTarget]] = None,
        schedule_id: Optional[int] = None,
        summary: str = "",
        commit: bool = True,
    ) -> List[int]:
        """一类内容 → 每个目标渠道一行出站记录，返回这些行的 id。

        - 参数按注册表校验（内容类型的 schema 是唯一入口），营业日在**这里**冻结；
        - 幂等键 = 内容类型 + 业务引用 + 目标：重复触发只落一行；
        - 停用的渠道也落一行，就地标成 skipped 并写明原因（订阅保留，投递跳过）；
        - 零订阅 → 一条不发，只记日志。

        ``commit=False`` 给「登记必须与业务事务同生共死」的调用方（验收照片）：这些行
        落进调用方的事务，由调用方决定提交还是回滚。代价之一是**不做**就地跳过标记
        （那要单独提交，会把调用方的事务切开）——停用的渠道由下一轮派发标成 skipped，
        终态与原因一样，只是晚 30 秒。
        """
        validated = validate_params(topic_id, params, trigger=trigger)
        frozen = freeze_business_date(validated, self._now())
        if not business_reference:
            raise ValueError("出站记录必须带业务引用（幂等键的一半）")

        if targets is None:
            targets = await resolve_targets(db, topic_id)
        if not targets:
            logger.warning(
                "出站零订阅：内容类型 %s 没有任何目标渠道，本次一条不发", topic_id
            )
            return []

        content_summary = summary or default_summary(frozen)
        outbox_ids: List[int] = []
        for target in targets:
            outbox_id = await db.wecom_outbox_enqueue({
                "topic_id": topic_id,
                "params_json": json.dumps(frozen, ensure_ascii=False),
                "schedule_id": schedule_id,
                "target_channel_id": target.channel_id,
                "content_summary": content_summary,
                "idempotency_key": build_idempotency_key(
                    topic_id, business_reference, target.channel_id
                ),
            }, commit=commit)
            if not outbox_id:
                logger.error(
                    "出站入队失败 topic=%s channel=%s", topic_id, target.channel_id
                )
                continue
            outbox_ids.append(int(outbox_id))
            if target.skipped_reason and commit:
                await self._skip_if_untouched(db, int(outbox_id), target.skipped_reason)
                logger.info(
                    "出站跳过（渠道停用）topic=%s channel=%s(%s)",
                    topic_id,
                    target.channel_id,
                    target.channel_name,
                )
        return outbox_ids

    # ── 派发 ──────────────────────────────────────────────────────────────

    async def dispatch_pending(self, db, *, limit: int = OUTBOX_BATCH_SIZE) -> int:
        """捞一批待发，按节流发送；返回真正发成功的条数。

        没轮到的行留在待发，由 30 秒一轮的调度循环接着发——超出节流是**排队**，
        不是丢弃。
        """
        rows = await db.wecom_outbox_pending(limit)
        sent = 0
        for row in rows:
            try:
                if await self._dispatch_row(db, row):
                    sent += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 单行异常不该带走整批（其余行还在排队）
                logger.error("出站派发异常 outbox=%s: %s", row.get("id"), exc)
        return sent

    async def _dispatch_row(self, db, row: Mapping[str, Any]) -> bool:
        row_id = int(row["id"])
        if not self._is_due(row, self._now()):
            return False  # 退避 / 补发还没到点

        channel_id = row.get("target_channel_id")
        if channel_id is None:
            await db.wecom_outbox_mark_failed(
                row_id, "目标渠道不存在（渠道已删）", attempts=self._attempts(row) + 1
            )
            return False
        channel = await db.wecom_webhook_get(int(channel_id))
        if channel is None:
            await db.wecom_outbox_mark_failed(
                row_id, f"目标渠道不存在: {channel_id}", attempts=self._attempts(row) + 1
            )
            return False
        if not channel.get("enabled"):
            await db.wecom_outbox_mark_skipped(row_id, REASON_CHANNEL_DISABLED)
            logger.info(
                "出站跳过 topic=%s channel=%s：渠道已停用",
                row.get("topic_id"),
                channel_id,
            )
            return False

        try:
            delivery = await self._render(row)
        except Exception as exc:
            # 渲染不出来的内容重试多少次都是同一个结果（参数坏的 / 图被清理了 / 装不下）：
            # 直接落终态并留下原因，不无限重试，也不静默。
            await db.wecom_outbox_mark_failed(
                row_id, f"正文渲染失败：{exc}", attempts=self._attempts(row) + 1
            )
            return False

        if delivery.is_image:
            outgoing: List[str] = []
            message_count = 1  # 一张图 = 一条消息，不按字节分块
        else:
            outgoing = expand_messages([delivery.text])
            message_count = len(outgoing)
        now = self._now()
        if not self._throttle_allow(int(channel_id), message_count, now):
            return False  # 超出本渠道这一分钟的额度：留在待发，下一轮再来
        self._throttle_consume(int(channel_id), message_count, now)

        try:
            webhook_url = decrypt_webhook_url(channel["webhook_url_encrypted"])
        except Exception as exc:
            await self._settle_failure(db, row, f"webhook 解密失败：{exc}")
            return False

        await db.wecom_outbox_mark_sending(row_id, sending_at=now.isoformat())
        if delivery.is_image:
            ok, error = await self._send_image(webhook_url, delivery.image_bytes or b"")
        else:
            ok, error = await self._send_all(webhook_url, outgoing)
        if ok:
            await db.wecom_outbox_mark_sent(
                row_id,
                delivery.byte_length,
                attempts=self._attempts(row) + 1,
            )
            return True
        await self._settle_failure(db, row, error)
        return False

    async def _render(self, row: Mapping[str, Any]) -> RenderedDelivery:
        """这次投递要发的东西：按内容类型找渲染器，没登记的走默认文本渲染。"""
        params = parse_params(row)
        renderer = self._renderers.get(str(row.get("topic_id") or ""))
        if renderer is None:
            return RenderedDelivery(text=render_content(row, params))
        return await renderer(row, params)

    async def _send_image(self, webhook_url: str, image_bytes: bytes) -> tuple[bool, str]:
        """发一张图。图片是**一条**消息（超限的图在渲染那一步就已经被降档或记失败了）。"""
        try:
            return await self._sender.send_image(webhook_url, image_bytes)
        except Exception as exc:
            return False, f"图片发送异常：{exc}"

    async def _send_all(self, webhook_url: str, outgoing: Sequence[str]) -> tuple[bool, str]:
        """同一投递的拆分段**按顺序**发送；任何一段失败就整条算失败。"""
        total = len(outgoing)
        for index, chunk in enumerate(outgoing, start=1):
            try:
                ok, response_text = await self._sender.send_text(webhook_url, chunk)
            except Exception as exc:
                return False, f"第 {index}/{total} 条发送异常：{exc}"
            if not ok:
                return False, f"第 {index}/{total} 条发送失败：{response_text}"
            if index < total and self._gap_seconds:
                await asyncio.sleep(self._gap_seconds)
        return True, "ok"

    async def _settle_failure(self, db, row: Mapping[str, Any], error: str) -> None:
        """失败落库：定下一次尝试的时间，用尽则记失败并保留最后一次错误。

        两类投递的走法不同（ADR 0095）：**事件类**按次数退避重试（约 1 / 5 / 15 分钟，
        重试 3 次），**定时类**在当天晚些补发一次（约 10 分钟）。定时类靠 `schedule_id`
        认出来——定时投递来自一条推送任务，事件投递没有任务，也不需要新加一列。
        """
        row_id = int(row["id"])
        attempts = self._attempts(row) + 1
        max_attempts = self._max_attempts(row)
        if row.get("schedule_id") is not None:
            delay: float = SCHEDULED_RETRY_DELAY_SECONDS
        else:
            backoff = EVENT_RETRY_BACKOFF_SECONDS
            delay = backoff[min(attempts - 1, len(backoff) - 1)]

        if attempts >= max_attempts:
            await db.wecom_outbox_mark_failed(row_id, error, attempts=attempts)
            logger.warning(
                "出站投递失败（尝试 %s 次用尽）outbox=%s: %s", attempts, row_id, error
            )
            return

        next_attempt_at = (self._now() + timedelta(seconds=delay)).isoformat()
        await db.wecom_outbox_mark_retry(
            row_id, error, STATUS_PENDING, attempts, scheduled_at=next_attempt_at
        )
        logger.info(
            "出站投递失败，%s 秒后重试 outbox=%s attempts=%s: %s",
            delay,
            row_id,
            attempts,
            error,
        )

    # ── 发送中兜底 ────────────────────────────────────────────────────────

    async def requeue_stale_sending(self, db, *, now: Optional[datetime] = None) -> int:
        """把卡在「发送中」的行捞回来，返回被结算的行数。

        ``mark_sending`` 之后、写终态之前进程退出（崩溃 / systemd 重启 / **更新作业重启
        应用**），这一行会永远停在 sending：派发只捞 pending，于是它既不会被重发、也不会
        被标失败，管理页面上只剩一条卡住的行。超过
        ``WECOM_OUTBOX_SENDING_TIMEOUT_SECONDS`` 还没写终态的行，就当作发送它的进程已经
        退出，按「这一次发送确实消耗了一次尝试」结算：

        - 已达上限 → 记失败，原因写明「发送中进程退出，重试次数已用尽」；
        - 未达上限 → 回到待发，attempts + 1，原有错误信息一并保留。

        阈值 0 = 关掉兜底（发送中的行永远不动）。正常在发的行不会被碰：派发与兜底由同
        一条 30 秒循环**顺序**驱动（单 worker，没有并发调用方），而在发的行从
        ``mark_sending`` 到写终态最多是「分段数 × 单条发送超时（10 秒）」，够不着默认
        的 300 秒。
        """
        timeout = self._sending_timeout_seconds()
        if timeout <= 0:
            return 0
        moment = now or self._now()
        cutoff = (moment - timedelta(seconds=timeout)).isoformat()
        rows = await db.wecom_outbox_stale_sending(cutoff, OUTBOX_BATCH_SIZE)
        reclaimed = 0
        for row in rows:
            try:
                await self._reclaim_row(db, row, moment)
                reclaimed += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 单行异常不该带走整批（其余行还卡着）
                logger.error("出站兜底异常 outbox=%s: %s", row.get("id"), exc)
        return reclaimed

    async def _reclaim_row(self, db, row: Mapping[str, Any], moment: datetime) -> None:
        """结算一行卡住的 sending：用尽的记失败，否则放回待发（并消耗一次尝试）。"""
        row_id = int(row["id"])
        attempts = self._attempts(row) + 1
        previous = str(row.get("last_error") or "").strip()
        if attempts >= self._max_attempts(row):
            reason = _with_previous_error(
                f"{REASON_SENDING_LOST_EXHAUSTED}（已尝试 {attempts} 次）", previous
            )
            await db.wecom_outbox_mark_failed(row_id, reason, attempts=attempts)
            logger.warning(
                "出站兜底：发送中进程退出且重试次数已用尽 outbox=%s attempts=%s",
                row_id,
                attempts,
            )
            return
        await db.wecom_outbox_mark_retry(
            row_id,
            _with_previous_error(REASON_SENDING_LOST, previous),
            STATUS_PENDING,
            attempts,
            scheduled_at=moment.isoformat(),
        )
        logger.warning(
            "出站兜底：发送中进程退出，已回到待发 outbox=%s attempts=%s",
            row_id,
            attempts,
        )

    @staticmethod
    def _sending_timeout_seconds() -> int:
        return int(getattr(settings, "WECOM_OUTBOX_SENDING_TIMEOUT_SECONDS", 300) or 0)

    # ── 时间与尝试次数 ────────────────────────────────────────────────────

    @staticmethod
    def _attempts(row: Mapping[str, Any]) -> int:
        return int(row.get("attempts") or 0)

    @staticmethod
    def _max_attempts(row: Mapping[str, Any]) -> int:
        """这一行的尝试上限：定时类首发 + 当天补发一次，事件类首发 + 退避重试 3 次。

        靠 ``schedule_id`` 认定时投递（它来自一条推送任务，事件投递没有任务），
        与 ``_settle_failure`` 同一把尺子 —— 两处各写一套迟早会漂。
        """
        if row.get("schedule_id") is not None:
            return SCHEDULED_MAX_ATTEMPTS
        return EVENT_MAX_ATTEMPTS

    @staticmethod
    def _is_due(row: Mapping[str, Any], now: datetime) -> bool:
        raw = row.get("scheduled_at")
        if not raw:
            return True
        try:
            due = datetime.fromisoformat(str(raw))
        except ValueError:
            return True  # 时间戳坏了不该把这条永远卡在待发
        if due.tzinfo is None:
            due = due.replace(tzinfo=CHINA_TZ)
        return due <= now

    async def _skip_if_untouched(self, db, outbox_id: int, reason: str) -> None:
        """把「还没发出去」的行就地标成跳过（重复入队时不动已终结 / 已尝试的行）。"""
        row = await db.wecom_outbox_get(outbox_id)
        if row is None or row.get("status") != STATUS_PENDING or self._attempts(row):
            return
        await db.wecom_outbox_mark_skipped(outbox_id, reason)

    # ── 保留清理 ──────────────────────────────────────────────────────────

    async def purge_expired(self, db, *, now: Optional[datetime] = None) -> int:
        """按保留天数清掉**终态**的出站行，返回删除条数。

        待发与发送中的行不管多老都不删：那是还没发出去的消息，删掉就是静默丢失。
        保留天数 0 = 永久保留（与日志保留同一个口径）。
        """
        days = int(getattr(settings, "WECOM_OUTBOX_RETENTION_DAYS", 90) or 0)
        if days <= 0:
            return 0
        cutoff = ((now or self._now()) - timedelta(days=days)).isoformat()
        removed = await db.wecom_outbox_purge_finished_before(cutoff)
        if removed:
            logger.info("出站记录清理：删除 %s 行（保留 %s 天）", removed, days)
        return int(removed or 0)

    async def purge_if_due(self, db, *, now: Optional[datetime] = None) -> int:
        """调度循环每轮都调用，但真正 DELETE 一小时最多一次。"""
        moment = now or self._now()
        if self._next_purge_at is not None and moment < self._next_purge_at:
            return 0
        self._next_purge_at = moment + timedelta(seconds=PURGE_INTERVAL_SECONDS)
        return await self.purge_expired(db, now=moment)

    # ── 每渠道节流 ────────────────────────────────────────────────────────

    def _throttle_allow(self, channel_id: int, count: int, now: datetime) -> bool:
        limit = self._rate_limit_per_minute()
        if limit <= 0:
            return True  # 0 = 关掉节流
        used = self._used_within_window(channel_id, now)
        if used == 0 and count > limit:
            # 单次投递本身就超过整分钟的额度（超长文案拆出很多段）：不能永远卡住它，
            # 放它走并吃掉这一分钟的额度。
            return True
        return used + count <= limit

    def _throttle_consume(self, channel_id: int, count: int, now: datetime) -> None:
        window = self._used_within_window_list(channel_id, now)
        window.extend([now] * max(1, int(count)))

    def _used_within_window(self, channel_id: int, now: datetime) -> int:
        return len(self._used_within_window_list(channel_id, now))

    def _used_within_window_list(self, channel_id: int, now: datetime) -> List[datetime]:
        """这一分钟窗口内已发出的条数；顺手就地丢掉过期的（保留同一个列表对象，
        否则 `consume` 会写进一个已被丢弃的列表里 —— 节流会静默失效）。"""
        window = self._recent_sends.setdefault(channel_id, [])
        cutoff = now - timedelta(seconds=THROTTLE_WINDOW_SECONDS)
        window[:] = [moment for moment in window if moment > cutoff]
        return window

    @staticmethod
    def _rate_limit_per_minute() -> int:
        return int(getattr(settings, "WECOM_OUTBOX_RATE_LIMIT_PER_MINUTE", 20) or 0)


wecom_outbox = WeComOutbox()
