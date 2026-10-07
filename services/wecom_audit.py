#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推送配置变更历史的记录构造（票 11，spec「鉴权与审计」）。

这一层只做**纯逻辑**：把「谁、对什么、做了什么、改前改后」拼成一条记录交给 repo
（``db_core/wecom_audit_repo.py`` 的 ``wecom_audit_add``）。放在这里而不是散在各路由里，
是为了让「变更前后值取哪些字段」只有一处口径 —— 页面上的「变更内容」一列直接读它算出来
的 ``changes``。

对象类型就是表名（``wecom_push_webhooks`` 等），页面把它翻成中文；这样审计记录与数据
管理页看到的是同一套标识，不必再维护一张对照表。

**快照是领域字段，不是整行**：只放页面上给人看的那几样（名称 / 启停 / 备注 / 订阅
目标……）。webhook 地址一律只以掩码出现 —— 审计要能读出「改了什么」，但不该成为第二份
凭据存储。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional

# 对象类型：就是表名。
AUDIT_OBJECT_CHANNEL = "wecom_push_webhooks"
AUDIT_OBJECT_GROUP = "wecom_channel_groups"
AUDIT_OBJECT_SUBSCRIPTION = "wecom_push_subscriptions"
AUDIT_OBJECT_JOB = "wecom_push_jobs"

# 动作：create / update / enable / disable / delete。
#
# 启停单列成 enable / disable（而不是塞进 update）：票面把「渠道的增删改与启停」并列，
# 事后查「这个群是什么时候被停用的」不该在一堆 update 里逐条翻。口径只有一个 ——
# 快照里 enabled 发生变化且没有别的字段跟着变，就是启停（``action_for_change``）。
AUDIT_ACTION_CREATE = "create"
AUDIT_ACTION_UPDATE = "update"
AUDIT_ACTION_ENABLE = "enable"
AUDIT_ACTION_DISABLE = "disable"
AUDIT_ACTION_DELETE = "delete"

# 「变更历史」的筛选项（接口回给页面，加一种对象类型只改这里）。
AUDIT_OBJECT_TYPES: tuple = (
    (AUDIT_OBJECT_CHANNEL, "推送渠道"),
    (AUDIT_OBJECT_GROUP, "渠道群组"),
    (AUDIT_OBJECT_SUBSCRIPTION, "推送订阅"),
    (AUDIT_OBJECT_JOB, "推送任务"),
)

AUDIT_ACTIONS: tuple = (
    (AUDIT_ACTION_CREATE, "新增"),
    (AUDIT_ACTION_UPDATE, "修改"),
    (AUDIT_ACTION_ENABLE, "启用"),
    (AUDIT_ACTION_DISABLE, "停用"),
    (AUDIT_ACTION_DELETE, "删除"),
)


def _jsonable(value: Any) -> Any:
    """把快照值收敛成 JSON 能原样读回来的形状（页面直接渲染它）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


def _dump(snapshot: Optional[Dict[str, Any]]) -> str:
    return json.dumps(_jsonable(snapshot or {}), ensure_ascii=False, sort_keys=True)


def changed_fields(
    before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """改动的字段 → ``{"before": 改前, "after": 改后}``。

    新建（没有改前）与删除（没有改后）都算「整份快照都是这次变更的内容」：页面上那两条
    也要看得见写了什么、删了什么。两边都在时只留**真的变了**的字段 —— 保存一次没动过的
    备注不该在历史里显示成一条变更。
    """
    if before is None:
        return {key: {"before": None, "after": _jsonable(value)}
                for key, value in (after or {}).items()}
    if after is None:
        return {key: {"before": _jsonable(value), "after": None}
                for key, value in (before or {}).items()}
    keys: Iterable[str] = dict.fromkeys([*before.keys(), *after.keys()])
    return {
        key: {"before": _jsonable(before.get(key)), "after": _jsonable(after.get(key))}
        for key in keys
        if before.get(key) != after.get(key)
    }


def action_for_change(
    before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]
) -> str:
    """写操作的动作：按快照差判出 ``create`` / ``enable`` / ``disable`` / ``update``。"""
    if before is None:
        return AUDIT_ACTION_CREATE
    changed = changed_fields(before, after)
    if not changed:
        return AUDIT_ACTION_UPDATE
    # 只有 enabled 变了：那就是启停（页面的快捷开关正是这一种，PUT 也会带上其余字段，
    # 值没变就不算「跟着变了」）。
    if set(changed) == {"enabled"}:
        return AUDIT_ACTION_ENABLE if changed["enabled"]["after"] else AUDIT_ACTION_DISABLE
    return AUDIT_ACTION_UPDATE


def record(
    *,
    action: str,
    object_type: str,
    object_id: Optional[int] = None,
    object_name: str = "",
    actor: str = "",
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """一条变更记录（交给 ``db.wecom_audit_add`` 落库的那个 dict）。

    动作可以由调用方显式给（删除时没有「改后」，判不出来），也可以传 ``action=None``
    走 ``action_for_change`` 的自动判定。
    """
    if action is None:
        action = action_for_change(before, after)
    return {
        "actor": str(actor or ""),
        "action": action,
        "object_type": object_type,
        "object_id": object_id,
        "object_name": object_name,
        "before_json": _dump(before),
        "after_json": _dump(after),
    }
