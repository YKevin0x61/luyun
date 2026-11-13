#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""菜品名归一化：装饰前缀可能叠加，必须剥到稳定为止（审查 R39）。"""

from services.dish_normalize import normalize_dish_name


def test_strips_single_prefix():
    assert normalize_dish_name("(外卖)虾饺") == "虾饺"
    assert normalize_dish_name("(普通)凤爪") == "凤爪"


def test_strips_stacked_prefixes():
    assert normalize_dish_name("(外卖)(普通)虾饺") == "虾饺"
    assert normalize_dish_name("(普通)(外卖)叉烧包") == "叉烧包"


def test_strips_prefix_and_count_suffix_together():
    assert normalize_dish_name("(外卖)(普通)虾饺(2只)") == "虾饺"


def test_keeps_middle_and_unknown_brackets():
    assert normalize_dish_name("沙翁(甜品)") == "沙翁(甜品)"
    assert normalize_dish_name("(招牌)虾饺") == "(招牌)虾饺"


def test_non_string_and_empty_input():
    assert normalize_dish_name(None) == ""
    assert normalize_dish_name("  ") == ""
