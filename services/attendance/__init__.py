#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""考勤表（`GLOSSARY.md` 的「考勤与假期」）：排班结果、净时长与花名册的**投影**。

它**不是**打卡系统：不记上下班时刻、不判迟到早退、不算实际工时（`docs/adr/0112`）。
这一层只读、只算当月；假期台账（月例休 / 补休 / 存假）是票 03 的事。

分层同仓库既有规矩：员工是谁从公共层取（`services.identity`），排班结果与加班台账
按表读 —— 考勤表本来就是它们三者的投影，不反过来调用它们的服务层。
"""

from typing import Any

from services.attendance.store import (
    LEDGER_WRITE_FIELDS,
    MANUAL_FIELDS,
    STATE_REST,
    STATE_TODO,
    STATE_WORK,
    AttendanceError,
    AttendanceStore,
)


def from_actor(actor: Any) -> int:
    """员工端 handler 从边界 actor 取员工 id。

    鸭子类型：服务层不认识 API 的 `WorkbenchActor`，只要求它有 `employee_id`；
    员工端那扇门已经保证拿到的是员工身份，所以这里不做第二遍身份判断。
    """
    return int(actor.employee_id)


__all__ = [
    "LEDGER_WRITE_FIELDS",
    "MANUAL_FIELDS",
    "STATE_REST",
    "STATE_TODO",
    "STATE_WORK",
    "AttendanceError",
    "AttendanceStore",
    "from_actor",
]
