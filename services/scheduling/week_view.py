#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""周表的一格怎么读（纯函数）：文字推送与图片推送共用同一份判定。

为什么不各写一份：这一格有四种「没有班次」的情形 —— 这一行还没写（未排）、那天休、
那天请假、以及班次行被硬删掉（`shift_name is None` 但没有请假记录）。把它们都读成
「休」，店长会以为那个人下周不用来；两边各写一份判定，迟早有一边先漂。

`SchedulingStore.week_grid` 产出 `cells[日期]` 这个形状，页面、文字推送、图片推送
都从它读 —— 这里只把「这一格是什么」翻成一个稳定的状态，不碰排版。
"""

from __future__ import annotations

from typing import Any, Mapping, NamedTuple

# 一格的状态：这四种必须分得开（见模块注释）。
KIND_SHIFT = "shift"
KIND_REST = "rest"
KIND_LEAVE = "leave"
KIND_UNSCHEDULED = "unscheduled"


class CellState(NamedTuple):
    """一格的读法：上什么班 / 在哪个区 / 是不是半天。"""

    kind: str
    shift_name: str = ""
    zone_name: str = ""
    half_rest: bool = False

    @property
    def is_shift(self) -> bool:
        return self.kind == KIND_SHIFT


def cell_state(cell: Mapping[str, Any] | None) -> CellState:
    """把 `week_grid` 的一格读成状态。

    ``scheduled=False`` 是「这一行还没写」（还没铺到 / 谁都没配规则），不是休 —— 那个
    区分是这一层存在的理由。班次行被硬删时 `shift_name` 为 None、`scheduled` 仍为真，
    所以先看 `scheduled` 再看名字。
    """
    value = cell or {}
    if not value.get("scheduled"):
        return CellState(KIND_UNSCHEDULED)
    shift_name = value.get("shift_name")
    if shift_name is None:
        if value.get("leave"):
            return CellState(KIND_LEAVE)
        return CellState(KIND_REST)
    if not str(shift_name).strip():
        # 空字符串只可能来自脏数据（正常要么 None、要么真名字）。按「上什么班」读会画出
        # 一个没有字的色块，读的人得靠猜 —— 当成未排。
        return CellState(KIND_UNSCHEDULED)
    return CellState(
        KIND_SHIFT,
        shift_name=str(shift_name),
        zone_name=str(value.get("zone_name") or ""),
        half_rest=bool(value.get("half_rest")),
    )


def cell_caption(state: CellState) -> str:
    """这一格的一句话写法（文字推送用）：``白班（半天）·凉菜间`` / ``休`` / ``请假`` / ``未排``。"""
    if state.kind == KIND_UNSCHEDULED:
        return "未排"
    if state.kind == KIND_REST:
        return "休"
    if state.kind == KIND_LEAVE:
        return "请假"
    text = state.shift_name
    if state.half_rest:
        text += "（半天）"
    if state.zone_name:
        text += f"·{state.zone_name}"
    return text
