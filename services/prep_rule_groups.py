#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配方系数按「逻辑菜」分组。

143 条规则里 52 条是 `(外卖…)` 变体：同一个菜在库里存了 3~6 行（堂食一条、外卖一条、
外卖 2 条一条，每种还可能对应多个备货品）。页面按原始行展示会重复得没法看，也没法
「改一次、全组生效」。

分组只发生在展示与编辑层，**不落库**：计算侧（备货建议量、销售报表）仍逐行匹配，
`services.dish_normalize` 的口径不动。所以这里多剥一层它剥不掉的组合前缀
（`(外卖)(2个)xxx` —— dish_normalize 只认结尾的 `(N个)`）。
"""

import re
from typing import Dict, Iterable, List

_PREFIX = re.compile(r'^\((?:外卖|普通|小份|中份|大份|半份)(?:JD)?\)')
_QTY = re.compile(r'^(?:\(\d+只\)|\(\d+个\)|\(\d+条\))')
_TRAILING_DASH = re.compile(r'(?:\(-\))$')
_WS = re.compile(r'\s+')


def group_key(dish_name) -> str:
    """把一个菜名压成「逻辑菜」的 key。反复剥装饰前缀/数量后缀直到稳定。"""
    text = str(dish_name or '')
    for _ in range(6):
        nxt = _WS.sub(' ', _QTY.sub('', _PREFIX.sub('', text))).strip()
        nxt = _TRAILING_DASH.sub('', nxt).strip()
        if nxt == text:
            break
        text = nxt
    return text


def pick_main_name(names: Iterable[str]) -> str:
    """组内的「主名」：优先本身不带装饰的那个，没有就取最短的。

    `(外卖)荔湾鲜虾艇仔粥` 与 `荔湾鲜虾艇仔粥` 同组时显示后者；只有外卖变体时
    （例如 `(外卖)陈皮芫荽牛肉肠`）就退回外卖原名，不硬造一个堂食名。
    """
    names = [str(n) for n in names]
    plain = [n for n in names if group_key(n) == n.strip()]
    pool = plain or names
    return sorted(pool, key=lambda n: (len(n), n))[0]


def group_rows(rows: Iterable[Dict], key_field: str = 'dish_name') -> Dict[str, List[Dict]]:
    """把规则行按逻辑菜分组，返回 {group_key: [row, ...]}，组内保持入参顺序。"""
    grouped: Dict[str, List[Dict]] = {}
    for row in rows:
        grouped.setdefault(group_key(row.get(key_field)), []).append(row)
    return grouped


__all__ = ["group_key", "pick_main_name", "group_rows"]
