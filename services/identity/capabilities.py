#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工的管理权限开关（2026-10-05 用户裁定：由超级管理员逐项放权）。

## 为什么有这一层

原来只有一个档位：`hygiene_employees.permission`（`普通员工` / `管理员`），勾上「管理员」
就一次拿到三项能力（日常验收、专项验收、整改单），而且只在员工端手机页里生效。用户要的是
**每项能力一个开关**，由超级管理员在花名册里逐项放 —— 有的人只该判日常、不该开整改单，
有的人只该发榜、不该碰别人交的照片，这些以前表达不出来。

## 契约

**能力键名是前后端契约**：这里十项，前端 `admin-web/src/utils/adminCaps.js` 一份，
两边键名与顺序必须逐字相同；`tests/test_hygiene_admin_caps.py` 会读前端那份文件比对。
**线上只走键名**（英文短横线风格），中文标签只用于界面显示。

## 与 `permission` 那一列的关系

`permission` 保留（花名册与员工页拿它显示"管理员 / 普通员工"这一个人话标签），但**判据一律
以开关为准**：迁移 `0015` 把升级前的「管理员」回填成下面那三项，所以升级当场行为不变；
之后在花名册里动的每一个勾都直接决定他能不能做那件事，不再有"标签给了、开关没给"的中间态。
"""

from __future__ import annotations

import json
from typing import Iterable, Optional

# ── 十项能力（键名 = 契约，顺序 = 花名册里的显示顺序）────────────────────────────
CAP_DAILY_REVIEW = "daily_review"  # 日常验收：判别人交的日常检查、看原图与标准图
CAP_DEEP_REVIEW = "deep_review"  # 专项验收：判专项卫生的前后对照
CAP_FIX = "fix"  # 整改单：开单 + 验收 + 驳回
CAP_ATTIRE = "attire"  # 仪容仪表：看/判仪容打卡、换标准
CAP_STANDARD = "standard"  # 标准图管理：换标准图、改标注、导出
CAP_ZONE = "zone"  # 工作区与检查项：加/改/删工作区与检查项
CAP_ROSTER = "roster"  # 花名册与排班：审批、停用、改权限/班次/区
CAP_BOARDS = "boards"  # 红黑榜与教材：标记合格对照、发榜
CAP_CLOCK = "clock"  # 时限设置：日常 / 专项的时限
CAP_DATA = "data"  # 数据与归档：看记录、导出、清理

CAPABILITIES: tuple[str, ...] = (
    CAP_DAILY_REVIEW,
    CAP_DEEP_REVIEW,
    CAP_FIX,
    CAP_ATTIRE,
    CAP_STANDARD,
    CAP_ZONE,
    CAP_ROSTER,
    CAP_BOARDS,
    CAP_CLOCK,
    CAP_DATA,
)

CAPABILITY_LABELS: dict[str, str] = {
    CAP_DAILY_REVIEW: "日常验收",
    CAP_DEEP_REVIEW: "专项验收",
    CAP_FIX: "整改单",
    CAP_ATTIRE: "仪容仪表",
    CAP_STANDARD: "标准图管理",
    CAP_ZONE: "工作区与检查项",
    CAP_ROSTER: "花名册与排班",
    CAP_BOARDS: "红黑榜与教材",
    CAP_CLOCK: "时限设置",
    CAP_DATA: "数据与归档",
}

CAPABILITY_NOTES: dict[str, str] = {
    CAP_DAILY_REVIEW: "判别人交的日常检查，看原图与标准图对照",
    CAP_DEEP_REVIEW: "判专项卫生的前后对照",
    CAP_FIX: "开整改单、验收或驳回整改单",
    CAP_ATTIRE: "看与判仪容打卡，维护仪容标准",
    CAP_STANDARD: "换标准图、改标注、导出整套标准",
    CAP_ZONE: "加/改/删工作区与检查项",
    CAP_ROSTER: "审批入职、停用、改权限与班次",
    CAP_BOARDS: "标记合格对照、发红黑榜、编教材",
    CAP_CLOCK: "设日常与专项的时限",
    CAP_DATA: "看历史记录、导出归档、清理数据",
}

#: 升级前那个「管理员」档位等于这三项 —— 迁移 `0015` 的回填值就是它，改这里要同步改迁移。
LEGACY_ADMIN_CAPABILITIES: tuple[str, ...] = (CAP_DAILY_REVIEW, CAP_DEEP_REVIEW, CAP_FIX)

_CAP_SET = frozenset(CAPABILITIES)


def parse_caps(raw: Optional[str]) -> tuple[str, ...]:
    """把库里那一列（JSON 数组文本）解析成能力键元组。

    **认不出的键一律丢掉、坏数据当空**（fail-closed）：这一列是权限，宁可少给一项，
    也不能因为格式坏了就默认放行。顺序按 `CAPABILITIES` 归一，方便比对与显示。
    """
    if not raw:
        return ()
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError):
        return ()
    if not isinstance(loaded, (list, tuple)):
        return ()
    picked = []
    for item in loaded:
        key = str(item).strip() if item is not None else ""
        if key in _CAP_SET and key not in picked:
            picked.append(key)
    return tuple(key for key in CAPABILITIES if key in picked)


def dump_caps(caps: Iterable[str] | None) -> str:
    """写回库里的形状：JSON 数组文本，只留认识的键、去重、按声明顺序（比对稳定）。"""
    if not caps:
        return "[]"
    picked = []
    for item in caps:
        key = str(item).strip() if item is not None else ""
        if key in _CAP_SET and key not in picked:
            picked.append(key)
    ordered = [key for key in CAPABILITIES if key in picked]
    return json.dumps(ordered, ensure_ascii=False)


def has_cap(caps: Iterable[str] | None, cap: str) -> bool:
    """这个人有没有这一项。`caps` 可以是已解析的元组，也可以是任意可迭代。"""
    if not caps or not cap:
        return False
    return cap in set(caps)
