#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业微信消息推送服务。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlparse

import httpx
from cryptography.fernet import InvalidToken

from database import CHINA_TZ, DatabaseManager
from services.business_day import business_date_of, previous_business_date
from services.credentials_store import _fernet
from services.dish_normalize import normalize_dish_name
from services.scheduling.week_view import cell_caption, cell_state
from services.wecom_push_topics import (
    TOPIC_DAILY_OPS_REPORT,
    TOPIC_RECONCILE_DIFF,
    TOPIC_SALES_REPORT,
    TOPIC_TEST_MESSAGE,
    TOPIC_WEEKLY_SCHEDULE,
    WEEKDAY_LABELS,
    WEEKDAY_SHORT_LABELS,
    PushTrigger,
    get_topic,
    topic_display_name,
)
from services.wecom_push_slots import (
    ScheduleSlot,
    dump_last_sent,
    last_sent_map,
    record_last_sent,
    slot_matches_day,
    slots_from_row,
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
# 漏发宽限：失败补发是 +10 分钟，一过点就报「漏发」会闪出一条几分钟后自己消失的告警。
MISSED_GRACE_MINUTES = 30
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
    """推送时间必须是 HH:MM —— 与触发点列表共用 `wecom_push_slots.normalize_time` 一条规则。

    接口层（`api/wecom_push.py` 的 `JobIn`）还留着这个历史名字；列表里的每一行也走
    同一个函数，两边分家的话「单点任务能存、多点任务报错」会很莫名其妙。
    """
    from services.wecom_push_slots import normalize_time

    return normalize_time(schedule_time)


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



def daily_ops_business_date(now: Optional[datetime] = None) -> str:
    """运营日报的营业日：按触发时间推断（ADR 0144）。

    22:15 触发 = 当天（店里打烊、对账已出）；店长把时间改到早上 8 点 = 上一个营业日
    （昨天的日报，而不是一张空的今天）。口径与 ``auto_business_date_mode`` 同一处，
    差的是这里直接给出日期字符串，供预览 / 立即发送使用 —— 定时投递那条路在入队时
    冻结（``freeze_business_date``），两者必须是同一个算法。
    """
    from services.wecom_push_topics import auto_business_date_mode

    moment = now or datetime.now(CHINA_TZ)
    business_date, _ = resolve_report_dates(auto_business_date_mode(moment), moment)
    return business_date

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
    # 与页面同口径：订单数是账单数（total_bills），规则覆盖写 87/109（80%）（审查 R16）。
    bills = summary.get("total_bills", summary.get("total_orders", 0))
    unique_dishes = summary.get("unique_dishes", 0) or 0
    covered = summary.get("covered_rules", 0) or 0
    coverage = round(covered / unique_dishes * 100) if unique_dishes else 0
    summary_line = (
        f"订单数：{bills}  "
        f"菜件总数：{summary.get('total_dishes', 0)}  "
        f"菜品数：{unique_dishes}  "
        f"规则覆盖：{covered}/{unique_dishes}（{coverage}%）"
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


def next_week_start(business_date: str) -> str:
    """「下一周」的第一天：``business_date`` 所在周的**下一个周一**（ISO 日期）。

    周日推 → 明天（周一）；周三推 → 下周一。只依赖营业日、不读钟：定时投递与失败
    补发因此落在同一周（`WeeklyScheduleRenderer` 用出站行的 `created_at` 冻结出这个
    营业日，见那里的注释）。
    """
    anchor = date.fromisoformat(business_date)
    return (anchor - timedelta(days=anchor.weekday()) + timedelta(days=7)).isoformat()


def week_start_of(day: str) -> str:
    """把任意一天归一到它所在周的周一（手动推送的 week_start 口径）。

    页面上翻到的是某一周，按钮只传那一周里的一天也行；接口与渲染器都按周一归一，
    同一周怎么传都是同一张图、同一个幂等键前缀。
    """
    anchor = date.fromisoformat(str(day or "").strip())
    return (anchor - timedelta(days=anchor.weekday())).isoformat()


def weekly_schedule_title(week_start: str, *, today: Optional[date] = None) -> str:
    """手动推送的图标题：按这一周与今天的关系说话。

    本周 / 下周不写日期（群里天天看「11/17 – 11/23 排班」会累）；更早或更晚的周写
    日期范围 —— 这张图可能用来对上周的班、也可能提前发节假日的班，光说「排班」没人
    知道是哪一周。日期范围本来就在标题条右侧，这里只给一句人话。
    """
    start = date.fromisoformat(week_start_of(week_start))
    anchor = today or datetime.now(CHINA_TZ).date()
    this_monday = anchor - timedelta(days=anchor.weekday())
    if start == this_monday:
        return "本周排班"
    if start == this_monday + timedelta(days=7):
        return "下周排班"
    end = start + timedelta(days=6)
    return f"{start.month}/{start.day} – {end.month}/{end.day} 排班"


def _schedule_cell_text(cell: Mapping[str, Any]) -> str:
    """周表一格在消息里的写法（班次 / 休 / 请假 / 未排）。

    判定在 `services/scheduling/week_view.py`（图片推送读同一份，两边不会对同一格
    各说各话），这里只负责文字版的样子。
    """
    return cell_caption(cell_state(cell))


def _format_person_schedule(
    days: List[Mapping[str, Any]], cells: Mapping[str, Any]
) -> str:
    """一个人的那一行：连续相同的日子合并成一段（``一~六 早班·熟笼 · 日 休``）。

    为什么要合并：真实门店里绝大多数人是固定班（``早班·熟笼`` 一周七天都一样），逐日
    铺开会变成同一句话重复七遍 —— 20 人的店一条就能发完的表要拆成两条，群里还得往下
    翻。合并只动**连续相同**的日子，某天休 / 请假 / 半天照样单独成段，信息一个不丢；
    七天完全相同时写「每天」，那是这种排班最常见的形态。
    """
    ordered = [
        _schedule_cell_text(cells.get(str(day.get("business_date") or "")) or {})
        for day in days
    ]
    if not ordered:
        return ""
    labels = [
        WEEKDAY_SHORT_LABELS.get(int(day.get("weekday", 0)), "")
        for day in days
    ]

    segments: List[tuple] = []  # (起始下标, 结束下标, 那一段的写法)
    for index, text in enumerate(ordered):
        if segments and segments[-1][2] == text and segments[-1][1] == index - 1:
            start, _end, _text = segments[-1]
            segments[-1] = (start, index, text)
        else:
            segments.append((index, index, text))

    if len(segments) == 1:
        return f"每天 {segments[0][2]}"

    parts = []
    for start, end, text in segments:
        if start == end:
            label = labels[start]
        else:
            label = f"{labels[start]}~{labels[end]}"
        parts.append(f"{label} {text}")
    return " · ".join(parts)


def render_weekly_schedule_text(grid: Mapping[str, Any]) -> RenderedMessage:
    """下一周排班表正文：一行一人，7 天按周一→周日排。

    一行一人而不是一天一段：群里每个人要查的是「我下周哪几天上班」，按人扫一眼就到；
    按天分段还得在 7 条消息里找自己的名字。一行一行也正好是 `expand_messages` 的拆分
    单位 —— 超过 2048 字节时按整行切，不会把某个人切成两半。

    数据全来自 `SchedulingStore.week_grid`（与页面周表同一份判据），这里只负责排版。
    """
    start = str(grid.get("start") or "")
    end = str(grid.get("end") or "")
    start_day = date.fromisoformat(start) if start else None
    end_day = date.fromisoformat(end) if end else None

    def short(day: Optional[date]) -> str:
        return f"{day.month}/{day.day}" if day else ""

    title = "【下周排班】"
    if start_day and end_day:
        title += (
            f"{short(start_day)}（{WEEKDAY_LABELS[start_day.weekday()]}）"
            f" - {short(end_day)}（{WEEKDAY_LABELS[end_day.weekday()]}）"
        )

    days = list(grid.get("days") or [])

    lines: List[str] = [title, ""]
    employees = list(grid.get("employees") or [])
    for employee in employees:
        name = str(employee.get("name") or "").strip()
        if not name:
            continue
        cells = employee.get("cells") or {}
        lines.append(f"{name}：" + _format_person_schedule(days, cells))

    if not employees:
        lines.append("这一周还没有排班（排班表里一个人都没有）。")

    content = "\n".join(lines).rstrip()
    return RenderedMessage(content=content, byte_length=len(content.encode("utf-8")))


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
        *,
        business_date: str = "",
    ) -> RenderedMessage:
        """销售报表正文（``business_date`` = 出站行里冻结的营业日，见 ADR 0095）。

        给了具体营业日就直接用它算，不再解析「今天 / 昨天」：定时类投递失败会在当天晚些
        补发一次，补发那一刻已经过了 06:00 的话，再解析一次就会把日报算成空白的一天。
        """
        if business_date:
            start_date, end_date = business_date, business_date
        else:
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
        business_date: str = "",
    ) -> RenderedMessage:
        """数据质量日报正文（``business_date`` 口径同 `build_sales_report_message`）。"""
        from services.data_quality_alerts import build_data_quality_job_message

        content = await build_data_quality_job_message(
            db, date_range_mode=date_range_mode, business_date=business_date
        )
        return RenderedMessage(content=content, byte_length=len(content.encode("utf-8")))

    async def _weekly_grid(
        self,
        db: DatabaseManager,
        *,
        business_date: str = "",
        now: Optional[datetime] = None,
        week_start: str = "",
    ) -> Dict[str, Any]:
        """投递那一刻的「下一周」周表（文字版与图片版共用这一份数据）。

        `business_date` 是投递那一刻的营业日；不给就取现在。进门先补未来 90 天
        （`expand`，与周表接口一致）：没人打开过月历的那一周本来可能还没有行，不补的话
        推出去是一张「未排」表。补排失败只记一条警告、继续用已有行 —— 渲染阶段抛异常
        会让这条投递直接落终态（出站对渲染失败不重试）。
        """
        from services.scheduling.store import SchedulingStore

        base = business_date or business_date_of(now or datetime.now(CHINA_TZ))
        store = SchedulingStore(db)
        try:
            await store.expand()
        except Exception as exc:  # noqa: BLE001 —— 补排失败不该让整条推送发不出去
            logger.warning("下周排班推送前补排失败（继续用已有行）：%s", exc)
        start = week_start_of(week_start) if week_start else next_week_start(base)
        return await store.week_grid(start=start)

    async def build_weekly_schedule_message(
        self,
        db: DatabaseManager,
        *,
        business_date: str = "",
        now: Optional[datetime] = None,
        week_start: str = "",
    ) -> RenderedMessage:
        """「下一周」排班表的**文字**版（字体缺失时的降级、以及老的调用方）。

        群机器人正常收到的是图片版（见 `build_weekly_schedule_image`）：文字版在
        20 人的店也要一百多行，群里要往下翻。
        """
        grid = await self._weekly_grid(
            db, business_date=business_date, now=now, week_start=week_start
        )
        return render_weekly_schedule_text(grid)

    async def build_weekly_schedule_image(
        self,
        db: DatabaseManager,
        *,
        business_date: str = "",
        now: Optional[datetime] = None,
        week_start: str = "",
        title: str = "",
        pushed_by: str = "",
    ) -> Optional[bytes]:
        """「下一周」排班表的**图片**版；本机没有中文字体时返回 ``None``（调用方退文字）。

        画图是纯 CPU：实测 19 人约 0.18 秒、40 人约 0.35 秒（本机），一天一次可以接受，
        但它会占住事件循环，所以不放在调度循环的主路径上白画 —— 只有真到发送那一步才会
        调到这里。
        """
        from services.wecom_schedule_image import render_weekly_schedule_png

        grid = await self._weekly_grid(
            db, business_date=business_date, now=now, week_start=week_start
        )
        return render_weekly_schedule_png(grid, title=title, pushed_by=pushed_by)

    async def _daily_ops_report(
        self,
        db: DatabaseManager,
        *,
        business_date: str = "",
        modules: Optional[Sequence[str]] = None,
        now: Optional[datetime] = None,
    ):
        """运营日报的数据（预览 / 立即发送 / 出站渲染共用这一份）。"""
        from services.wecom_daily_ops import collect_report

        return await collect_report(
            db,
            business_date=business_date or daily_ops_business_date(now),
            modules=modules,
        )

    async def build_daily_ops_message(
        self,
        db: DatabaseManager,
        *,
        business_date: str = "",
        modules: Optional[Sequence[str]] = None,
        now: Optional[datetime] = None,
    ) -> RenderedMessage:
        """运营日报的**文字**版（出图失败时的降级、以及排查用）。"""
        from services.wecom_daily_ops import render_text

        report = await self._daily_ops_report(
            db, business_date=business_date, modules=modules, now=now
        )
        content = render_text(report)
        return RenderedMessage(content=content, byte_length=len(content.encode("utf-8")))

    async def build_daily_ops_image(
        self,
        db: DatabaseManager,
        *,
        business_date: str = "",
        modules: Optional[Sequence[str]] = None,
        now: Optional[datetime] = None,
    ) -> Optional[bytes]:
        """运营日报的**图片**版；本机没有中文字体时返回 ``None``（调用方退文字）。"""
        from services.wecom_daily_ops import render_png

        report = await self._daily_ops_report(
            db, business_date=business_date, modules=modules, now=now
        )
        return render_png(report, modules=modules)

    async def render_job_message(
        self, db: DatabaseManager, job: Mapping[str, Any], *, now: Optional[datetime] = None
    ) -> RenderedMessage:
        """按任务的**内容类型**渲染正文（定时侧）。

        报表类照旧现算（数据要去库里取）；其余内容类型的正文由触发点放进参数的 `text`
        里（参数即正文）。所以注册表**新加一类内容类型时这里不用改** —— 只有当它的正文
        需要现算数据时，才在这里多一条分支。下周排班表就是这一类（现算，数据来自排班）。
        """
        topic_id = str(job.get("topic_id") or "")
        params = job_params(job)
        if topic_id == TOPIC_SALES_REPORT:
            # 营业日按**这次的触发时刻**推断（ADR 0144）：一条任务可能 11:30 与 21:30
            # 各推一次，早上那次推上一营业日、晚上那次推当天，任务级固定成「昨天」会让
            # 早上那次拿到一份还没打完烊的报表。
            return await self.build_sales_report_message(
                db,
                business_date=daily_ops_business_date(now),
                station=str(params.get("station") or ""),
            )
        if topic_id == TOPIC_RECONCILE_DIFF:
            return await self.build_data_quality_message(
                db,
                business_date=daily_ops_business_date(now),
            )
        if topic_id == TOPIC_WEEKLY_SCHEDULE:
            # 预览 / 立即发送用**此刻**的营业日：「下一周」对现在这一刻有意义。
            return await self.build_weekly_schedule_message(db, now=now)
        if topic_id == TOPIC_DAILY_OPS_REPORT:
            return await self.build_daily_ops_message(
                db, modules=params.get("modules"), now=now
            )
        text = str(params.get("text") or "").strip()
        if text:
            return RenderedMessage(content=text, byte_length=len(text.encode("utf-8")))
        raise ValueError(
            f"「{topic_display_name(topic_id)}」的正文由触发点产出，这里没有可预览的内容"
        )

    async def render_job_delivery(
        self, db: DatabaseManager, job: Mapping[str, Any], *, now: Optional[datetime] = None
    ) -> "RenderedDelivery":
        """按任务渲染**这次要发出去的东西**（文字或图片，二选一）。

        预览、立即发送、定时投递都从这里取：三处必须拿到同一份东西，否则「预览看到文字、
        发出去的却是图」这种错位没法在页面上被看出来。

        下周排班表优先出图（用户要的就是截图）。出图失败（本机没有中文字体、图超 2MB、
        画图抛异常）都**退回文字** —— 宁可推一条不那么好看的文字，也不要当天一条都不发。
        """
        from services.wecom_outbox import RenderedDelivery

        topic_id = str(job.get("topic_id") or "")
        if topic_id == TOPIC_WEEKLY_SCHEDULE:
            try:
                image = await self.build_weekly_schedule_image(db, now=now)
            except Exception as exc:  # noqa: BLE001 —— 出图失败退文字，不让推送整条落空
                logger.warning("下周排班表出图失败，改发文字版：%s", exc)
                image = None
            if image:
                return RenderedDelivery(image_bytes=image)
            logger.warning("下周排班表没有可用的图片，改发文字版（中文字体缺失或图太大）")
        if topic_id == TOPIC_DAILY_OPS_REPORT:
            modules = job_params(job).get("modules")
            try:
                image = await self.build_daily_ops_image(db, modules=modules, now=now)
            except Exception as exc:  # noqa: BLE001 —— 出图失败退文字
                logger.warning("运营日报出图失败，改发文字版：%s", exc)
                image = None
            if image:
                return RenderedDelivery(image_bytes=image)
            logger.warning("运营日报没有可用的图片，改发文字版（中文字体缺失或图太大）")
            message = await self.build_daily_ops_message(db, modules=modules, now=now)
            return RenderedDelivery(text=message.content)
        message = await self.render_job_message(db, job, now=now)
        return RenderedDelivery(text=message.content)

    async def preview_job(self, db: DatabaseManager, job_id: int) -> RenderedMessage:
        job = await db.wecom_job_get(job_id)
        if not job:
            raise ValueError("推送任务不存在")
        return await self.render_job_message(db, job)

    async def preview_job_delivery(
        self, db: DatabaseManager, job_id: int
    ) -> "RenderedDelivery":
        """预览：与真发同一份 delivery（下周排班表在这里就是那张图）。"""
        job = await db.wecom_job_get(job_id)
        if not job:
            raise ValueError("推送任务不存在")
        return await self.render_job_delivery(db, job)

    async def enqueue_job(
        self,
        db: DatabaseManager,
        job: Mapping[str, Any],
        *,
        business_reference: str,
        targets: Optional[List[Any]] = None,
        now: Optional[datetime] = None,
        slot_time: str = "",
    ) -> List[int]:
        """把一条推送任务按它的内容类型入队，收件人来自订阅（票 08）。

        入队而不是直接发送：节流、重试、发送记录都由统一出站管（ADR 0095）。出站行带
        `schedule_id` = 任务 id，定时类「失败当天补发一次」的状态机就挂在这上面。

        ``now`` 传给入队那一步去冻结营业日：调度循环算 `last_sent_date` 与冻结参数用的
        必须是同一个时刻（假时钟才穿得过去）。``slot_time`` 是这次命中的触发点时刻，只
        用来在发送记录里区分「同一条任务的 11:30 与 21:30 两封」。
        """
        # 延迟导入：出站服务要用本模块的发送器与拆分函数，模块级互相 import 会成环。
        from services.wecom_outbox import wecom_outbox

        topic_id = str(job.get("topic_id") or "")
        topic = get_topic(topic_id)
        if topic is None:
            raise ValueError(f"未知的推送内容类型: {topic_id}")
        job_name = str(job.get("name") or "")
        summary_text = f"{job_name} · {slot_time}" if slot_time else job_name
        return await wecom_outbox.enqueue_topic(
            db,
            topic_id,
            params=job_params(job),
            trigger=PushTrigger.SCHEDULED,
            business_reference=business_reference,
            targets=targets,
            schedule_id=int(job["id"]),
            # 发送记录页悬停能认出这是哪一封：报表类的正文在发送时才渲染，入队时拿不到
            # 首行，用「任务名 · 触发时刻」做摘要（同一任务多个触发点时两者缺一不可）。
            summary=summary_text,
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

        moment = now or datetime.now(CHINA_TZ)
        # 与定时投递走同一个 delivery：预览看到的（图或文字）就是这一次要发的东西。
        delivery = await self.render_job_delivery(db, job, now=moment)
        targets = await resolve_targets(db, topic_id)
        if not targets:
            # 静默成功会让店长以为发出去了：零订阅必须当场说清（用户故事 9）。
            raise ValueError(
                f"「{topic.name}」还没有订阅任何渠道，一条都发不出去："
                "请先在「订阅」里勾选收件群"
            )

        # 手工发送每次都是新的一次外发（连点两次就是两封），所以引用里带时刻；定时投递
        # 用的是「营业日#任务#触发点」，两者不会撞在同一个幂等键上。任务 id 也进引用：
        # 两条任务同时点「立即发送」是两次真实外发，不该被幂等键吞掉一条。
        outbox_ids = await self.enqueue_job(
            db,
            job,
            business_reference=f"{business_date_of(moment)}#manual#{job_id}#{moment.isoformat()}",
            targets=targets,
            now=moment,
        )
        return {
            "success": True,
            "status": "queued",
            "queued": len(outbox_ids),
            "target_count": len(targets),
            "kind": "image" if delivery.is_image else "text",
            "message_bytes": delivery.byte_length,
            # 图片一条就是一封，不像文字要按 2048 字节拆条。
            "chunk_count": 1 if delivery.is_image else len(expand_messages([delivery.text])),
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
        """到点就把这条任务**按订阅入队**，但每个营业日的每个触发点只入队一次。

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
        business_day = date.fromisoformat(current_date)
        jobs = await db.wecom_jobs_all(include_disabled=False)
        dispatched_count = 0
        for job in jobs:
            due_slots = self._due_slots(job, business_day, current_minute, business_date=current_date)
            if not due_slots:
                continue
            # 同一分钟命中多个点（「每天 09:00」+「周一 09:00」）只入队一次：同一营业日
            # 同一时刻推两遍一模一样的内容没有意义，而触发点列表本来允许这样叠。
            slot = due_slots[0]
            try:
                outbox_ids = await self.enqueue_job(
                    db,
                    job,
                    business_reference=f"{current_date}#{job.get('id')}#{slot.time}",
                    slot_time=slot.time,
                    now=current_time,
                )
                if not outbox_ids:
                    logger.warning(
                        "定时推送零订阅：任务 %s（内容类型 %s）没有任何目标渠道，本次一条不发",
                        job.get("id"),
                        job.get("topic_id"),
                    )
                    continue
                sent_map = record_last_sent(
                    last_sent_map(job), current_date, slot.time, current_time
                )
                await db.wecom_job_update(
                    int(job["id"]),
                    {
                        "last_sent_date": current_date,
                        "last_sent_json": dump_last_sent(sent_map),
                    },
                )
                dispatched_count += 1
            except Exception as exc:
                logger.error("企微定时推送任务失败 job_id=%s: %s", job.get("id"), exc)
        return dispatched_count

    @staticmethod
    def _slot_moment(business_date: str, slot_time: str) -> Optional[datetime]:
        """营业日里某个触发点对应的**真实时刻**（06:00 切日的换算）。

        营业日 D 从 D 06:00 起算，所以 00:00–05:59 的点其实落在 D+1 凌晨；漏发判定
        必须跟真实时刻比，否则半夜那条永远显示「还没到」。
        """
        try:
            day = date.fromisoformat(business_date)
            hour = int(slot_time[:2])
            minute = int(slot_time[3:])
        except (TypeError, ValueError):
            return None
        moment = datetime.combine(day, datetime.min.time(), tzinfo=CHINA_TZ)
        moment += timedelta(hours=hour, minutes=minute)
        if hour < 6:
            moment += timedelta(days=1)
        return moment

    @staticmethod
    def _slot_applies(slot: "ScheduleSlot", topic: Optional[Any]) -> bool:
        """这个触发点对这个内容类型有没有意义。

        `weekday=None` 的意思是「每天」，非周任务正是这样；但**周任务**的点没有星期
        （迁移前的坏数据、手改了库）按「每天」发，会让每周一张的排班图变成每天一张，
        所以跳过 —— 与旧版 `_weekly_due` 遇到坏 weekday 时一样保守。保存接口也会拒绝
        这种写法，这里只是兜底。
        """
        if topic is not None and topic.weekly and slot.weekday is None:
            logger.debug("周任务的触发点 %s 没有星期，跳过", slot.time)
            return False
        return True

    @classmethod
    def _due_slots(
        cls,
        job: Mapping[str, Any],
        business_day: date,
        current_minute: str,
        *,
        business_date: str,
    ) -> List["ScheduleSlot"]:
        """这一分钟该触发的点：时刻相同、星期对得上、这个营业日还没发过。"""
        slots = slots_from_row(job)
        if not slots:
            return []
        topic = get_topic(str(job.get("topic_id") or ""))
        sent_today = last_sent_map(job).get(business_date, {})
        if not sent_today and str(job.get("last_sent_date") or "") == business_date:
            # 0063 之前的行只记了日期、没记时刻（迁移回填给的是空串键）。这一天既然发过，
            # 就不该在第一个到点的位置上再推一遍。
            return []
        return [
            slot
            for slot in slots
            if slot.time == current_minute
            and cls._slot_applies(slot, topic)
            and slot_matches_day(slot, business_day)
            and slot.time not in sent_today
        ]

    @classmethod
    def today_schedule(
        cls, job: Mapping[str, Any], now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """这条任务在**当前营业日**每个触发点的状态（只读，页面用）。

        `missed` 只列「过点 30 分钟仍未发出」的点：失败的定时投递会在 +10 分钟补发一次，
        一过点就报漏发，店长会看到一枚几分钟后自己消失的假告警。

        尺子与调度循环一致：营业日来自 `business_date_of`（06:00 切），星期匹配走
        `slot_matches_day`，已发记录读 `last_sent_json`（旧行回退 `last_sent_date`）。
        """
        moment = now or datetime.now(CHINA_TZ)
        business_date = business_date_of(moment)
        slots = slots_from_row(job)
        enabled = bool(job.get("enabled"))
        day = date.fromisoformat(business_date)
        sent = last_sent_map(job).get(business_date, {})
        flagged_today = str(job.get("last_sent_date") or "") == business_date
        entries: List[Dict[str, Any]] = []
        next_time = ""
        topic = get_topic(str(job.get("topic_id") or ""))
        for slot in slots:
            due_at = cls._slot_moment(business_date, slot.time)
            if not enabled or not cls._slot_applies(slot, topic) or not slot_matches_day(slot, day):
                state = "off"
            elif slot.time in sent or flagged_today:
                state = "sent"
            elif due_at is not None and moment >= due_at + timedelta(minutes=MISSED_GRACE_MINUTES):
                state = "missed"
            else:
                state = "pending"
                if due_at is not None and due_at > moment and not next_time:
                    next_time = slot.time
            entries.append({"time": slot.time, "weekday": slot.weekday, "state": state})
        states = [entry["state"] for entry in entries]
        if not enabled or not entries or all(state == "off" for state in states):
            overall = "off"
        elif "missed" in states:
            overall = "missed"
        elif all(state in ("sent", "off") for state in states):
            overall = "sent"
        else:
            overall = "pending"
        return {
            "state": overall,
            "business_date": business_date,
            "slots": entries,
            "missed": [entry["time"] for entry in entries if entry["state"] == "missed"],
            "next_time": next_time,
        }

    @classmethod
    def today_state(cls, job: Mapping[str, Any], now: Optional[datetime] = None) -> str:
        """这条任务**今天这一次**到点了没发 —— 只读判断，不参与任何投递逻辑。

        取值：
        - `sent`    —— 今天（营业日）该发的点都发过了；
        - `pending` —— 今天还有没到点的点；
        - `missed`  —— 有点过点 30 分钟仍未发出；
        - `off`     —— 任务停用，或按星期规则今天本来就不该发。

        页面只显示一枚徽章，所以这里只给汇总值；哪个点漏了看 `today_schedule`。
        """
        return cls.today_schedule(job, now=now)["state"]

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


class ScheduledReportRenderer:
    """报表类内容在统一出站上的渲染器（销售报表 / 对账差异告警的**定时侧**）。

    报表类的出站行里只有冻结的营业日 + 档口，正文要发送时现算数据 —— 所以这两类内容
    **必须**在出站上登记渲染器（`main.py` 的 lifespan 里登记）。没登记就会落到默认文本
    渲染，而参数里没有 `text`，于是每一条日报都以「正文渲染失败」落终态且不重试：
    2026-10-11 22:10:06 的「每日销售报表 → 沟通群」（0 字节 / 尝试 1 次 / failed）
    就是这么没发出去的。

    对账差异告警是**双触发**内容类型：事件侧（日终对账差异即时告警）的正文已经算好放在
    参数里，这里原样返回，不能被报表渲染接管。
    """

    def __init__(self, db: DatabaseManager, service: Optional[WeComPushService] = None) -> None:
        self._db = db
        self._service = service or wecom_push_service

    async def render(
        self, row: Mapping[str, Any], params: Mapping[str, Any]
    ) -> "RenderedDelivery":
        # 延迟导入：出站服务模块级 import 了本模块的发送器与拆分函数，成环。
        from services.wecom_outbox import RenderedDelivery

        text = str(params.get("text") or "").strip()
        if text:
            return RenderedDelivery(text=text)

        topic_id = str(row.get("topic_id") or "")
        if topic_id not in (TOPIC_SALES_REPORT, TOPIC_RECONCILE_DIFF):
            raise ValueError(
                f"内容类型 {topic_id} 没有登记渲染器：正文既不在参数里，也没有现算的口径"
            )
        business_date = str(params.get("business_date") or "").strip()
        if not business_date:
            raise ValueError(f"内容类型 {topic_id} 的出站行里没有冻结的营业日（business_date）")

        if topic_id == TOPIC_SALES_REPORT:
            message = await self._service.build_sales_report_message(
                self._db,
                business_date=business_date,
                station=str(params.get("station") or ""),
            )
        else:
            message = await self._service.build_data_quality_message(
                self._db, business_date=business_date
            )
        return RenderedDelivery(text=message.content)


class WeeklyScheduleRenderer:
    """「下周排班表」在统一出站上的渲染器（正文发送时现算，出站行里没有正文）。

    用出站行的 ``created_at``（入队时刻）还原**营业日**，再由它算「下一周」：定时投递
    失败后当天晚些会补发一次（出站对定时类固定 +10 分钟），补发那一刻如果已经跨过
    06:00，再读一次钟就会把「下周」算成再下一周 —— 冻结在入队时刻才与 ADR 0095 的
    口径一致（销售报表的 `business_date` 参数冻结是同一件事的另一种写法）。
    """

    def __init__(self, db: DatabaseManager, service: Optional[WeComPushService] = None) -> None:
        self._db = db
        self._service = service or wecom_push_service

    async def render(
        self, row: Mapping[str, Any], params: Mapping[str, Any]
    ) -> "RenderedDelivery":
        from services.wecom_outbox import RenderedDelivery

        created = str(row.get("created_at") or "").strip()
        try:
            moment = datetime.fromisoformat(created) if created else datetime.now(CHINA_TZ)
        except ValueError:
            moment = datetime.now(CHINA_TZ)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=CHINA_TZ)
        manual_week_start = str(params.get("week_start") or "").strip()
        if manual_week_start:
            # 手动推送（周表页的按钮）：人指定了周次，画的就是那一周。这一发点的是
            # 「发图」，出图失败不能退文字 —— 一段上百行的文字甩进群里既不是他要的，
            # 也看不出是哪一周；让这一行落 failed，发送记录里写明原因。
            title = str(params.get("title") or "") or weekly_schedule_title(manual_week_start)
            image = await self._service.build_weekly_schedule_image(
                self._db,
                week_start=manual_week_start,
                title=title,
                pushed_by=str(params.get("operator_name") or ""),
            )
            if not image:
                raise ValueError("服务器缺中文字体，无法生成排班图")
            return RenderedDelivery(image_bytes=image)
        # 定时任务：优先发图（用户要的就是截图）；出图失败退文字，别让当天一条都不发。
        try:
            image = await self._service.build_weekly_schedule_image(
                self._db, business_date=business_date_of(moment)
            )
        except Exception as exc:  # noqa: BLE001 —— 出图失败退文字
            logger.warning("下周排班表出图失败，改发文字版：%s", exc)
            image = None
        if image:
            return RenderedDelivery(image_bytes=image)
        message = await self._service.build_weekly_schedule_message(
            self._db, business_date=business_date_of(moment)
        )
        return RenderedDelivery(text=message.content)
