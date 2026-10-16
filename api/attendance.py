#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""考勤表的 HTTP 适配层。口径在 `services/attendance/store.py`，这里只管翻译。

票 01 只有**一扇门**：管理端会话（`require_session`）。考勤表是店长的表 —— 里面有
全店人的底薪与假期，员工那一档连页面都进不去（页面清单里 `audience: admin`）。
这一层也只有一个读端点，还没有写路径：逐日改格在票 06（复用排班的单日覆盖写入口），
手填列在票 05，封账在票 04。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from api.security import require_session
from database import get_db
from services.attendance import AttendanceError, AttendanceStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/attendance", tags=["考勤"])

# 服务层的 `AttendanceError.code` 是稳定标识，不是给店长看的文案。这里把每个 code
# 翻成一句能照着改的话 —— 逐条点名该应用哪个迁移（同排班的 `not_migrated`）。
_ERROR_DETAILS = {
    "not_migrated": (
        "考勤表要读的表还没建好：请在 Admin「系统更新 → 数据库迁移」应用 "
        "migrations/pg/0005_scheduling.sql、migrations/pg/0021_overtime_entries.sql、"
        "migrations/pg/0026_statutory_holidays.sql 与 "
        "migrations/pg/0027_attendance_ledger.sql，然后刷新本页"
    ),
}

_ERROR_STATUS = {
    # `not_migrated` 不是「参数写错了」，是这台机器还没升级完 —— 503 比 400 诚实。
    "not_migrated": 503,
}


def _bad_request(exc: AttendanceError) -> HTTPException:
    detail = _ERROR_DETAILS.get(exc.code, "考勤参数不合法")
    logger.info("考勤请求被拒: code=%s", exc.code)
    return HTTPException(status_code=_ERROR_STATUS.get(exc.code, 400), detail=detail)


@router.get("/sheet")
async def attendance_sheet(
    db=Depends(get_db),
    _session: str = Depends(require_session),
) -> dict:
    """当月那一张考勤表（一人一张卡）。

    **接口上没有月份参数**，这是刻意的：这一页只算当月，选不到历史月份
    （`spec.md` 的 Implementation Decisions）。带 `?month=` 也不会被认。
    """
    store = AttendanceStore(db)
    try:
        return await store.sheet()
    except AttendanceError as exc:
        raise _bad_request(exc) from exc
