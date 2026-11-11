#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作台 nudge：排班 / 加班 / 卫生「叫醒谁、scope 怎么拼」的唯一出处。

nudge 不带数据（见 `services/realtime/hub.py`），客户端收到后自己重拉 HTTP。
scope 里的 `employee_id` 是归属不变式的一半：hub 的 `_staff_owns_scope` 只把
「scope 里 employee_id 等于自己」的那条推给员工连接。所以这里**一人一条**——
把两个人塞进同一条 scope，hub 只能整条丢或整条放行，两种都不对。

`realtime_hub.broadcast_nudge` / `_staff_owns_scope` / `allowed_topics` 都不在
本模块：归属不变式仍是 hub 的，这里只负责把 scope 拼成它要的形状。

三个 helper 保持搬家前的签名与语义，调用点不感知这次迁移。
"""

from typing import Iterable, Optional

from services.realtime.hub import realtime_hub


async def _fanout(
    topic: str,
    scope: dict,
    employee_ids: Iterable[Optional[int]],
    *,
    broadcast_when_unowned: bool = False,
) -> None:
    """按员工扇出一条 nudge：去重、一人一条、scope 带 `employee_id`。

    `employee_ids` 里的 None 与重复 id 都不额外播。一个有效 id 都没有时默认
    **不广播**（说不清是谁就不推，迁移前的排班 / 加班 helper 就是这个语义）；
    只有 `broadcast_when_unowned=True` 的全店级事件才原样播一条。
    """
    ids = {int(item) for item in employee_ids if item is not None}
    if not ids:
        if broadcast_when_unowned:
            await realtime_hub.broadcast_nudge(topic, scope)
        return
    for employee_id in ids:
        await realtime_hub.broadcast_nudge(
            topic, {**scope, "employee_id": employee_id}
        )


async def nudge_scheduling(reason: str, *employee_ids: Optional[int]) -> None:
    """排班或申请动了，叫相关的人自己来拉一次。

    换班牵两个人、批假牵一个人，所以收可变参数；读路径不广播（数据形状没变）。
    """
    await _fanout("scheduling", {"reason": reason}, employee_ids)


async def nudge_overtime(reason: str, *employee_ids: Optional[int]) -> None:
    """加班台账动了，叫相关的人自己来拉一次（一笔登记通常只牵一个人）。"""
    await _fanout("overtime", {"reason": reason}, employee_ids)


async def nudge_hygiene(resource: str, action: str, **scope) -> None:
    """卫生事件动了：`resource` / `action` 与调用方给的额外字段都进 scope。

    只有调用方给了 `employee_id` 才是**个人事件**（一人一条）；没给就是全店级
    事件（看板 / 档口 / 配置），员工照常收——hub 的 `_staff_owns_scope` 对
    scope 不带 employee_id 的那类放行。
    """
    await _fanout(
        "hygiene",
        {"resource": resource, "action": action, **scope},
        [scope.get("employee_id")],
        broadcast_when_unowned=True,
    )
