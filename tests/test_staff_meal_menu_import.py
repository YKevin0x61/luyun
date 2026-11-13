#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工餐菜单 xlsx 解析的黄金测试（票 02）。

真实数据：``tests/fixtures/staff_meal/2026-{09,10,11}.xlsx`` 是店里 2026 年 9/10/11 月
三张菜单模板的原样拷贝（**不要改内容**）。下面每一组数字都来自对这三个文件的实测，
改了解析口径必须能解释差异，不能顺手把期望值调成「当前输出」。
"""

from io import BytesIO
from pathlib import Path

import openpyxl
import pytest

from services.staff_meal.menu_import import (
    MenuDay,
    MenuImportError,
    parse_menu_workbook,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "staff_meal"
FILES = {
    (2026, 9): "2026-09.xlsx",
    (2026, 10): "2026-10.xlsx",
    (2026, 11): "2026-11.xlsx",
}

# 票 02 的黄金数字（PoC 实测，三个月一致）。
EXPECTED = {
    9: {"days": 30, "lunch": 82, "dinner": 78, "drink": 17, "freeform": 4},
    10: {"days": 31, "lunch": 85, "dinner": 81, "drink": 19, "freeform": 4},
    11: {"days": 30, "lunch": 80, "dinner": 75, "drink": 19, "freeform": 5},
}


def _load(year: int, month: int) -> list[MenuDay]:
    return parse_menu_workbook((FIXTURES / FILES[(year, month)]).read_bytes(), year, month)


@pytest.fixture(scope="module")
def months() -> dict[int, list[MenuDay]]:
    return {month: _load(2026, month) for month in (9, 10, 11)}


def _by_date(days: list[MenuDay]) -> dict[str, MenuDay]:
    return {day.menu_date: day for day in days}


def _count_lunch(day: MenuDay) -> int:
    return sum(1 for value in (day.lunch_main, day.lunch_side1, day.lunch_side2) if value)


def _count_dinner(day: MenuDay) -> int:
    return sum(1 for value in (day.dinner_main, day.dinner_side1, day.dinner_side2) if value)


def _workbook_bytes(rows: list[list[object]]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ── 月份骨架 ────────────────────────────────────────────────────


def test_every_month_has_contiguous_days_and_expected_counts(months):
    for month, days in months.items():
        expected = EXPECTED[month]
        assert len(days) == expected["days"], f"{month} 月天数"
        assert [day.menu_date for day in days] == [
            f"2026-{month:02d}-{day:02d}" for day in range(1, expected["days"] + 1)
        ], f"{month} 月不是连续的自然日"
        assert sum(_count_lunch(day) for day in days) == expected["lunch"]
        assert sum(_count_dinner(day) for day in days) == expected["dinner"]
        assert sum(1 for day in days if day.drink) == expected["drink"]
        assert sum(1 for day in days if day.freeform) == expected["freeform"]


def test_weekdays_are_in_range(months):
    for days in months.values():
        for day in days:
            assert day.weekday is None or 1 <= day.weekday <= 7, day


# ── 自主发挥日 ──────────────────────────────────────────────────


def test_freeform_days_are_lunch_only(months):
    freeform_days = [day for days in months.values() for day in days if day.freeform]
    assert len(freeform_days) == 13  # 9 月 4 + 10 月 4 + 11 月 5
    for day in freeform_days:
        assert day.lunch_main == "自主发挥", day
        assert day.lunch_side1 is None and day.lunch_side2 is None, day
        assert day.dinner_main is None and day.dinner_side1 is None and day.dinner_side2 is None, day
        assert day.drink is None, day


# ── 具体日期 ────────────────────────────────────────────────────


def test_mid_autumn_date_cell_with_text(months):
    """9/25 的日期格是文本「25（中秋节）」，必须取出 25 并保留当天三层信息。"""
    day = _by_date(months[9])["2026-09-25"]
    assert day.weekday == 5  # 2026-09-25 是周五
    assert (day.lunch_main, day.lunch_side1, day.lunch_side2) == (
        "番茄炖牛腩",
        "青瓜炒肉卷",
        "清炒小白菜",
    )
    assert (day.dinner_main, day.dinner_side1, day.dinner_side2) == (
        "花生焖猪脚",
        "芋丝蒸腊味",
        "清炒小白菜",
    )
    assert day.drink == "龙骨玉米汤/苹果"  # 一格写两样：汤 + 水果
    assert day.freeform is False


def test_known_november_days(months):
    by_date = _by_date(months[11])

    day12 = by_date["2026-11-12"]
    assert day12.lunch_main == "金针菇蒸肥牛卷"
    assert day12.lunch_side1 == "肉沫茄子"
    assert day12.lunch_side2 == "清炒冬瓜"
    assert day12.dinner_main == "卤鸡爪"
    assert day12.dinner_side1 == "西蓝花炒肉片"
    assert day12.dinner_side2 == "清炒冬瓜"
    assert day12.drink is None  # 当天饮品格是「/」

    day13 = by_date["2026-11-13"]
    assert (day13.lunch_main, day13.lunch_side1, day13.lunch_side2) == (
        "辣子鸡",
        "平菇炒牛肉丸",
        "耗油娃娃菜",
    )
    assert (day13.dinner_main, day13.dinner_side1, day13.dinner_side2) == (
        "菠萝粒酸甜排骨",
        "洋葱炒猪颈肉",
        "耗油娃娃菜",
    )
    assert day13.drink == "圣女果"


def test_dish_name_keeps_cooking_notes():
    """「鹅肉（卤/炒/焖）」这类带做法的菜名要原样保留，不要在括号处截断。"""
    days = _load(2026, 9)
    names = {
        value
        for day in days
        for value in (
            day.lunch_main,
            day.lunch_side1,
            day.lunch_side2,
            day.dinner_main,
            day.dinner_side1,
            day.dinner_side2,
        )
        if value
    }
    assert "鹅肉（卤/炒/焖）" in names


# ── 坏输入 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "data"),
    [
        ("非 xlsx 字节", b"not an xlsx"),
        ("空字节", b""),
    ],
)
def test_rejects_non_workbook_bytes(label, data):
    with pytest.raises(MenuImportError):
        parse_menu_workbook(data, 2026, 11)


def test_rejects_workbook_without_time_row():
    data = _workbook_bytes([["2026年11月员工餐饮菜单"], ["随便", None]])
    with pytest.raises(MenuImportError, match="时间"):
        parse_menu_workbook(data, 2026, 11)


def test_rejects_block_without_weekday_row():
    data = _workbook_bytes(
        [["2026年11月员工餐饮菜单"], ["时间", None, None, 1], ["午餐", "主菜", None, "菜"]]
    )
    with pytest.raises(MenuImportError, match="星期"):
        parse_menu_workbook(data, 2026, 11)


def test_rejects_unparseable_day_cell():
    data = _workbook_bytes(
        [
            ["2026年11月员工餐饮菜单"],
            ["时间", None, None, "abc"],
            ["星期", None, None, "日"],
        ]
    )
    with pytest.raises(MenuImportError, match="日期格"):
        parse_menu_workbook(data, 2026, 11)


def test_rejects_wrong_month_title():
    """把 9 月文件当 10 月导入必须当场报错，不能静默写错月份。"""
    data = (FIXTURES / "2026-09.xlsx").read_bytes()
    with pytest.raises(MenuImportError, match="不一致"):
        parse_menu_workbook(data, 2026, 10)


def test_error_messages_are_chinese_and_carry_position():
    data = _workbook_bytes(
        [["2026年11月员工餐饮菜单"], ["时间", None, None, "abc"], ["星期", None, None, "日"]]
    )
    with pytest.raises(MenuImportError) as ctx:
        parse_menu_workbook(data, 2026, 11)
    message = str(ctx.value)
    assert "第 2 行" in message and "日期格" in message


# ── 与 store 的写列对齐 ─────────────────────────────────────────


def test_menu_day_as_row_matches_store_fields(months):
    row = months[9][0].as_row()
    assert set(row) == {
        "menu_date",
        "weekday",
        "lunch_main",
        "lunch_side1",
        "lunch_side2",
        "dinner_main",
        "dinner_side1",
        "dinner_side2",
        "drink",
        "freeform",
    }
    assert row["menu_date"] == "2026-09-01"
