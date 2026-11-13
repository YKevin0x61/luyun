#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工餐采购单的推送（ADR 0135）。

一条采购单提交 / 改后重推时，渲染成与现网 OpenClaw 同款的文本，走企业微信推送的
事件型通道入队（`services/wecom_outbox.py` 的 `enqueue_topic`）：

    【员工餐】采购计划 2026-11-13
    提交人：余威威
    备注：鸡要现杀的
    清远鸡 3只
    沙姜 0.5斤
    ...

幂等键 = `staff_meal_purchase:{使用日}:{push_seq}`：同一张单的同一版只落一行出站记录，
页面连点两次「提交」不会发两条（`push_seq` 由 service 在提交时 +1，重推才递增）。
零订阅时 `enqueue_topic` 返回空列表 —— 这不是成功，调用方要把单据标成 failed 并说明
去订阅页勾上这一项。

推送状态不靠出队回调：读单时按当前的 `push_seq` 查出站行，映射成
`scheduled / pushing / pushed / failed`（见 `latest_push_status`）。这样不引入第二套
状态机，出站表仍是投递事实的唯一来源。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Iterable, Optional

from db_core.utils import CHINA_TZ
from services.wecom_outbox import PushTrigger, wecom_outbox
from services.wecom_push_topics import TOPIC_STAFF_MEAL_PURCHASE

logger = logging.getLogger(__name__)

#: 出站业务引用的前缀（幂等键的第二段）。改格式要同时改读取侧 `latest_push_status`。
PURCHASE_REFERENCE_PREFIX = TOPIC_STAFF_MEAL_PURCHASE


def build_business_reference(order_date: str, push_seq: int) -> str:
    return f"{PURCHASE_REFERENCE_PREFIX}:{order_date}:{int(push_seq)}"


def format_quantity(value: Optional[float]) -> str:
    """数量的人话写法：9 → 「9」、0.50 → 「0.5」、None/0 → 空。"""
    if value is None:
        return ""
    number = float(value)
    if number == int(number):
        return str(int(number))
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def render_purchase_text(
    order_date: str,
    rows: Iterable[dict],
    *,
    updated: bool = False,
    submitter_name: str = "",
    remark: str = "",
) -> str:
    """把汇总行渲染成企微文本（标题 + 提交人 + 备注 + 每行「食材 数量单位」）。

    提交人一行是 2026-11-11 用户新要求（m01140）：这一域对全员开放，群里得能看出
    是谁提交的；空名字（历史单 / 管理端）就不写这一行。

    备注是 2026-11-13 用户裁定（S4 方案 A）：单据备注作为「提交人」之后、清单之前
    单独一行进群消息 —— 页面上写的是「给采购的人留一句话」，那它就必须真的出现在
    采购员能看到的地方。换行折成空格（企微一条文本里换行会被当成两条消息拆开），
    空备注不渲染。

    `rows` 是 `StaffMealStore.summary_rows` 的输出（已按食材 + 单位排序）；数量为空
    的行只写食材名 —— 这是「同名菜只带出食材名、数量留空」的推送形态。
    """
    title = f"【员工餐】采购计划 {order_date}"
    if updated:
        title += "（更新）"
    lines = [title]
    who = str(submitter_name or "").strip()
    if who:
        lines.append(f"提交人：{who}")
    note = str(remark or "").strip()
    if note:
        note_text = re.sub(r"\r\n|\r|\n", " ", note)
        lines.append(f"备注：{note_text}")
    for row in rows:
        name = str(row.get("item_name") or "").strip()
        if not name:
            continue
        unit = str(row.get("unit") or "").strip()
        quantity = format_quantity(row.get("quantity"))
        # 数量留空的行只写食材名（没量就没有单位的意义）：群里看到的是
        # 「这道菜要用的东西」，采购员自己补量，比「猪颈肉 斤」这种残句好读。
        lines.append(f"{name} {quantity}{unit}".rstrip() if quantity else name)
    return "\n".join(lines)


async def enqueue_purchase_push(
    db,
    *,
    order_date: str,
    push_seq: int,
    rows: Iterable[dict],
    updated: bool = False,
    submitter_name: str = "",
    remark: str = "",
    scheduled_at: Optional[str] = None,
) -> list[int]:
    """渲染并登记一次投递，返回出站行 id（零订阅 → 空列表）。

    ``scheduled_at`` 非空 = 定时版：出站行落 pending 并带上计划时刻，调度循环到点才发。
    """
    text = render_purchase_text(
        order_date,
        rows,
        updated=updated,
        submitter_name=submitter_name,
        remark=remark,
    )
    ids = await wecom_outbox.enqueue_topic(
        db,
        TOPIC_STAFF_MEAL_PURCHASE,
        params={"text": text},
        trigger=PushTrigger.EVENT,
        business_reference=build_business_reference(order_date, push_seq),
        summary=text.splitlines()[0],
        scheduled_at=scheduled_at,
    )
    if not ids:
        logger.warning(
            "员工餐采购单 %s 推送未入队：没有任何渠道订阅「员工餐采购计划」",
            order_date,
        )
    return list(ids)


def map_outbox_status(
    rows: Iterable[dict], now: Optional[datetime] = None
) -> Optional[tuple[str, str]]:
    """出站行 → 单据上的推送状态；没有行时返回 None（保持库里原值）。

    - 任一行 failed → failed（带最后一条错误）；
    - pending 行里还有 ``scheduled_at`` 晚于 ``now`` 的 → scheduled（等定时到点）；
    - 还有 pending / sending → pushing；
    - 其余（sent，或渠道停用被 skipped）→ pushed。

    优先级 fixed：failed > scheduled > pushing > pushed —— 失败要让人看见，定时只是
    「还没到点」，不能盖住失败。``now`` 缺省取墙上时钟；服务层读单时传 store 的时钟，
    测试用假时钟。
    """
    items = list(rows)
    if not items:
        return None
    statuses = [str(row.get("status") or "") for row in items]
    if "failed" in statuses:
        error = ""
        for row in items:
            if str(row.get("status") or "") == "failed":
                error = str(row.get("last_error") or "").strip()
                if error:
                    break
        return "failed", error or "企业微信推送失败"
    now_dt = now or datetime.now(CHINA_TZ)
    for row in items:
        if str(row.get("status") or "") != "pending":
            continue
        scheduled = str(row.get("scheduled_at") or "").strip()
        if not scheduled:
            continue
        try:
            due = datetime.fromisoformat(scheduled)
        except ValueError:
            continue
        if due > now_dt:
            return "scheduled", ""
    if any(status in {"pending", "sending"} for status in statuses):
        return "pushing", ""
    return "pushed", ""


async def latest_push_status(
    db,
    order_date: str,
    push_seq: int,
    *,
    now: Optional[datetime] = None,
) -> Optional[tuple[str, str]]:
    """按当前这一版（`push_seq`）查出站状态；出站行已被清理时返回 None。"""
    if push_seq <= 0:
        return None
    rows = await db.wecom_outbox_for_business_reference(
        PURCHASE_REFERENCE_PREFIX, build_business_reference(order_date, push_seq)
    )
    return map_outbox_status(rows, now)
