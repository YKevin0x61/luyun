"""ADR 0141 备货品身份键的纯函数测试（不建库、不碰时钟）。"""

from services.prep_item_identity import (
    id_of,
    item_key,
    keys_for,
    rule_key,
    rule_usage,
    total_for,
)


def test_item_key_prefers_id():
    assert item_key(7, "虾饺馅", "份") == ("id", 7)
    # 字符串 id 也归一到 int，避免同一品出现 ("id", "7") 与 ("id", 7) 两个桶
    assert item_key("7", "虾饺馅", "份") == ("id", 7)


def test_item_key_falls_back_to_name_and_unit():
    assert item_key(None, "虾饺馅", "份") == ("name", "虾饺馅", "份")
    assert item_key(None, None, None) == ("name", "", "")
    assert item_key(None, "", "") == ("name", "", "")


def test_rule_key_uses_semi_name_and_unit():
    assert rule_key({"prep_item_id": 3, "semi_name": "旧名", "unit": "份"}) == ("id", 3)
    assert rule_key({"prep_item_id": None, "semi_name": "虾饺馅", "unit": "份"}) == (
        "name",
        "虾饺馅",
        "份",
    )
    assert rule_key({}) == ("name", "", "")


def test_keys_for_with_id_returns_id_and_name_buckets():
    item = {"prep_item_id": 5, "item_name": "虾饺馅", "unit": "份"}
    assert keys_for(item) == (("id", 5), ("name", "虾饺馅", "份"))


def test_keys_for_without_id_returns_name_bucket_only():
    item = {"prep_item_id": None, "item_name": "虾饺馅", "unit": "份"}
    assert keys_for(item) == (("name", "虾饺馅", "份"),)


def test_total_for_sums_id_and_name_buckets():
    item = {"prep_item_id": 5, "item_name": "虾饺馅", "unit": "份"}
    totals = {("id", 5): 2.0, ("name", "虾饺馅", "份"): 1.5}
    assert total_for(totals, item) == 3.5


def test_total_for_missing_buckets_is_zero():
    item = {"prep_item_id": 5, "item_name": "虾饺馅", "unit": "份"}
    assert total_for({}, item) == 0.0
    assert total_for({("id", 6): 4.0}, item) == 0.0


def test_rule_usage_expands_factor_and_keeps_zero_factor():
    rules = [
        {"prep_item_id": 1, "semi_name": "甲", "unit": "份", "factor": 0.5},
        {"prep_item_id": None, "semi_name": "乙", "unit": "份", "factor": 0},
    ]
    assert list(rule_usage(rules, 4)) == [(("id", 1), 2.0), (("name", "乙", "份"), 0.0)]


def test_rule_usage_keeps_nonpositive_quantity_unfiltered():
    rules = [{"prep_item_id": 1, "semi_name": "甲", "unit": "份", "factor": 1}]
    # 退菜行/0 增量由调用方过滤，这里原样产出
    assert list(rule_usage(rules, -2)) == [(("id", 1), -2.0)]


def test_rule_usage_missing_factor_is_zero():
    rules = [{"prep_item_id": 1, "semi_name": "甲", "unit": "份"}]
    assert list(rule_usage(rules, 3)) == [(("id", 1), 0.0)]


def test_id_of():
    assert id_of(("id", 9)) == 9
    assert id_of(("name", "虾饺馅", "份")) is None
    assert id_of(()) is None
