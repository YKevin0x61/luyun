#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工申请的群通知：文案、审批链接、入队。

两件事分开测：**文案长什么样**（纯字符串，不碰库）与**入队这一步失败不会把申请带走**
（FakeOutbox 抛异常 / 零订阅）。「提交申请之后真的入队了」那一条在
`tests/test_scheduling_api.py` 与 `tests/test_overtime_api.py` 里 —— 那里有完整的
员工会话与业务链路，比在这里手搓一个 app 更贴真实路径。
"""

import asyncio

import pytest

import services.wecom_request_notice as notice
from config import settings

LEAVE = {
    "id": 7,
    "start_date": "2026-11-05",
    "end_date": "2026-11-07",
    "note": "家里有事，回乡下",
}
ENTRY = {
    "id": 9,
    "entry_date": "2026-11-02",
    "half_hours": 5,
    "hours": 2.5,
    "kind": "overtime",
    "reason": "盘点到九点",
}
MAKEUP = {
    "id": 10,
    "entry_date": "2026-11-02",
    "half_hours": -2,
    "hours": -1.0,
    "kind": "makeup",
    "reason": "",
}
DEDUCTION = {"id": 3, "month": "2026-10", "days": 2}


class FakeOutbox:
    """记下每一次入队；`ids` 为空表示零订阅，`boom` 表示库写不进去。"""

    def __init__(self, ids=(1,), boom=None):
        self.calls = []
        self._ids = list(ids)
        self._boom = boom

    async def enqueue_topic(self, db, topic_id, **kwargs):
        self.calls.append({"db": db, "topic_id": topic_id, **kwargs})
        if self._boom is not None:
            raise self._boom
        return list(self._ids)


# ── 文案 ────────────────────────────────────────────────────────────────────


def test_a_one_day_leave_reads_like_a_sentence():
    text = notice.leave_request_text(
        {"id": 1, "start_date": "2026-11-05", "end_date": "2026-11-05"},
        employee_name="张三",
        base_url="https://admin.example.com",
    )

    assert text.splitlines()[0] == "【请假申请】张三 请 1 天假：2026-11-05"
    assert text.splitlines()[-1] == "去审批：https://admin.example.com/workbench/hr/inbox"


def test_a_multi_day_leave_says_both_ends():
    text = notice.leave_request_text(LEAVE, employee_name="张三", base_url="https://a.cn")

    assert "请 3 天假：2026-11-05 至 2026-11-07" in text
    assert "说明：家里有事，回乡下" in text


def test_the_note_is_optional_and_a_long_one_is_clipped():
    bare = notice.leave_request_text(
        {"id": 1, "start_date": "2026-11-05", "end_date": "2026-11-05"},
        employee_name="张三",
    )
    assert "说明" not in bare

    long_note = notice.leave_request_text(
        {**LEAVE, "note": "很" * 400}, employee_name="张三"
    )
    line = next(item for item in long_note.splitlines() if item.startswith("说明"))
    # 截断后仍是一行、且以省略号收尾（企微一条文本消息 2048 字节，别让人把消息顶满）。
    assert line.endswith("…")
    assert len(line) <= notice._MAX_REASON_CHARS + len("说明：") + len("…")


def test_overtime_and_makeup_read_differently():
    overtime = notice.overtime_entry_text(ENTRY, employee_name="李四")
    makeup = notice.overtime_entry_text(MAKEUP, employee_name="李四")

    assert overtime.splitlines()[0] == "【加班申请】李四 2026-11-02 加班 2.5 小时"
    assert "理由：盘点到九点" in overtime
    # 负数小时不能写出「补钟 -1 小时」。
    assert makeup.splitlines()[0] == "【补钟申请】李四 2026-11-02 补钟 -1 小时"


def test_a_deduction_talks_in_days():
    text = notice.overtime_deduction_text(DEDUCTION, employee_name="李四")

    assert text.splitlines()[0] == "【补钟申请】李四 申请用存假抵扣 2026-10 的补钟 2 天"


# ── 链接 ────────────────────────────────────────────────────────────────────


def test_the_link_points_at_the_manager_inbox():
    assert notice.admin_url(notice.LEAVE_REVIEW_PATH, "https://a.cn") == (
        "https://a.cn/workbench/hr/inbox"
    )
    assert notice.admin_url(notice.OVERTIME_REVIEW_PATH, "https://a.cn/") == (
        "https://a.cn/workbench/hr/overtime"
    )


def test_no_link_when_the_admin_address_is_not_configured(monkeypatch):
    """用户拍板：没配就不带链接 —— 宁少一句，也不发点不开的死链。"""
    monkeypatch.setattr(settings, "ADMIN_BASE_URL", "")

    text = notice.leave_request_text(LEAVE, employee_name="张三")

    assert "去审批" not in text
    assert "http" not in text


def test_the_link_is_read_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_BASE_URL", "https://shop.example.com/")

    text = notice.leave_request_text(LEAVE, employee_name="张三")

    assert "去审批：https://shop.example.com/workbench/hr/inbox" in text


# ── 入队 ────────────────────────────────────────────────────────────────────
#
# 用 `asyncio.run` 而不是 `@pytest.mark.anyio`：仓库没装 trio，anyio 插件会把每个
# 用例在 asyncio 与 trio 两个后端各跑一遍，trio 那半边必然 import 失败。


def test_a_leave_request_is_enqueued_with_its_business_id():
    outbox = FakeOutbox()

    asyncio.run(
        notice.notify_leave_request(None, LEAVE, employee_name="张三", outbox=outbox)
    )

    (call,) = outbox.calls
    assert call["topic_id"] == "leave_request"
    assert call["business_reference"] == "leave:7"
    assert call["params"]["text"].startswith("【请假申请】张三")
    # 事件型：不是定时，也不绑任务。
    assert call["trigger"].value == "event"


def test_an_overtime_entry_and_a_deduction_share_one_topic():
    outbox = FakeOutbox()

    async def _both():
        await notice.notify_overtime_entry(
            None, ENTRY, employee_name="李四", outbox=outbox
        )
        await notice.notify_overtime_deduction(
            None, DEDUCTION, employee_name="李四", outbox=outbox
        )

    asyncio.run(_both())

    assert [call["topic_id"] for call in outbox.calls] == ["overtime_request"] * 2
    # 两种业务各自去重：加班按登记 id，抵扣单按单据 id。
    assert [call["business_reference"] for call in outbox.calls] == [
        "overtime:9",
        "deduction:3",
    ]


def test_a_row_without_an_id_still_gets_a_stable_reference():
    outbox = FakeOutbox()

    asyncio.run(
        notice.notify_leave_request(
            None, {**LEAVE, "id": None}, employee_name="张三", outbox=outbox
        )
    )

    reference = outbox.calls[0]["business_reference"]
    assert reference.startswith("leave:") and len(reference) > len("leave:")


def test_zero_subscribers_is_not_an_error():
    outbox = FakeOutbox(ids=())

    # 不该抛：零订阅是配置问题，不是事故。
    asyncio.run(
        notice.notify_leave_request(None, LEAVE, employee_name="张三", outbox=outbox)
    )

    assert len(outbox.calls) == 1


def test_a_broken_outbox_never_fails_the_request():
    outbox = FakeOutbox(boom=RuntimeError("库写不进去"))

    async def _both():
        await notice.notify_leave_request(
            None, LEAVE, employee_name="张三", outbox=outbox
        )
        await notice.notify_overtime_entry(
            None, ENTRY, employee_name="李四", outbox=outbox
        )

    # 申请已经落库了：群通知发不出去绝不能让它变成「提交失败」。
    asyncio.run(_both())
