#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作任务的企微群推送（ADR 0131 / 票 09）。

只有发布者勾了「同时发企业微信群」才走这里：发布即时任务、或定期任务在「当期
当天生成」时，把标题 / 截止 / 指派摘要渲染成一段文本，经事件型通道入队
（`services/wecom_outbox.py` 的 `enqueue_topic`）。

幂等键 = `work_task:{definition_id}:{period_date}`：同一期只落一条出站记录，
重试 / 常驻循环重复调用都不会发两条。零订阅时 `enqueue_topic` 返回空列表 ——
调用方要把「没有群订阅」提示回发布页，但任务本身照常创建（推送不是业务前置）。
逾期没有额外的推送：任务发布/生成那一刻发过就够了，不催办。
"""

from __future__ import annotations

import logging

from services.wecom_outbox import PushTrigger, wecom_outbox
from services.wecom_push_topics import TOPIC_WORK_TASK

logger = logging.getLogger(__name__)

#: 出站业务引用的前缀（幂等键的第二段）。改格式要同时改读取侧（当前没有读取侧）。
WORK_TASK_REFERENCE_PREFIX = TOPIC_WORK_TASK


def build_business_reference(definition_id: int, period_date: str) -> str:
    return f"{WORK_TASK_REFERENCE_PREFIX}:{int(definition_id)}:{period_date}"


def render_work_task_text(
    *,
    title: str,
    period_date: str,
    deadline_hhmm: str,
    assignee_text: str,
) -> str:
    """渲染群消息正文：一行标题 + 截止 + 指派，最后一句告诉员工怎么交。"""
    lines = [
        f"【工作任务】{title}",
        f"截止：{period_date} {deadline_hhmm}",
        f"指派：{assignee_text}",
        "完成后拍照提交，由店长验收。",
    ]
    return "\n".join(line for line in lines if line.strip())


async def enqueue_work_task_push(
    db,
    *,
    definition_id: int,
    period_date: str,
    title: str,
    deadline_hhmm: str,
    assignee_text: str,
) -> list[int]:
    """渲染并登记一次群推送，返回出站行 id（零订阅 → 空列表）。"""
    text = render_work_task_text(
        title=title,
        period_date=period_date,
        deadline_hhmm=deadline_hhmm,
        assignee_text=assignee_text,
    )
    ids = await wecom_outbox.enqueue_topic(
        db,
        TOPIC_WORK_TASK,
        params={"text": text},
        trigger=PushTrigger.EVENT,
        business_reference=build_business_reference(definition_id, period_date),
        summary=text.splitlines()[0],
    )
    if not ids:
        logger.warning(
            "工作任务 %s（%s）推送未入队：没有任何渠道订阅「工作任务」",
            title,
            period_date,
        )
    return list(ids)
