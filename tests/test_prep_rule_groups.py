#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逻辑菜分组（`services/prep_rule_groups`）：把外卖/规格变体折进同一个菜。

这些用例锁的是「改一次全组生效」的前提：同菜异名不能乱并，变体名必须并干净。
"""

from services.prep_rule_groups import group_key, group_rows, pick_main_name


def test_group_key_strips_takeout_prefix():
    assert group_key("(外卖)金莎脆皮红米肠") == "金莎脆皮红米肠"
    assert group_key("金莎脆皮红米肠") == "金莎脆皮红米肠"


def test_group_key_strips_quantity_suffix_after_takeout():
    # `(外卖)(2条)xxx` —— dish_normalize 只认结尾的 (N条)，这个它在开头，所以这里多剥一层
    assert group_key("(外卖)(2条)金莎脆皮红米肠") == "金莎脆皮红米肠"
    assert group_key("(外卖)(2个)金丝鲍汁腐皮卷") == "金丝鲍汁腐皮卷"
    assert group_key("(外卖) (小份)禄运干炒牛河") == "禄运干炒牛河"


def test_group_key_strips_trailing_dash():
    assert group_key("排骨凤爪蒸饭(-)") == "排骨凤爪蒸饭"


def test_group_key_does_not_merge_different_dishes():
    # 「灼」和「淋」是同菜异名，但字不一样：脚本不猜，留给核对清单
    assert group_key("(外卖)(小份)红葱头酱油淋生菜") != group_key("红葱头酱油灼生菜")
    assert group_key("(外卖)新碧绿鲜虾香茜饺") != group_key("碧绿鲜虾香茜饺")


def test_pick_main_name_prefers_plain_variant():
    assert pick_main_name(["(外卖)荔湾鲜虾艇仔粥", "荔湾鲜虾艇仔粥"]) == "荔湾鲜虾艇仔粥"
    assert pick_main_name(["荔湾鲜虾艇仔粥", "(外卖)荔湾鲜虾艇仔粥"]) == "荔湾鲜虾艇仔粥"


def test_pick_main_name_falls_back_to_variant():
    # 整组只有外卖变体时，不硬造一个堂食名
    assert pick_main_name(["(外卖)陈皮芫荽牛肉肠"]) == "(外卖)陈皮芫荽牛肉肠"


def test_group_rows_keeps_variants_together():
    rows = [
        {"dish_name": "金莎脆皮红米肠", "id": 1},
        {"dish_name": "(外卖)金莎脆皮红米肠", "id": 2},
        {"dish_name": "(外卖)(2条)金莎脆皮红米肠", "id": 3},
        {"dish_name": "大良双皮奶", "id": 4},
    ]
    grouped = group_rows(rows)
    assert list(grouped) == ["金莎脆皮红米肠", "大良双皮奶"]
    assert [r["id"] for r in grouped["金莎脆皮红米肠"]] == [1, 2, 3]
