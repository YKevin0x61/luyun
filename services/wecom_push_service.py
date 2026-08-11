#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业微信消息推送服务。"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import httpx
from cryptography.fernet import InvalidToken

from database import CHINA_TZ, DatabaseManager
from services.credentials_store import _fernet
from services.dish_normalize import normalize_dish_name

logger = logging.getLogger(__name__)

WECOM_TEXT_BYTE_LIMIT = 2048
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
    except (InvalidToken, UnicodeError, ValueError) as exc:
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


def resolve_report_dates(date_range_mode: str, now: Optional[datetime] = None) -> tuple[str, str]:
    current_time = now or datetime.now(CHINA_TZ)
    mode = (date_range_mode or "today").strip()
    if mode == "yesterday":
        target_date = current_time.date() - timedelta(days=1)
    else:
        target_date = current_time.date()
    date_text = target_date.isoformat()
    return date_text, date_text


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

    async def send_text(self, webhook_url: str, content: str) -> tuple[bool, str]:
        payload = {"msgtype": "text", "text": {"content": content}}
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

    async def preview_job(self, db: DatabaseManager, job_id: int) -> RenderedMessage:
        job = await db.wecom_job_get(job_id)
        if not job:
            raise ValueError("推送任务不存在")
        push_type = job.get("push_type") or SALES_REPORT_PUSH_TYPE
        if push_type == SALES_REPORT_PUSH_TYPE:
            return await self.build_sales_report_message(
                db,
                date_range_mode=job.get("date_range_mode", "today"),
                station=job.get("station", ""),
            )
        if push_type == DATA_QUALITY_PUSH_TYPE:
            return await self.build_data_quality_message(
                db,
                date_range_mode=job.get("date_range_mode", "today"),
            )
        raise ValueError("暂不支持该推送类型")

    async def send_job(self, db: DatabaseManager, job_id: int, mark_sent: bool = False) -> Dict[str, Any]:
        job = await db.wecom_job_get(job_id)
        if not job:
            raise ValueError("推送任务不存在")
        webhook = await db.wecom_webhook_get(int(job["webhook_id"]))
        if not webhook:
            raise ValueError("webhook 不存在")
        if not webhook.get("enabled"):
            raise ValueError("webhook 已停用")

        rendered_message = await self.preview_job(db, job_id)
        webhook_url = decrypt_webhook_url(webhook["webhook_url_encrypted"])
        sent_at = datetime.now(CHINA_TZ).isoformat()
        status = "success"
        response_text = ""
        error = ""
        message_parts = list(rendered_message.parts) or [rendered_message.content]
        try:
            ok, response_text = await self.send_messages(webhook_url, message_parts)
            if not ok:
                status = "failed"
                error = response_text or "企业微信返回失败"
        except Exception as exc:
            status = "failed"
            error = str(exc)

        await db.wecom_log_add({
            "job_id": job_id,
            "webhook_id": webhook["id"],
            "webhook_name": webhook.get("name", ""),
            "push_type": job.get("push_type", ""),
            "status": status,
            "message_bytes": rendered_message.byte_length,
            "error": error,
            "response_text": response_text,
            "sent_at": sent_at,
        })
        if status == "success" and mark_sent:
            await db.wecom_job_mark_sent(job_id, datetime.now(CHINA_TZ).date().isoformat())
        return {
            "success": status == "success",
            "status": status,
            "message_bytes": rendered_message.byte_length,
            "error": error,
            "response_text": response_text,
        }

    async def send_test_message(self, db: DatabaseManager, webhook_id: int) -> Dict[str, Any]:
        webhook = await db.wecom_webhook_get(webhook_id)
        if not webhook:
            raise ValueError("webhook 不存在")
        if not webhook.get("enabled"):
            raise ValueError("webhook 已停用")
        content = f"LuckIn 企业微信推送测试\n时间：{datetime.now(CHINA_TZ).strftime('%Y-%m-%d %H:%M:%S')}"
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
        await db.wecom_log_add({
            "job_id": None,
            "webhook_id": webhook["id"],
            "webhook_name": webhook.get("name", ""),
            "push_type": "test",
            "status": status,
            "message_bytes": rendered_message.byte_length,
            "error": error,
            "response_text": response_text,
            "sent_at": datetime.now(CHINA_TZ).isoformat(),
        })
        return {
            "success": status == "success",
            "status": status,
            "message_bytes": rendered_message.byte_length,
            "error": error,
            "response_text": response_text,
        }

    async def dispatch_due_jobs(self, db: DatabaseManager, now: Optional[datetime] = None) -> int:
        current_time = now or datetime.now(CHINA_TZ)
        current_date = current_time.date().isoformat()
        current_minute = current_time.strftime("%H:%M")
        jobs = await db.wecom_jobs_all(include_disabled=False)
        dispatched_count = 0
        for job in jobs:
            if job.get("schedule_time") != current_minute:
                continue
            if job.get("last_sent_date") == current_date:
                continue
            try:
                result = await self.send_job(db, int(job["id"]), mark_sent=True)
                if result.get("success"):
                    dispatched_count += 1
            except Exception as exc:
                logger.error("企微定时推送任务失败 job_id=%s: %s", job.get("id"), exc)
        return dispatched_count

    async def scheduler_loop(self, db: DatabaseManager) -> None:
        logger.info("企业微信推送调度器已启动")
        while True:
            try:
                await self.dispatch_due_jobs(db)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("企业微信推送调度器异常: %s", exc)
            await asyncio.sleep(SCHEDULER_INTERVAL_SECONDS)


wecom_push_service = WeComPushService()
