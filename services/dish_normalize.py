#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""菜品名称归一化共享模块。

抽取自 database.py:compute_sales_report 内部的局部 normalize() 与
sales_report.py 的 normalize_name_for_match()，两份逻辑完全一致。
统一这里，避免备货预测、销售报表、半成品换算各处分裂。

调用约定：
    from services.dish_normalize import normalize_dish_name
    norm = normalize_dish_name(raw_name)

行为约束（任何修改必须同步更新）：
    - 输入非字符串自动转字符串。
    - 去除装饰性括号前后缀（外卖、小/中/大/半份、N只/N个/N条），
      保留菜品规范名作为换算 key。
    - 不改变中间内容，只移除尾部 (-) 与已知前后缀。
    - 末尾 strip 空白。
"""

import re

# POS 原始菜名尾部的装饰性 "(-)"（可能带空白），采集入库前剥离。
_TRAILING_DASH_SUFFIX = re.compile(r'\s*\(-\)\s*$')

# (pattern, replacement) 列表保持有序，便于必要时按顺序排查。
_PATTERNS = (
    (re.compile(r'\(-\)$'), ''),
    (re.compile(r'^\(外卖\)'), ''),
    (re.compile(r'^\(普通\)'), ''),
    (re.compile(r'\(\d+只\)$'), ''),
    (re.compile(r'\(\d+个\)$'), ''),
    (re.compile(r'\(\d+条\)$'), ''),
    (re.compile(r'^\(小份\)'), ''),
    (re.compile(r'^\(中份\)'), ''),
    (re.compile(r'^\(大份\)'), ''),
    (re.compile(r'^\(半份\)'), ''),
)


def normalize_dish_name(name) -> str:
    """归一化菜品名称，返回去除装饰前后缀后的规范名。"""
    text = str(name) if name is not None else ''
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text.strip()


def strip_trailing_dash_suffix(name) -> str:
    """仅剥离尾部 "(-)" 后缀（采集入库用），保留 (普通)/(小份) 等其他前后缀。"""
    text = str(name) if name is not None else ''
    return _TRAILING_DASH_SUFFIX.sub('', text).strip()


__all__ = ["normalize_dish_name", "strip_trailing_dash_suffix"]
