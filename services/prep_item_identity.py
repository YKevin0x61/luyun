#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备货品身份键（ADR 0141）。

同一个备货品在预测 / 实绩 / 售罄 / 复盘四条链路里必须只有一种身份读法。本模块
就是那**唯一**一处：只回答「这个备货品 / 这条配方规则的身份键是什么」，不读库、
不看时钟、不写日志。

键统一成命名空间元组：

- ``("id", prep_item_id)``：规则或快照行已经对齐 `prep_items.id`；
- ``("name", 品名, 单位)``：还没对上主数据，回落到文本键。

规则侧品名取 `semi_name`，备货品侧取 `item_name`，两侧都 `str(... or "")`。
读取端用 `keys_for` 同时拿两种键、用 `total_for` 把两个桶相加——一个备货品可能
同时有「接了主数据的老规则」和「没接上的规则」，两桶相加、不是二选一。
"""

from typing import Any, Iterator, Mapping, Optional, Tuple

Key = Tuple[Any, ...]


def item_key(prep_item_id, item_name, unit) -> Key:
    """备货品（或快照行）的身份键：有 id 按 id，没 id 回落 (品名, 单位)。"""
    if prep_item_id is not None:
        return ("id", int(prep_item_id))
    return ("name", str(item_name or ""), str(unit or ""))


def rule_key(rule: Mapping) -> Key:
    """配方规则的身份键；规则侧品名是 `semi_name`，不是备货品的 `item_name`。"""
    return item_key(rule.get("prep_item_id"), rule.get("semi_name"), rule.get("unit"))


def keys_for(item: Mapping) -> Tuple[Key, ...]:
    """一个备货品要查的全部桶：有 id 时返回 (id 键, 名字键)，否则只有名字键。"""
    name_key = item_key(None, item.get("item_name"), item.get("unit"))
    if item.get("prep_item_id") is not None:
        return (item_key(item.get("prep_item_id"), None, None), name_key)
    return (name_key,)


def total_for(totals: Mapping[Key, float], item: Mapping) -> float:
    """一个备货品的量 = 它的 id 桶 + 名字桶；任一桶缺失按 0 计。"""
    return sum(float(totals.get(key, 0.0)) for key in keys_for(item))


def rule_usage(rules, quantity) -> Iterator[Tuple[Key, float]]:
    """配方展开：逐个 `(rule_key(rule), quantity × factor)`。

    非正数量 / 非正系数的过滤**不在这里**：预测、实绩、售罄三处对该过滤的条件
    本来就不同（实绩要单独处理退菜行、售罄跳过 0 增量），由调用方自己决定。
    """
    for rule in rules:
        yield rule_key(rule), float(quantity) * float(rule.get("factor") or 0)


def id_of(key) -> Optional[int]:
    """键里的备货品 id；名字键（还没对齐主数据）返回 None。"""
    if key and key[0] == "id":
        return int(key[1])
    return None
