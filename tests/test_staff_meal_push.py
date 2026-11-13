#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工餐采购单的推送文本（ADR 0135 票 04）。

这里只钉纯函数的形状 —— 真入队 / 零订阅 / 幂等键在 `tests/test_staff_meal_api.py`
里走 HTTP 验过。用户会直接看到这段文本，所以逐字对齐现网 OpenClaw 的观感：

    【员工餐】采购计划 2026-11-13
    清远鸡 3只
    沙姜 0.5斤
    ...

另外钉住长清单的行为：企微文本 2048 字节一条，超了要按整行拆成多条，不能把
某一行（「清远鸡 3只」）切成两半。
"""

from datetime import datetime

from services.staff_meal.push import (
    build_business_reference,
    format_quantity,
    map_outbox_status,
    render_purchase_text,
)
from services.wecom_push_service import WECOM_TEXT_BYTE_LIMIT, split_text_for_wecom


def test_quantity_reads_like_a_human_wrote_it():
    assert format_quantity(None) == ""
    assert format_quantity(9) == "9"
    assert format_quantity(9.0) == "9"
    assert format_quantity(0.5) == "0.5"
    assert format_quantity(0.50) == "0.5"
    assert format_quantity(1.25) == "1.25"
    assert format_quantity(40) == "40"


def test_business_reference_carries_date_and_version():
    assert (
        build_business_reference("2026-11-13", 1)
        == "staff_meal_purchase:2026-11-13:1"
    )
    assert (
        build_business_reference("2026-11-13", 12)
        == "staff_meal_purchase:2026-11-13:12"
    )


def test_render_title_and_one_line_per_item():
    text = render_purchase_text(
        "2026-11-13",
        [
            {"item_name": "清远鸡", "quantity": 3, "unit": "只"},
            {"item_name": "沙姜", "quantity": 0.5, "unit": "斤"},
            {"item_name": "柱侯酱", "quantity": 2, "unit": "瓶"},
        ],
    )
    assert text.splitlines() == [
        "【员工餐】采购计划 2026-11-13",
        "清远鸡 3只",
        "沙姜 0.5斤",
        "柱侯酱 2瓶",
    ]


def test_submitter_name_goes_under_the_title():
    """提交人要出现在群里（2026-11-11 用户裁定）：标题下一行「提交人：某某」。"""
    text = render_purchase_text(
        "2026-11-13",
        [{"item_name": "清远鸡", "quantity": 3, "unit": "只"}],
        submitter_name="余威威",
    )
    assert text.splitlines()[:3] == [
        "【员工餐】采购计划 2026-11-13",
        "提交人：余威威",
        "清远鸡 3只",
    ]
    # 空名字（历史单 / 管理端没留名）不写这一行，别渲染出「提交人：」。
    blank = render_purchase_text(
        "2026-11-13",
        [{"item_name": "清远鸡", "quantity": 3, "unit": "只"}],
        submitter_name="  ",
    )
    assert "提交人" not in blank


def test_latest_version_marks_updated():
    text = render_purchase_text(
        "2026-11-13",
        [{"item_name": "清远鸡", "quantity": 4, "unit": "只"}],
        updated=True,
    )
    assert text.splitlines()[0] == "【员工餐】采购计划 2026-11-13（更新）"


def test_remark_renders_between_submitter_and_items():
    """单据备注进群消息（2026-11-13 裁定 S4 方案 A）：提交人之后、清单之前单独一行。"""
    text = render_purchase_text(
        "2026-11-13",
        [
            {"item_name": "清远鸡", "quantity": 3, "unit": "只"},
            {"item_name": "沙姜", "quantity": 0.5, "unit": "斤"},
        ],
        submitter_name="余威威",
        remark="鸡要现杀的",
    )
    assert text.splitlines() == [
        "【员工餐】采购计划 2026-11-13",
        "提交人：余威威",
        "备注：鸡要现杀的",
        "清远鸡 3只",
        "沙姜 0.5斤",
    ]


def test_remark_without_submitter_goes_right_under_title():
    text = render_purchase_text(
        "2026-11-13",
        [{"item_name": "清远鸡", "quantity": 3, "unit": "只"}],
        remark="多带两把葱",
    )
    assert text.splitlines() == [
        "【员工餐】采购计划 2026-11-13",
        "备注：多带两把葱",
        "清远鸡 3只",
    ]


def test_remark_strips_and_folds_newlines_into_spaces():
    """企微一条文本里的换行会被拆成两条消息：备注里的换行要折成空格。"""
    text = render_purchase_text(
        "2026-11-13",
        [{"item_name": "清远鸡", "quantity": 3, "unit": "只"}],
        remark="  鸡要现杀的\r\n别放冰箱\n  ",
    )
    assert text.splitlines() == [
        "【员工餐】采购计划 2026-11-13",
        "备注：鸡要现杀的 别放冰箱",
        "清远鸡 3只",
    ]


def test_blank_remark_renders_nothing():
    for remark in ("", "   ", "\n\r\n", None):
        text = render_purchase_text(
            "2026-11-13",
            [{"item_name": "清远鸡", "quantity": 3, "unit": "只"}],
            remark=remark,
        )
        assert all("备注" not in line for line in text.splitlines()), remark


def test_items_without_quantity_show_name_only():
    """数量留空的行只写名字：没量就没有单位，不写「猪颈肉 斤」这种残句。"""
    text = render_purchase_text(
        "2026-11-14",
        [
            {"item_name": "猪颈肉", "quantity": None, "unit": "斤"},
            {"item_name": "南瓜", "quantity": 5, "unit": "斤"},
        ],
    )
    assert text.splitlines() == [
        "【员工餐】采购计划 2026-11-14",
        "猪颈肉",
        "南瓜 5斤",
    ]


def test_same_item_in_two_units_stays_two_lines():
    """同食材不同单位分行（ADR 0135 决定⑤），不能合并成一个数。"""
    text = render_purchase_text(
        "2026-11-13",
        [
            {"item_name": "排骨", "quantity": 14, "unit": "斤"},
            {"item_name": "排骨", "quantity": 1, "unit": "公斤"},
        ],
    )
    assert "排骨 14斤" in text.splitlines()
    assert "排骨 1公斤" in text.splitlines()


def test_long_list_splits_on_line_boundaries():
    """超长清单（150 条，约 3.7 KB）：按 <2048 字节拆成多条，每条都以完整行断开。"""
    rows = [
        {"item_name": f"测试食材{i:03d}", "quantity": i / 2, "unit": "公斤"}
        for i in range(1, 151)
    ]
    text = render_purchase_text("2026-11-13", rows)
    chunks = split_text_for_wecom(text)

    assert len(chunks) > 1, "150 行必须超过一条企微文本的上限"
    for chunk in chunks:
        assert len(chunk.encode("utf-8")) <= WECOM_TEXT_BYTE_LIMIT
        for line in chunk.splitlines():
            assert line, "拆分不能产生空行"
            if line.startswith("【员工餐】"):
                continue
            assert line.startswith("测试食材"), f"拆断了行：{line!r}"

    joined = set()
    for chunk in chunks:
        joined.update(chunk.splitlines())
    assert len(joined) == 151  # 标题 + 150 行，一条不少


def test_outbox_status_mapping():
    assert map_outbox_status([]) is None
    assert map_outbox_status([{"status": "pending"}]) == ("pushing", "")
    assert map_outbox_status([{"status": "sending"}]) == ("pushing", "")
    assert map_outbox_status([{"status": "sent"}]) == ("pushed", "")
    assert map_outbox_status([{"status": "skipped"}]) == ("pushed", "")
    assert map_outbox_status([{"status": "failed", "last_error": "webhook 401"}]) == (
        "failed",
        "webhook 401",
    )
    assert map_outbox_status(
        [{"status": "sent"}, {"status": "failed", "last_error": "超时"}]
    ) == ("failed", "超时")


def test_outbox_status_mapping_distinguishes_scheduled():
    """pending + 未来的 scheduled_at → scheduled；到点 / 无计划 → pushing（契约 §5）。"""
    now = datetime.fromisoformat("2026-11-13T10:00:00+08:00")
    future = {"status": "pending", "scheduled_at": "2026-11-14T10:00:00+08:00"}
    due_now = {"status": "pending", "scheduled_at": "2026-11-13T10:00:00+08:00"}
    past = {"status": "pending", "scheduled_at": "2026-11-12T10:00:00+08:00"}

    assert map_outbox_status([future], now) == ("scheduled", "")
    assert map_outbox_status([due_now], now) == ("pushing", "")
    assert map_outbox_status([past], now) == ("pushing", "")
    # 没到点也不能盖住失败：失败优先级最高，页面要让店长看见「没发出去」。
    assert map_outbox_status(
        [future, {"status": "failed", "last_error": "webhook 401"}], now
    ) == ("failed", "webhook 401")
    # 已发成功的一版即使 scheduled_at 还写着未来时刻，也按已推处理。
    assert map_outbox_status(
        [{"status": "sent", "scheduled_at": "2026-11-14T10:00:00+08:00"}], now
    ) == ("pushed", "")
