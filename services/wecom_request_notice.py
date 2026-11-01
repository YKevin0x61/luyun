#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工申请（请假 / 加班补钟）的群通知：文案、店长审批页链接、入队。

只做一件事：把一条**已经落库**的申请拼成一段企微群文本，交给统一出站。文案里那句
「去审批」的地址由 ``ADMIN_BASE_URL``（config.py）拼出来；那一项留空时**整句省略** ——
门店地址猜不出来，发一个点不开的死链比不发更糟（店长会以为系统坏了，回头问
「这个链接怎么打不开」）。

为什么文案放这儿而不是塞进 ``wecom_push_topics``：注册表是「有哪些内容类型」的声明，
改一句中文不该动注册表；也不该放进 ``wecom_push_service`` —— 那个模块负责渲染与投递，
不该认识「请假」「补钟」这些业务词。排班与加班两边都从这里取，文案只此一份。

数字口径：加班的 ``half_hours`` 是半小时的整数倍，``hours = half_hours / 2``（台账
``services/overtime/ledger.py`` 的 ``_decorate`` 已经算好 ``hours`` 与 ``kind``），
这里直接用台账给的，不自己再除一遍。

**入队失败一律只记日志、不向外抛**：员工提交申请这件事绝不能因为群通知发不出去而失败
（同 `services/hygiene/notifier.py` 的取舍）。落败的行会留在发送记录里，店长能看见。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date
from typing import Any, Mapping, Optional

from config import settings
from services.wecom_outbox import wecom_outbox
from services.wecom_push_topics import (
    TOPIC_LEAVE_REQUEST,
    TOPIC_OVERTIME_REQUEST,
    PushTrigger,
)

logger = logging.getLogger(__name__)

# 店长那边的落点：请假去人事「待办」，加班去「加班统计」。两份路径写在
# ``admin-web/src/router/index.js`` 的 workbenchHrPage 里，改路由要同步这里。
LEAVE_REVIEW_PATH = "/workbench/hr/inbox"
OVERTIME_REVIEW_PATH = "/workbench/hr/overtime"

# 员工那边的落点：驳回之后他得回去重提，所以驳回那条要带员工页的链接（批准不带 ——
# 批完了没什么可点的，多一句链接只是噪音）。
STAFF_MONTH_PATH = "/workbench/staff/month"
STAFF_OVERTIME_PATH = "/workbench/staff/overtime"

# 一行的最大长度：企微群文本消息上限 2048 字节，两条申请各占一行绰绰有余，
# 但理由/备注是用户填的自由文本，长起来能顶满整条消息——截断留出链接与标题的位置。
_MAX_REASON_CHARS = 120

_OVERTIME_LABELS = {"overtime": "加班", "makeup": "补钟"}


def admin_url(path: str, base_url: Optional[str] = None) -> str:
    """把站内路径拼成能在群里点开的外链；``ADMIN_BASE_URL`` 没配就返回空串。

    ``base_url`` 只给测试用（避免为了一个字符串拼接去改全局 settings）。
    """
    base = str(settings.ADMIN_BASE_URL if base_url is None else base_url or "").strip()
    if not base:
        return ""
    return f"{base.rstrip('/')}{path}"


def _clip(text: Any) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= _MAX_REASON_CHARS:
        return cleaned
    return cleaned[:_MAX_REASON_CHARS] + "…"


def _hours_text(hours: Any) -> str:
    """小时数：2.5 / 1 / 一个都不多余的小数点。"""
    try:
        value = float(hours)
    except (TypeError, ValueError):
        return "?"
    return f"{value:g}"


def _days_between(start_date: Any, end_date: Any) -> int:
    """请假天数（两端都算）。日期坏了就按 1 天报，不抛——文案拼不出来不该挡住申请。"""
    try:
        first = date.fromisoformat(str(start_date))
        last = date.fromisoformat(str(end_date or start_date))
    except (TypeError, ValueError):
        return 1
    return max(1, (last - first).days + 1)


def leave_request_text(
    request: Mapping[str, Any],
    *,
    employee_name: str = "",
    base_url: Optional[str] = None,
) -> str:
    """一条请假申请的群通知。

    ``request`` 就是 ``SchedulingStore.submit_leave`` 返回的那一行（``start_date`` /
    ``end_date`` / ``note``），字段缺了也不会拼出半句话。
    """
    first = str(request.get("start_date") or "").strip()
    last = str(request.get("end_date") or first).strip()
    days = _days_between(first, last)
    who = str(employee_name or "").strip() or "有员工"
    span = first if first == last else f"{first} 至 {last}"

    lines = [f"【请假申请】{who} 请 {days} 天假：{span}"]
    note = _clip(request.get("note"))
    if note:
        lines.append(f"说明：{note}")
    url = admin_url(LEAVE_REVIEW_PATH, base_url)
    if url:
        lines.append(f"去审批：{url}")
    return "\n".join(lines)


def overtime_entry_text(
    entry: Mapping[str, Any],
    *,
    employee_name: str = "",
    base_url: Optional[str] = None,
) -> str:
    """一笔加班 / 补钟登记的群通知。

    ``entry`` 是台账 ``OvertimeLedger.submit`` 返回的那一行：``hours`` 已除过 2，
    ``kind`` 是 ``overtime`` 或 ``makeup``（正数记加班、负数记补钟）。
    """
    kind = _OVERTIME_LABELS.get(str(entry.get("kind") or ""), "加班/补钟")
    who = str(employee_name or "").strip() or "有员工"
    day = str(entry.get("entry_date") or "").strip()
    hours = _hours_text(entry.get("hours"))

    lines = [f"【{kind}申请】{who} {day} {kind} {hours} 小时"]
    reason = _clip(entry.get("reason"))
    if reason:
        lines.append(f"理由：{reason}")
    url = admin_url(OVERTIME_REVIEW_PATH, base_url)
    if url:
        lines.append(f"去审批：{url}")
    return "\n".join(lines)


def overtime_deduction_text(
    deduction: Mapping[str, Any],
    *,
    employee_name: str = "",
    base_url: Optional[str] = None,
) -> str:
    """一张「用存假抵扣补钟」单据的群通知。

    与 `overtime_entry_text` 同一条审批线，但说的是「几天」而不是「几小时」：抵扣单
    只能整天（``HALF_HOURS_PER_LEAVE_DAY``），写成小时会让人以为可以抵半天。
    """
    who = str(employee_name or "").strip() or "有员工"
    month = str(deduction.get("month") or "").strip()
    days = deduction.get("days")
    days_text = f"{int(days)} 天" if isinstance(days, int) or str(days or "").isdigit() else "若干天"

    lines = [f"【补钟申请】{who} 申请用存假抵扣 {month} 的补钟 {days_text}"]
    url = admin_url(OVERTIME_REVIEW_PATH, base_url)
    if url:
        lines.append(f"去审批：{url}")
    return "\n".join(lines)


# ── 入队 ────────────────────────────────────────────────────────────────────
#
# 三个入口一个形状：拼文案 → 交给统一出站 → 出什么事都只记日志。
# 业务引用用**真实业务 id**（「这条申请只推一次」），不用文案摘要 —— 请假文案里没有
# 会变的数字，同一个人连提两条一模一样的假是两件事，必须各推一条。


def _reference(prefix: str, row: Mapping[str, Any], text: str) -> str:
    """业务引用：``leave:12`` 这种，拿不到 id 才退回文案摘要（宁可多发也不静默丢）。"""
    row_id = row.get("id")
    if row_id is not None and str(row_id).strip():
        return f"{prefix}:{row_id}"
    return f"{prefix}:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]}"


async def _push_text(
    db, topic_id: str, text: str, reference: str, *, outbox=None
) -> None:
    sender = outbox if outbox is not None else wecom_outbox
    try:
        outbox_ids = await sender.enqueue_topic(
            db,
            topic_id,
            params={"text": text},
            trigger=PushTrigger.EVENT,
            business_reference=reference,
        )
    except Exception as exc:
        logger.warning("员工申请的群通知入队失败（不影响申请本身）topic=%s ref=%s: %s",
                       topic_id, reference, exc)
        return
    if not outbox_ids:
        # 零订阅是配置问题（店长该去订阅矩阵勾一下），不是事故：记一句就走。
        logger.info("员工申请的群通知未入队：没有渠道订阅这一类内容 topic=%s ref=%s",
                    topic_id, reference)


async def notify_leave_request(
    db,
    request: Mapping[str, Any],
    *,
    employee_name: str = "",
    base_url: Optional[str] = None,
    outbox=None,
) -> None:
    """员工提了请假 → 群里知会店长。失败不影响申请落库。"""
    text = leave_request_text(request, employee_name=employee_name, base_url=base_url)
    await _push_text(
        db, TOPIC_LEAVE_REQUEST, text, _reference("leave", request, text), outbox=outbox
    )


async def notify_overtime_entry(
    db,
    entry: Mapping[str, Any],
    *,
    employee_name: str = "",
    base_url: Optional[str] = None,
    outbox=None,
) -> None:
    """员工登记了加班 / 补钟 → 群里知会店长。"""
    text = overtime_entry_text(entry, employee_name=employee_name, base_url=base_url)
    await _push_text(
        db, TOPIC_OVERTIME_REQUEST, text, _reference("overtime", entry, text), outbox=outbox
    )


async def notify_overtime_deduction(
    db,
    deduction: Mapping[str, Any],
    *,
    employee_name: str = "",
    base_url: Optional[str] = None,
    outbox=None,
) -> None:
    """员工申请「用存假抵扣补钟」→ 同一条审批线，同一类订阅。"""
    text = overtime_deduction_text(deduction, employee_name=employee_name, base_url=base_url)
    await _push_text(
        db,
        TOPIC_OVERTIME_REQUEST,
        text,
        _reference("deduction", deduction, text),
        outbox=outbox,
    )


# ── 审批结果 ────────────────────────────────────────────────────────────────
#
# 走**同一类订阅**，不新开内容类型：店长勾「请假申请」订阅的是「请假这件事」，知道
# 「谁请了」和知道「批没批」是同一件事的两段，不该让他去矩阵里勾第二遍。文案前缀
# （``【请假已批】`` / ``【请假被驳】``）把两段分开，同一条时间线上读起来是连贯的。
#
# 三条规矩：
#
# * **批准不带链接**：批完了没有可点的下一步，多一句只是噪音。
# * **驳回带员工页链接**：他得回去重提，那句「去哪」要能点开。
# * **理由写全**：驳回是唯一不可逆的动作，员工看到「没批」却不知道为什么，只会来问人。


def _decision_headline(kind_label: str, approved: bool) -> str:
    return f"【{kind_label}已批】" if approved else f"【{kind_label}被驳】"


def leave_decision_text(
    request: Mapping[str, Any],
    *,
    approved: bool,
    employee_name: str = "",
    note: str = "",
    base_url: Optional[str] = None,
) -> str:
    """一条请假申请的审批结果。``request`` 要带 ``start_date`` / ``end_date``。"""
    first = str(request.get("start_date") or "").strip()
    last = str(request.get("end_date") or first).strip()
    days = _days_between(first, last)
    who = str(employee_name or request.get("employee_name") or "").strip() or "有员工"
    span = first if first == last else f"{first} 至 {last}"

    lines = [f"{_decision_headline('请假', approved)}{who} {span}（{days} 天）"]
    if not approved:
        reason = _clip(note or request.get("decision_note"))
        if reason:
            lines.append(f"原因：{reason}")
        url = admin_url(STAFF_MONTH_PATH, base_url)
        if url:
            lines.append(f"去重提：{url}")
    return "\n".join(lines)


def overtime_decision_text(
    entry: Mapping[str, Any],
    *,
    approved: bool,
    employee_name: str = "",
    note: str = "",
    base_url: Optional[str] = None,
) -> str:
    """一笔加班 / 补钟登记的审批结果。"""
    kind = _OVERTIME_LABELS.get(str(entry.get("kind") or ""), "加班/补钟")
    who = str(employee_name or entry.get("employee_name") or "").strip() or "有员工"
    day = str(entry.get("entry_date") or "").strip()
    hours = _hours_text(entry.get("hours"))

    lines = [f"{_decision_headline(kind, approved)}{who} {day} {kind} {hours} 小时"]
    if not approved:
        reason = _clip(note or entry.get("reject_reason"))
        if reason:
            lines.append(f"原因：{reason}")
        url = admin_url(STAFF_OVERTIME_PATH, base_url)
        if url:
            lines.append(f"去重记：{url}")
    return "\n".join(lines)


def overtime_deduction_decision_text(
    deduction: Mapping[str, Any],
    *,
    approved: bool,
    employee_name: str = "",
    note: str = "",
    base_url: Optional[str] = None,
) -> str:
    """一张「用存假抵扣补钟」单据的审批结果。"""
    who = str(employee_name or deduction.get("employee_name") or "").strip() or "有员工"
    month = str(deduction.get("month") or "").strip()
    days = deduction.get("days")
    days_text = f"{int(days)} 天" if isinstance(days, int) or str(days or "").isdigit() else "若干天"

    lines = [f"{_decision_headline('抵扣单', approved)}{who} 用存假抵扣 {month} 的补钟 {days_text}"]
    if not approved:
        reason = _clip(note or deduction.get("reject_reason"))
        if reason:
            lines.append(f"原因：{reason}")
        url = admin_url(STAFF_OVERTIME_PATH, base_url)
        if url:
            lines.append(f"去重提：{url}")
    return "\n".join(lines)


async def notify_leave_decision(
    db,
    request: Mapping[str, Any],
    *,
    approved: bool,
    employee_name: str = "",
    note: str = "",
    base_url: Optional[str] = None,
    outbox=None,
) -> None:
    """请假批了 / 驳了 → 群里知会（同一条订阅，不新开内容类型）。"""
    text = leave_decision_text(
        request, approved=approved, employee_name=employee_name, note=note, base_url=base_url
    )
    await _push_text(
        db,
        TOPIC_LEAVE_REQUEST,
        text,
        _reference("leave_decision", request, text),
        outbox=outbox,
    )


async def notify_overtime_decision(
    db,
    entry: Mapping[str, Any],
    *,
    approved: bool,
    employee_name: str = "",
    note: str = "",
    base_url: Optional[str] = None,
    outbox=None,
) -> None:
    """加班 / 补钟批了或驳了 → 群里知会。"""
    text = overtime_decision_text(
        entry, approved=approved, employee_name=employee_name, note=note, base_url=base_url
    )
    await _push_text(
        db,
        TOPIC_OVERTIME_REQUEST,
        text,
        _reference("overtime_decision", entry, text),
        outbox=outbox,
    )


async def notify_overtime_deduction_decision(
    db,
    deduction: Mapping[str, Any],
    *,
    approved: bool,
    employee_name: str = "",
    note: str = "",
    base_url: Optional[str] = None,
    outbox=None,
) -> None:
    """抵扣单批了或驳了 → 群里知会。"""
    text = overtime_deduction_decision_text(
        deduction,
        approved=approved,
        employee_name=employee_name,
        note=note,
        base_url=base_url,
    )
    await _push_text(
        db,
        TOPIC_OVERTIME_REQUEST,
        text,
        _reference("deduction_decision", deduction, text),
        outbox=outbox,
    )
