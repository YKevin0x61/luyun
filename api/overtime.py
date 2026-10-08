#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加班与补钟的 HTTP 适配层。台账在 `services/overtime/ledger.py`，这里只管翻译。

票 01 只开**员工自己那一面**（`/api/overtime/me`）：提交、看自己的记录、撤回。
管理端的审批与统计在票 02，店长在手机上的审批面在票 04 —— 都不在这里提前开，
所以这一层的每个端点都挂员工门（`require_staff_session`），**接口上也没有
`employee_id`**：员工会话读得到的、动得了的只有自己那几笔。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.security import require_staff_session
from database import get_db
from services.overtime.ledger import (
    HALF_HOURS_MAX,
    MAX_REASON,
    EntryActor,
    OvertimeError,
    OvertimeLedger,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/overtime", tags=["加班与补钟"])

# 服务层的 `OvertimeError.code` 是稳定标识，不是给员工看的文案。这里把每个 code 翻成
# 一句能照着改的话 —— 「参数不合法」那种一句话会把「前天补不了」和「时长打错了」
# 说成同一件事，而这两件事员工要做的事完全不一样。
_ERROR_DETAILS = {
    "invalid_date": "日期格式应该是 YYYY-MM-DD：请重新选一天",
    # 未来与过去分开说：一个是「这事还没发生」，一个是「窗口过了、得找店长」。
    "future_day": "还没到的日子登不了：加班记的是已经发生的事",
    "past_window": "只能登记今天和昨天的加班：更早的请找店长补录",
    "invalid_half_hours": "时长要按 0.5 小时加减：请用 + / − 按钮调整",
    "zero_half_hours": "时长不能是 0：请用 + / − 按钮调出这笔的时长",
    # 带一个 `{}`：服务层把上限的小时数放在 `args[0]`（别把 12 写死在文案里）。
    "hours_too_large": "单笔最多 {} 小时：超过了请分成两笔",
    "missing_reason": "请写一句事由：这笔加班 / 补钟是干什么的",
    # 带一个 `{}`：服务层把上限（`MAX_REASON`）放在 `args[0]`（同事由长度的口径）。
    "reason_too_long": "事由最多 {} 个字：请缩短一点再提交",
    "invalid_month": "月份格式应该是 YYYY-MM",
    # 「不存在」也用在「这条是别人的」上：不告诉员工「它在，但不是你的」。
    "unknown_employee": "找不到这个员工：刷新一下再选",
    "unknown_entry": "这条登记不存在：可能已经被撤回，刷新看看",
    "entry_not_pending": "这条登记已经处理过了：刷新看看它现在到哪一步",
    # 逐条点名迁移文件（同排班的 `not_migrated`）：503 要说清该应用哪一个脚本。
    # 票 03 / 05 各自加表时，也在这一句里补上自己的文件名。
    "not_migrated": (
        "加班登记表还没建好：请在 Admin「系统更新 → 数据库迁移」应用 "
        "migrations/pg/0021_overtime_entries.sql，然后刷新本页"
    ),
}

_ERROR_STATUS = {
    # 找不到的那一行：这里只有「这条登记」。撤别人的登记也走它。
    "unknown_entry": 404,
    "unknown_employee": 404,
    # `not_migrated` 不是「参数写错了」，是这台机器还没升级完 —— 503 比 400 诚实。
    "not_migrated": 503,
}


class EntryRequest(BaseModel):
    """提一笔登记。三个字段都给了默认值：缺字段落到服务层那道闸上、回一句中文 400，
    而不是 pydantic 的英文 422（同排班 `LeaveRequest` 的理由）。

    `half_hours` 收 float 是为了让「1.25 小时不是 0.5 的整数倍」也说中文（服务层的
    `_require_half_hours` 认得出来它）；前端传的就是半小时的整数倍。
    """

    entry_date: Optional[str] = None
    half_hours: Optional[float] = None
    reason: Optional[str] = None


def _bad_request(exc: OvertimeError) -> HTTPException:
    detail = _ERROR_DETAILS.get(exc.code, "加班登记参数不合法")
    if "{}" in detail:
        # `OvertimeError(code, message)` 的 message 放在 args[0]（默认等于 code），
        # 服务层用它带「哪里错」的细节（上限是多少、几个字）。
        # 用 `str.replace` 而不是 `str.format`：模板里多一个花括号、或哪天文案里出现
        # 字面 `{...}`，`format` 会抛 KeyError/IndexError/ValueError 变成 500。
        raw = exc.args[0] if exc.args else ""
        if not raw or raw == exc.code:
            logger.warning("加班登记错误缺细节，用「？」占位: code=%s", exc.code)
        detail = detail.replace("{}", raw if raw and raw != exc.code else "？")
    logger.info("加班登记请求被拒: code=%s", exc.code)
    return HTTPException(status_code=_ERROR_STATUS.get(exc.code, 400), detail=detail)


def _entry_actor(employee: dict) -> EntryActor:
    """员工会话 → 台账层的 actor。

    票 01 的人**没有补录能力**：窗口就是今天与昨天（判据在服务层的 `submit()` 里）。
    票 04 把 `overtime` 能力键接进来时，这里多一个 `can_backfill=has_cap(...)`，
    窗口那条分支一个字都不用改。
    """
    return EntryActor(employee_id=int(employee["id"]))


def _who(employee: dict) -> dict:
    return {"id": employee["id"], "name": employee["name"]}


@router.get("/me")
async def my_entries(
    month: Optional[str] = Query(None, description="YYYY-MM；不填就是本月"),
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """我的登记（新的在前）+ 某个月的净时长（员工端那一页读的就是它）。

    `month` 只决定月度卡看哪个月，决定不了看谁 —— 同排班 `my_month` 的口径。
    上限随这一条下去（`max_reason` / `max_half_hours`）：前端拿它设输入与文案，
    不再写死第二份 50 与 12（同 `/me/requests` 的 `max_request_note`）。
    """
    ledger = OvertimeLedger(db)
    try:
        data = await ledger.list_mine(employee["id"], month)
    except OvertimeError as exc:
        raise _bad_request(exc) from exc
    return {
        "employee": _who(employee),
        "max_reason": MAX_REASON,
        "max_half_hours": HALF_HOURS_MAX,
        **data,
    }


@router.post("/me")
async def submit_entry(
    payload: EntryRequest,
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """提一笔加班（正数）或补钟（负数）。落「待审批」——票 02 才有人点头。"""
    ledger = OvertimeLedger(db)
    try:
        entry = await ledger.submit(
            _entry_actor(employee),
            payload.entry_date,
            payload.half_hours,
            payload.reason,
        )
    except OvertimeError as exc:
        raise _bad_request(exc) from exc
    return {"employee": _who(employee), "entry": entry}


@router.delete("/me/{entry_id}")
async def cancel_entry(
    entry_id: int,
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """撤回自己**还没被审批**的那一笔。台账是流水：撤回不是删除。

    别人的登记一律 404（`unknown_entry`）：接口不区分「不存在」与「不是你的」。
    """
    ledger = OvertimeLedger(db)
    try:
        entry = await ledger.cancel(employee["id"], entry_id)
    except OvertimeError as exc:
        raise _bad_request(exc) from exc
    return {"employee": _who(employee), "entry": entry}
