#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""考勤表（`GLOSSARY.md` 的「考勤与假期」）：排班结果、净时长与花名册的**投影**。

它**不是**打卡系统：不记上下班时刻、不判迟到早退、不算实际工时（`docs/adr/0112`）。
这一层只读、只算当月；假期台账（月例休 / 补休 / 存假）是票 03 的事。

分层同仓库既有规矩：员工是谁从公共层取（`services.identity`），排班结果与加班台账
按表读 —— 考勤表本来就是它们三者的投影，不反过来调用它们的服务层。
"""

from services.attendance.store import (
    MANUAL_FIELDS,
    STATE_REST,
    STATE_TODO,
    STATE_WORK,
    AttendanceError,
    AttendanceStore,
)

__all__ = [
    "MANUAL_FIELDS",
    "STATE_REST",
    "STATE_TODO",
    "STATE_WORK",
    "AttendanceError",
    "AttendanceStore",
]
