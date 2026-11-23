#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采购今日汇总 → 商鹏云小票打印：编排「读配置 → 排版 → 绑定 → 推送」。

和 spyun.py 的分工：那边只管协议（签名、错误码、四个接口），这边管业务顺序
与失败语义。三条失败要分开报，因为它们对应三种不同的处理方式：

- not_configured / disabled —— 店里还没配，去设置页填，重试无用；
- empty —— 今天没有采购单，不是错误，不该弹红框吓人；
- bind_failed / print_failed / status_failed —— 配置对但设备侧有问题，可以重试。

绑定只在第一次需要：平台要求先 /printer/add 才能 /printer/print。绑成功后把
bound_at 写回配置，之后每张票只发一次 print，省一次往返，也让「重试=重打」
的确定性保持住（print 本身没有幂等键）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional

from database import CHINA_TZ
from services.staff_meal.store import StaffMealError, StaffMealStore
from services.printer.config import PrinterConfig, load_config, mark_bound
from services.printer.spyun import SpyunClient, SpyunError
from services.printer.ticket import (
    DEFAULT_FONT_SCALE,
    SCALE_TAGS,
    Ticket,
    build_ticket,
    normalize_scale,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PrinterError",
    "build_test_ticket",
    "device_status",
    "next_day_staff_meal",
    "preview_ticket",
    "print_summary",
    "send_test_ticket",
]


class PrinterError(Exception):
    """打印链路上的业务失败。code 给接口层翻 HTTP 状态用。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _client(config: PrinterConfig) -> SpyunClient:
    return SpyunClient(config.appid, config.appsecret)


def _now_text() -> str:
    return datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _summary_of(ticket: Ticket, config: PrinterConfig) -> dict:
    return {
        "characters": len(ticket.text),
        "bytes": ticket.byte_length,
        "lines": len(ticket.lines),
        "printed_suppliers": ticket.printed_suppliers,
        "printed_products": ticket.printed_products,
        "total_suppliers": ticket.total_suppliers,
        "total_products": ticket.total_products,
        "truncated": ticket.truncated,
        "paper_width": config.paper_width,
        "times": config.times,
        "font_scale": ticket.scale,
        "staff_meal_date": ticket.staff_meal_date,
    }


async def _ensure_bound(db, config: PrinterConfig, client: SpyunClient) -> PrinterConfig:
    """首次打印前把设备绑到 appid 下。已绑过就直接返回。"""
    if config.bound_at:
        return config
    try:
        result = await client.add_printer(config.sn, config.pkey)
    except SpyunError as exc:
        logger.warning("绑定商鹏云打印机失败 sn=%s：%s", config.sn, exc)
        raise PrinterError(
            "bind_failed",
            "绑定打印机失败：" + str(exc) + "（核对 SN / PKEY 与开发者账号是否一致）",
        ) from exc
    logger.info(
        "商鹏云打印机已绑定 sn=%s already_added=%s",
        config.sn,
        bool(result.get("already_added")),
    )
    return await mark_bound(db, config, _now_text())


WEEKDAY_TEXT = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _next_day(summary: Mapping[str, Any]) -> Optional[str]:
    """票面采购日期的第二天。日期不合法就不附员工餐，别让一张票整个失败。"""
    day = str(summary.get("date") or "").strip()
    try:
        return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    except ValueError:
        return None


async def next_day_staff_meal(db, summary: Mapping[str, Any]) -> Optional[dict]:
    """次日的员工餐**采购清单**：提交人 + 他在员工餐页填的采购条目。

    票上附它的理由：采购汇总在早上打，员工餐的货要提前一天备；两者同一张纸上
    拣货的人才不会漏。内容和企微那条「【员工餐】采购计划」是同一份数据
    （summary_rows 按品名+单位合并），没有条目就返回 None —— 空标题只占纸。
    """
    target = _next_day(summary)
    if not target:
        return None
    store = StaffMealStore(db)
    try:
        order = await store.order_by_date(target)
        items = await store.summary_rows(int(order["id"])) if order else []
    except StaffMealError as exc:
        # 员工餐迁移还没应用 / 表不存在：小票照打，只是少这一段。
        logger.info("次日员工餐清单读不到 %s：%s", target, exc)
        return None
    rows = [
        {
            "name": str(row.get("item_name") or "").strip(),
            "quantity": row.get("quantity"),
            "unit": str(row.get("unit") or "").strip(),
        }
        for row in items
        if str(row.get("item_name") or "").strip()
    ]
    if not rows:
        return None
    date_value = datetime.strptime(target, "%Y-%m-%d")
    return {
        "date": target,
        "weekday": WEEKDAY_TEXT[date_value.weekday()],
        "submitter": str((order or {}).get("submitter_name") or "").strip(),
        "items": rows,
    }


def preview_ticket(
    summary: Mapping[str, Any],
    config: Optional[PrinterConfig] = None,
    staff_meal: Optional[Mapping[str, Any]] = None,
) -> dict:
    """只排版不发送——设置页里「看看长什么样」用。"""
    effective = config or PrinterConfig()
    ticket = build_ticket(
        summary,
        paper_width=effective.paper_width,
        line_break=effective.line_break,
        scale=effective.font_scale,
        staff_meal=staff_meal,
    )
    return {"text": ticket.text, **_summary_of(ticket, effective)}


async def print_summary(db, summary: Mapping[str, Any]) -> dict:
    """把今天的汇总推给打印机。返回平台订单号与排版统计。"""
    config = await load_config(db)
    if not config.configured:
        raise PrinterError("not_configured", "还没配置商鹏云打印机，请先点本页的「打印机」填写")
    if not config.enabled:
        raise PrinterError("disabled", "小票打印没开启，请点本页「打印机」勾上「开启小票打印」")

    staff_meal = await next_day_staff_meal(db, summary)
    ticket = build_ticket(
        summary,
        paper_width=config.paper_width,
        line_break=config.line_break,
        scale=config.font_scale,
        staff_meal=staff_meal,
    )
    # 今天没采购单也不算空：次日员工餐要买的东西一样值得打一张。
    if not ticket.printed_products and not ticket.staff_meal_date:
        raise PrinterError(
            "empty",
            str(summary.get("date") or "") + " 没有采购单，次日员工餐也没有登记要买的货",
        )

    client = _client(config)
    config = await _ensure_bound(db, config, client)

    try:
        payload = await client.print_text(config.sn, ticket.text, times=config.times)
    except SpyunError as exc:
        logger.warning("商鹏云打印失败 sn=%s：%s", config.sn, exc)
        raise PrinterError(
            "print_failed",
            "推送打印失败：" + str(exc) + "（设备可能离线，联网后云端会自动补打）",
        ) from exc

    order_id = payload.get("id")
    logger.info(
        "采购汇总小票已推送 id=%s sn=%s 行=%d 字节=%d 供应商=%d 商品=%d 截断=%s",
        order_id,
        config.sn,
        len(ticket.lines),
        ticket.byte_length,
        ticket.printed_suppliers,
        ticket.printed_products,
        ticket.truncated,
    )
    return {
        "ok": True,
        "order_id": order_id,
        "create_time": payload.get("create_time"),
        "sn": config.sn,
        **_summary_of(ticket, config),
    }


async def device_status(db) -> dict:
    """查设备在线状态与排队张数。配置不全时不联网，直接告诉前端缺什么。"""
    config = await load_config(db)
    if not config.configured:
        raise PrinterError("not_configured", "还没配置商鹏云打印机")
    client = _client(config)
    try:
        payload = await client.printer_info(config.sn)
    except SpyunError as exc:
        raise PrinterError("status_failed", "查询设备状态失败：" + str(exc)) from exc
    online = payload.get("online")
    return {
        "sn": config.sn,
        "name": payload.get("name"),
        "online": str(online).strip() == "1" or online is True,
        "status": payload.get("status"),
        "queued": payload.get("sqsnum"),
        "model_type": payload.get("model_type"),
        "model_name": payload.get("model_name"),
    }


TEST_TICKET_LINES = [
    "【测试小票】",
    "========================",
    "厨务管家 · 采购汇总打印",
    "设备连接正常",
    "========================",
    "打印时间：{{time}}",
]


def build_test_ticket(
    now: Optional[str] = None,
    *,
    line_break: str = "br",
    cut: bool = False,
    scale: Any = DEFAULT_FONT_SCALE,
) -> str:
    """设置页的测试票。字号跟正式票一致，不然「试出来」的和真打的不一样。"""
    token = "\n" if line_break == "lf" else "<BR>"
    lines = [line.replace("{{time}}", now or _now_text()) for line in TEST_TICKET_LINES]
    body = token.join(lines)
    tag = SCALE_TAGS[normalize_scale(scale)]
    if tag:
        body = "<%s>%s</%s>" % (tag, body, tag)
    if cut:
        body += token + "<CUT>"
    return body


async def send_test_ticket(db) -> dict:
    """设置页的「打测试票」。宁可先在这里撞出网络/签名问题。"""
    config = await load_config(db)
    if not config.configured:
        raise PrinterError("not_configured", "还没配置商鹏云打印机")
    client = _client(config)
    config = await _ensure_bound(db, config, client)
    try:
        payload = await client.print_text(
            config.sn,
            build_test_ticket(
                line_break=config.line_break,
                cut=config.paper_width >= 80,
                scale=config.font_scale,
            ),
            times=1,
        )
    except SpyunError as exc:
        raise PrinterError("print_failed", "测试小票发送失败：" + str(exc)) from exc
    return {"ok": True, "order_id": payload.get("id"), "sn": config.sn}
