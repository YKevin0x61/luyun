#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业微信消息推送服务。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import parse_qs, urlparse

import httpx
from cryptography.fernet import InvalidToken

from database import CHINA_TZ, DatabaseManager
from services.business_day import business_date_of, previous_business_date
from services.credentials_store import _fernet
from services.dish_normalize import normalize_dish_name
from services.wecom_push_topics import (
    TOPIC_RECONCILE_DIFF,
    TOPIC_SALES_REPORT,
    TOPIC_TEST_MESSAGE,
    PushTrigger,
    get_topic,
    topic_display_name,
)

logger = logging.getLogger(__name__)

WECOM_TEXT_BYTE_LIMIT = 2048
# 群机器人 image 消息：base64 编码**前**的图片不能超过 2MB（官方口径）。
WECOM_IMAGE_BYTE_LIMIT = 2 * 1024 * 1024
WECOM_WEBHOOK_HOST = "qyapi.weixin.qq.com"
SALES_REPORT_PUSH_TYPE = "sales_report_text"
DATA_QUALITY_PUSH_TYPE = "data_quality_alert"
ALLOWED_PUSH_TYPES = frozenset({SALES_REPORT_PUSH_TYPE, DATA_QUALITY_PUSH_TYPE})
SCHEDULER_INTERVAL_SECONDS = 30
SEND_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class RenderedMessage:
    content: str
    byte_length: int
    limit: int = WECOM_TEXT_BYTE_LIMIT
    parts: tuple = ()


def encrypt_webhook_url(webhook_url: str) -> str:
    return _fernet().encrypt(webhook_url.encode("utf-8")).decode("ascii")


def decrypt_webhook_url(encrypted_url: str) -> str:
    try:
        return _fernet().decrypt(encrypted_url.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("webhook 解密失败") from exc
    except UnicodeError as exc:
        raise ValueError("webhook 解密失败") from exc


def validate_webhook_url(webhook_url: str) -> str:
    normalized_url = (webhook_url or "").strip()
    parsed_url = urlparse(normalized_url)
    query_values = parse_qs(parsed_url.query)
    key_value = (query_values.get("key") or [""])[0].strip()
    if (
        parsed_url.scheme != "https"
        or parsed_url.netloc != WECOM_WEBHOOK_HOST
        or parsed_url.path != "/cgi-bin/webhook/send"
        or not key_value
    ):
        raise ValueError("请输入有效的企业微信机器人 webhook 地址")
    return normalized_url


def mask_webhook_url(webhook_url: str) -> str:
    parsed_url = urlparse(webhook_url)
    query_values = parse_qs(parsed_url.query)
    key_value = (query_values.get("key") or [""])[0]
    if len(key_value) <= 8:
        masked_key = "*" * len(key_value)
    else:
        masked_key = f"{key_value[:4]}...{key_value[-4:]}"
    return f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}?key={masked_key}"


def validate_schedule_time(schedule_time: str) -> str:
    normalized_time = (schedule_time or "").strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", normalized_time):
        raise ValueError("推送时间格式必须是 HH:MM")
    return normalized_time


def job_params(job: Mapping[str, Any]) -> Dict[str, Any]:
    """推送任务的参数（`params_json` → dict）。

    解析不了就当空参数：页面与预览不该因为库里一行坏数据整页打不开（入队时注册表会
    按 schema 报出缺哪个字段，那才是该看见的错误）。
    """
    try:
        params = json.loads(str(job.get("params_json") or "{}"))
    except ValueError:
        return {}
    return dict(params) if isinstance(params, dict) else {}


def resolve_report_dates(date_range_mode: str, now: Optional[datetime] = None) -> tuple[str, str]:
    """把「今天 / 昨天」翻成报表区间端点（两段都是**营业日**）。

    门店的「今天」是 06:00 切日的营业日，与采集 / 对账 / 卫生同一口径（CORR-05）。
    原先这里按日历日切：`resolve_report_dates("today", 01:00)` 会给出**当天**日期，
    而采集侧那一刻还在写前一营业日的单 —— 两边对不上。

    返回的是同一个日期两次（`start == end`）：区间端点由调用方翻成时间戳，`db_core.reports`
    会把它展开成 `[当日 06:00, 次日 06:00)` 的半开区间。保留 ``(start, end)`` 的元组形状是
    因为既有调用方按两段解包。
    """
    current_time = now or datetime.now(CHINA_TZ)
    business_date = business_date_of(current_time)
    mode = (date_range_mode or "today").strip()
    if mode == "yesterday":
        business_date = previous_business_date(business_date)
    return business_date, business_date


def _format_qty(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or 0)
    if abs(number - int(number)) < 1e-9:
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _build_semi_usage_lines(semi_finished: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for position_index, block in enumerate(semi_finished or []):
        position = block.get("position") or "未分类"
        items = block.get("items") or []
        if not items:
            continue
        is_last_position = position_index == len(semi_finished) - 1
        position_prefix = "└─" if is_last_position else "├─"
        lines.append(f"{position_prefix} {position}")
        item_indent = "   " if is_last_position else "│  "
        for item_index, semi_item in enumerate(items):
            is_last_item = item_index == len(items) - 1
            item_prefix = "└─" if is_last_item else "├─"
            semi_name = semi_item.get("semi_name") or ""
            qty = _format_qty(semi_item.get("qty", 0))
            unit = semi_item.get("unit") or ""
            lines.append(f" {item_indent}{item_prefix} {semi_name} × {qty}{unit}")
    return lines


def render_sales_report_text(report_data: Dict[str, Any], fixed_dishes: Optional[List[Dict[str, Any]]] = None) -> RenderedMessage:
    summary = report_data.get("summary") or {}
    date_range = report_data.get("date_range") or {}
    start_date = date_range.get("start") or ""
    end_date = date_range.get("end") or start_date
    report_date_text = start_date if start_date == end_date else f"{start_date} ~ {end_date}"

    title_line = f"【销售报表】{report_date_text}"
    summary_line = (
        f"订单数：{summary.get('total_orders', 0)}  "
        f"菜件总数：{summary.get('total_dishes', 0)}  "
        f"菜品数：{summary.get('unique_dishes', 0)}  "
        f"规则覆盖：{summary.get('covered_rules', 0)}"
    )

    dish_lines = ["【菜品销量】"]
    dish_sales = report_data.get("dish_sales") or []
    fixed_dish_items = fixed_dishes or []

    # 订单菜名带 (-)、(普通)、(外卖)(1只) 等前后缀，按归一化名汇总以正确匹配并合并跨档口销量。
    normalized_qty: Dict[str, int] = {}
    for item in dish_sales:
        normalized_key = normalize_dish_name(item.get("dish_name", ""))
        normalized_qty[normalized_key] = normalized_qty.get(normalized_key, 0) + int(item.get("qty") or 0)

    if fixed_dish_items:
        fixed_total_qty = 0
        for index, fixed_dish in enumerate(fixed_dish_items, start=1):
            dish_name = fixed_dish.get("dish_name") or ""
            qty = int(normalized_qty.get(normalize_dish_name(dish_name), 0))
            fixed_total_qty += qty
            dish_lines.append(f"{index}. {dish_name} {qty}份")
        dish_lines.append(f"总计 {fixed_total_qty}份")
    else:
        display_order: List[str] = []
        aggregated_qty: Dict[str, int] = {}
        for item in dish_sales:
            name = item.get("dish_name", "")
            if name not in aggregated_qty:
                display_order.append(name)
            aggregated_qty[name] = aggregated_qty.get(name, 0) + int(item.get("qty") or 0)
        for index, name in enumerate(display_order, start=1):
            dish_lines.append(f"{index}. {name} {aggregated_qty[name]}份")
        if not display_order:
            dish_lines.append("暂无销售数据")

    semi_finished = report_data.get("semi_finished") or []
    semi_usage_lines = _build_semi_usage_lines(semi_finished)
    semi_lines = ["【半成品用量】", *semi_usage_lines] if semi_usage_lines else []

    # 两个逻辑板块：菜品销量 / 半成品用量，发送时各成一条消息。
    dish_part = "\n".join([title_line, summary_line, "", *dish_lines])
    parts = [dish_part]
    if semi_lines:
        semi_part = "\n".join([f"{title_line}（半成品用量）", *semi_lines])
        parts.append(semi_part)

    content = "\n\n".join(parts)
    return RenderedMessage(content=content, byte_length=len(content.encode("utf-8")), parts=parts)


def assert_message_size(rendered_message: RenderedMessage) -> None:
    if rendered_message.byte_length > rendered_message.limit:
        raise ValueError(
            f"消息内容 {rendered_message.byte_length} 字节，超过企业微信 text 限制 {rendered_message.limit} 字节"
        )


def _hard_split_line(line: str, limit: int) -> List[str]:
    """把单行超长文本按字节切成多段，避免拆断 UTF-8 多字节字符。"""
    chunks: List[str] = []
    current = ""
    for char in line:
        candidate = current + char
        if len(candidate.encode("utf-8")) > limit:
            if current:
                chunks.append(current)
            current = char
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def expand_messages(parts: List[str], limit: int = WECOM_TEXT_BYTE_LIMIT) -> List[str]:
    """把逻辑板块展开为实际发送的消息列表，超限板块再按行细分。"""
    outgoing: List[str] = []
    for part in parts:
        if len(part.encode("utf-8")) > limit:
            outgoing.extend(split_text_for_wecom(part, limit))
        else:
            outgoing.append(part)
    return outgoing or [""]


def split_text_for_wecom(content: str, limit: int = WECOM_TEXT_BYTE_LIMIT) -> List[str]:
    """按行把内容拆成多条 <= limit 字节的文本块，尽量保持行完整。"""
    chunks: List[str] = []
    current_lines: List[str] = []

    def current_bytes(extra_line: str) -> int:
        candidate = current_lines + [extra_line]
        return len("\n".join(candidate).encode("utf-8"))

    for line in content.split("\n"):
        if len(line.encode("utf-8")) > limit:
            if current_lines:
                chunks.append("\n".join(current_lines))
                current_lines = []
            chunks.extend(_hard_split_line(line, limit))
            continue
        if current_lines and current_bytes(line) > limit:
            chunks.append("\n".join(current_lines))
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        chunks.append("\n".join(current_lines))
    return chunks or [""]


class WeComPushService:
    async def build_sales_report_message(
        self,
        db: DatabaseManager,
        date_range_mode: str = "today",
        station: str = "",
    ) -> RenderedMessage:
        start_date, end_date = resolve_report_dates(date_range_mode)
        report_data = await db.reports.compute_sales_report(start_date, end_date, station or None)
        fixed_dishes = await db.report_dishes_all()
        return render_sales_report_text(report_data, fixed_dishes)

    async def _post_payload(self, webhook_url: str, payload: Dict[str, Any]) -> tuple[bool, str]:
        """POST 一条消息给群机器人，按 errcode 判成功失败。"""
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT_SECONDS) as client:
            response = await client.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        response_text = response.text[:1000]
        if response.status_code != 200:
            return False, response_text
        try:
            data = response.json()
        except ValueError:
            return False, response_text
        errcode = int(data.get("errcode", -1))
        errmsg = str(data.get("errmsg", response_text))
        return errcode == 0, errmsg

    async def send_text(self, webhook_url: str, content: str) -> tuple[bool, str]:
        return await self._post_payload(
            webhook_url, {"msgtype": "text", "text": {"content": content}}
        )

    async def send_image(self, webhook_url: str, image_bytes: bytes) -> tuple[bool, str]:
        """发一张图。

        群机器人的 `image` 消息只吃 **base64 + md5**，不接受 URL（要 URL 的是图文卡片的
        `picurl`，那还得是公网可访问的 https）。卫生的照片端点全要会话鉴权，公网本来也
        取不到 —— 所以只能服务端读文件后走这条路。
        """
        if not image_bytes:
            return False, "图片内容为空"
        if len(image_bytes) > WECOM_IMAGE_BYTE_LIMIT:
            return False, (
                f"图片 {len(image_bytes)} 字节，超过企业微信 image 限制 "
                f"{WECOM_IMAGE_BYTE_LIMIT} 字节"
            )
        payload = {
            "msgtype": "image",
            "image": {
                "base64": base64.b64encode(image_bytes).decode("ascii"),
                "md5": hashlib.md5(image_bytes).hexdigest(),
            },
        }
        return await self._post_payload(webhook_url, payload)

    async def send_messages(self, webhook_url: str, parts: List[str]) -> tuple[bool, str]:
        """按逻辑板块顺序发送；单个板块若超限再按行细分为多条。"""
        outgoing = expand_messages(parts)
        total = len(outgoing)
        for index, chunk in enumerate(outgoing, start=1):
            ok, response_text = await self.send_text(webhook_url, chunk)
            if not ok:
                return False, f"第 {index}/{total} 条发送失败：{response_text}"
            if index < total:
                await asyncio.sleep(0.3)
        return True, "ok"

    async def send_text_chunks(self, webhook_url: str, content: str) -> tuple[bool, str]:
        """长文本按企业微信 text 限制拆分为多条顺序发送。"""
        return await self.send_messages(webhook_url, [content])

    async def build_data_quality_message(
        self,
        db: DatabaseManager,
        *,
        date_range_mode: str = "today",
    ) -> RenderedMessage:
        from services.data_quality_alerts import build_data_quality_job_message

        content = await build_data_quality_job_message(db, date_range_mode=date_range_mode)
        return RenderedMessage(content=content, byte_length=len(content.encode("utf-8")))

    async def render_job_message(self, db: DatabaseManager, job: Mapping[str, Any]) -> RenderedMessage:
        """按任务的**内容类型**渲染正文（定时侧）。

        报表类照旧现算（数据要去库里取）；其余内容类型的正文由触发点放进参数的 `text`
        里（参数即正文）。所以注册表**新加一类内容类型时这里不用改** —— 只有当它的正文
        需要现算数据时，才在这里多一条分支。
        """
        topic_id = str(job.get("topic_id") or "")
        params = job_params(job)
        if topic_id == TOPIC_SALES_REPORT:
            return await self.build_sales_report_message(
                db,
                date_range_mode=str(params.get("date_range_mode") or "today"),
                station=str(params.get("station") or ""),
            )
        if topic_id == TOPIC_RECONCILE_DIFF:
            return await self.build_data_quality_message(
                db,
                date_range_mode=str(params.get("date_range_mode") or "today"),
            )
        text = str(params.get("text") or "").strip()
        if text:
            return RenderedMessage(content=text, byte_length=len(text.encode("utf-8")))
        raise ValueError(
            f"「{topic_display_name(topic_id)}」的正文由触发点产出，这里没有可预览的内容"
        )

    async def preview_job(self, db: DatabaseManager, job_id: int) -> RenderedMessage:
        job = await db.wecom_job_get(job_id)
        if not job:
            raise ValueError("推送任务不存在")
        return await self.render_job_message(db, job)

    async def enqueue_job(
        self,
        db: DatabaseManager,
        job: Mapping[str, Any],
        *,
        business_reference: str,
        targets: Optional[List[Any]] = None,
        now: Optional[datetime] = None,
    ) -> List[int]:
        """把一条推送任务按它的内容类型入队，收件人来自订阅（票 08）。

        入队而不是直接发送：节流、重试、发送记录都由统一出站管（ADR 0095）。出站行带
        `schedule_id` = 任务 id，定时类「失败当天补发一次」的状态机就挂在这上面。

        ``now`` 传给入队那一步去冻结营业日：调度循环算 `last_sent_date` 与冻结参数用的
        必须是同一个时刻（假时钟才穿得过去）。
        """
        # 延迟导入：出站服务要用本模块的发送器与拆分函数，模块级互相 import 会成环。
        from services.wecom_outbox import wecom_outbox

        topic_id = str(job.get("topic_id") or "")
        topic = get_topic(topic_id)
        if topic is None:
            raise ValueError(f"未知的推送内容类型: {topic_id}")
        return await wecom_outbox.enqueue_topic(
            db,
            topic_id,
            params=job_params(job),
            trigger=PushTrigger.SCHEDULED,
            business_reference=business_reference,
            targets=targets,
            schedule_id=int(job["id"]),
            # 发送记录页悬停能认出这是哪一封：报表类的正文在发送时才渲染，入队时拿不到
            # 首行，用任务名做摘要。
            summary=str(job.get("name") or ""),
            now=now,
        )

    async def send_job_now(
        self, db: DatabaseManager, job_id: int, now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """立即发送：按这个内容类型的**全部订阅目标**投递（票 08）。

        正文先渲染一次 —— 页面拿到的字节数与拆条数就是这次要发出去的东西；真正的发送
        交给统一出站（每渠道节流、失败退避重试、结果落发送记录）。
        """
        from services.wecom_outbox import resolve_targets

        job = await db.wecom_job_get(job_id)
        if not job:
            raise ValueError("推送任务不存在")
        topic_id = str(job.get("topic_id") or "")
        topic = get_topic(topic_id)
        if topic is None:
            raise ValueError(f"未知的推送内容类型: {topic_id}")

        rendered_message = await self.render_job_message(db, job)
        targets = await resolve_targets(db, topic_id)
        if not targets:
            # 静默成功会让店长以为发出去了：零订阅必须当场说清（用户故事 9）。
            raise ValueError(
                f"「{topic.name}」还没有订阅任何渠道，一条都发不出去："
                "请先在「订阅」里勾选收件群"
            )

        moment = now or datetime.now(CHINA_TZ)
        # 手工发送每次都是新的一次外发（连点两次就是两封），所以引用里带时刻；定时投递
        # 用的是营业日，两者不会撞在同一个幂等键上。
        outbox_ids = await self.enqueue_job(
            db,
            job,
            business_reference=f"{business_date_of(moment)}#manual#{moment.isoformat()}",
            targets=targets,
            now=moment,
        )
        parts = list(rendered_message.parts) or [rendered_message.content]
        return {
            "success": True,
            "status": "queued",
            "queued": len(outbox_ids),
            "target_count": len(targets),
            "message_bytes": rendered_message.byte_length,
            "chunk_count": len(expand_messages(parts)),
        }

    async def send_test_message(self, db: DatabaseManager, webhook_id: int) -> Dict[str, Any]:
        """给某个渠道发一条测试消息，并把结果登记进发送记录。

        测试发送**同步**发（点一下就要看到成败），所以不排队等调度循环；结果作为一条
        终态的出站行落库 —— 否则「测试发送」的效果在发送记录里看不到（票 07 的遗留）。
        """
        from services.wecom_outbox import wecom_outbox

        webhook = await db.wecom_webhook_get(webhook_id)
        if not webhook:
            raise ValueError("webhook 不存在")
        if not webhook.get("enabled"):
            raise ValueError("webhook 已停用")
        content = f"厨务管家 企业微信推送测试\n时间：{datetime.now(CHINA_TZ).strftime('%Y-%m-%d %H:%M:%S')}"
        rendered_message = RenderedMessage(content=content, byte_length=len(content.encode("utf-8")))
        assert_message_size(rendered_message)
        webhook_url = decrypt_webhook_url(webhook["webhook_url_encrypted"])
        status = "success"
        response_text = ""
        error = ""
        try:
            ok, response_text = await self.send_text(webhook_url, content)
            if not ok:
                status = "failed"
                error = response_text or "企业微信返回失败"
        except Exception as exc:
            status = "failed"
            error = str(exc)
        await wecom_outbox.record_direct_delivery(
            db,
            TOPIC_TEST_MESSAGE,
            params={"text": content},
            channel_id=int(webhook["id"]),
            status="sent" if status == "success" else "failed",
            message_bytes=rendered_message.byte_length,
            error=error,
        )
        return {
            "success": status == "success",
            "status": status,
            "message_bytes": rendered_message.byte_length,
            "error": error,
            "response_text": response_text,
        }

    async def dispatch_due_jobs(self, db: DatabaseManager, now: Optional[datetime] = None) -> int:
        """到点就把这条任务**按订阅入队**，但每个营业日只入队一次。

        收件人来自订阅（票 08）：任务只描述「内容类型 + 参数 + 时间」，一次投递一个目标
        一行出站记录，真正发出去由统一出站的状态机负责（节流、失败当天补发都在那儿）。

        `last_sent_date` 存的是**营业日**（06:00 切），不是日历日（CORR-05）：定时任务
        的 `schedule_time` 可以落在 06:00 之后（正常营业时段），也可以落在 00:00–06:00
        ——后者按日历日排重会在同一场营业里推两次（跨零点前后各一次）。与
        `resolve_report_dates` 用同一把尺子，报表内容与排重键才不会错位。

        「入队成功」才算今天发过（零订阅不写 `last_sent_date`）：一条都没入队时店长补上
        订阅，当天到点还能补发一次。
        """
        current_time = now or datetime.now(CHINA_TZ)
        current_date = business_date_of(current_time)
        current_minute = current_time.strftime("%H:%M")
        jobs = await db.wecom_jobs_all(include_disabled=False)
        dispatched_count = 0
        for job in jobs:
            if job.get("schedule_time") != current_minute:
                continue
            if job.get("last_sent_date") == current_date:
                continue
            try:
                outbox_ids = await self.enqueue_job(
                    db, job, business_reference=current_date, now=current_time
                )
                if not outbox_ids:
                    logger.warning(
                        "定时推送零订阅：任务 %s（内容类型 %s）没有任何目标渠道，本次一条不发",
                        job.get("id"),
                        job.get("topic_id"),
                    )
                    continue
                await db.wecom_job_mark_sent(int(job["id"]), current_date)
                dispatched_count += 1
            except Exception as exc:
                logger.error("企微定时推送任务失败 job_id=%s: %s", job.get("id"), exc)
        return dispatched_count

    async def scheduler_loop(self, db: DatabaseManager) -> None:
        """30 秒一轮：定时任务到点就推 + 运维事件 + 统一出站的兜底、派发与保留清理。

        统一出站（`services/wecom_outbox.py`）的兜底（卡在发送中的行）、重试、补发、
        节流都挂在这条既有循环上，**不新增常驻 task**（单 worker 约束，常驻 task 清单
        有测试钉着）。兜底放在派发前：捞回来的行在同一轮就能重新排队发出。

        运维事件（`services/wecom_ops_events.py`：磁盘 / 内存水位、更新与冷备结果）同样
        挂在这里，且排在派发之前 —— 本轮入队的行当轮就能发出去。水位是状态、结果是
        状态文件，都不需要自己的循环。
        """
        # 延迟导入：出站服务要用本模块的发送器与拆分函数，模块级互相 import 会成环。
        from services.wecom_outbox import wecom_outbox
        from services.wecom_ops_events import ops_event_watcher

        logger.info("企业微信推送调度器已启动")
        while True:
            try:
                await self.dispatch_due_jobs(db)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("企业微信推送调度器异常: %s", exc)
            try:
                await ops_event_watcher.poll_once(db)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("企微运维事件轮询异常: %s", exc)
            try:
                await wecom_outbox.requeue_stale_sending(db)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("企微出站兜底异常: %s", exc)
            try:
                await wecom_outbox.dispatch_pending(db)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("企微出站派发异常: %s", exc)
            try:
                await wecom_outbox.purge_if_due(db)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("企微出站清理异常: %s", exc)
            await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


wecom_push_service = WeComPushService()
