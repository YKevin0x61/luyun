#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""考勤表的 HTTP 适配层。口径在 `services/attendance/store.py`，这里只管翻译。

票 01 只有**一扇门**：管理端会话（`require_session`）。考勤表是店长的表 —— 里面有
全店人的底薪与假期，员工那一档连页面都进不去（页面清单里 `audience: admin`）。

票 06 给这扇门加了**第一个写动作**：逐日改格（把某天改成休、或换成另一个启用中的
班次）。它**没有第二套写排班的方式** —— 走的是排班服务层的单日覆盖
（`SchedulingStore.set_day_override`，结果行来源 `override`），这里只做三件事：
挑出那扇门、把那层的错误翻成人话、按排班既有惯例（`api/scheduling.py` 的
`_scheduling_nudge`）广播**一人一条**的排班通知。手填列在票 05。

票 04 加的是**封账上月**（`POST /api/attendance/seal`）：超级管理员点一次，上一个自然月
的存假结余就落进月度考勤台账（`services/attendance/store.py` 的 `AttendanceStore.seal`）。
只封已经过完的月份，封过的月不再封；这一条**不广播 nudge** —— 它改的是管理端那张表自己
的账，员工手机上没有任何东西跟着变。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.scheduling import _bad_request as _scheduling_bad_request
from api.scheduling import _scheduling_nudge
from api.security import require_session
from database import get_db
from services.attendance import AttendanceError, AttendanceStore
from services.scheduling.store import SchedulingError, SchedulingStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/attendance", tags=["考勤"])

# 服务层的 `AttendanceError.code` 是稳定标识，不是给店长看的文案。这里把每个 code
# 翻成一句能照着改的话 —— 逐条点名该应用哪个迁移（同排班的 `not_migrated`）。
_ERROR_DETAILS = {
    "not_migrated": (
        "考勤表要读的表还没建好：请在 Admin「系统更新 → 数据库迁移」应用 "
        "migrations/pg/0005_scheduling.sql、migrations/pg/0021_overtime_entries.sql、"
        "migrations/pg/0026_statutory_holidays.sql、"
        "migrations/pg/0027_attendance_ledger.sql 与 "
        "migrations/pg/0028_staff_assignments_half_rest.sql，然后刷新本页"
    ),
    # 逐日写口（票 07 的「休半天」）那三句。带 `{}` 的那几句由 `_bad_request` 填细节。
    "invalid_business_date": "日期格式应该是 YYYY-MM-DD",
    "out_of_month": "考勤表只算当月：只能改 {} 所在那个月的格子",
    "half_rest_needs_shift": "那天没有班次（休 / 还没排到），标不上「休半天」：先给那天排一个班次",
}

_ERROR_STATUS = {
    # `not_migrated` 不是「参数写错了」，是这台机器还没升级完 —— 503 比 400 诚实。
    "not_migrated": 503,
}

# 逐日改格（票 06）的拒绝理由：单日覆盖那几条 + 这一页多出来的那一条。文案跟排班
# 月历**刻意不一样**：那边是「在月历上刷格子」，这边是「在考勤表卡片上点某一天」，
# 说「月历」会让店长去找一个他不在的页面。带 `{}` 的那几条由 `_write_error` 填细节。
_WRITE_DETAILS = {
    "invalid_business_date": "日期格式应该是 YYYY-MM-DD",
    "unknown_employee": "找不到这位员工：刷新页面重新选一次",
    "unknown_shift": "班次不存在或已停用：请重新选一个班次",
    "unknown_zone": "工作区不存在：请先在卫生的工作区页面新建，或刷新本页",
    "past_day": "超出可补记窗口：{} 之前的日子不再改写",
    "beyond_window": "这天还没排到：排班只铺到 {}，等它进窗口再改",
    "rest_with_details": "「改成休」的那天不能再带班次或工作区：请把这两项留空",
    # 这一页多出来的那条闸（票 06 验收 3）：口头说清楚「谁在等、等什么」，
    # 让店长知道该先去待办里把那条申请处理掉。`{}` 由 `_write_error` 填。
    "pending_request": "{}在等店长批：先把它批掉或驳回，再改这一天",
    # **复用同一个 code 的两句**：那两条是 `set_day_override` 自己抛的
    # `SchedulingError`，但这句话是给在考勤表页点格子的店长听的，不是给月历上的。
    "missing_shift": "请选一个班次；要改成休就用「改成休」",
    # 只可能出现在「那天本来在窗口里、刚被展平」的竞态上，照实说。
    "not_migrated": (
        "排班表还没建好：请在 Admin「系统更新 → 数据库迁移」应用 "
        "migrations/pg/0005_scheduling.sql、0006_scheduling_zone_defaults.sql、"
        "0007_scheduling_overrides.sql、0008_scheduling_requests.sql、"
        "0009_scheduling_swap.sql 与 0010_shift_duty_slot.sql，然后刷新本页"
    ),
}

# 申请那两种说法（`pending_request` 的 `{}` 就是它）。
_PENDING_WORDS = {"leave": "请假", "swap": "换班"}

# 封账（票 04）的拒绝理由。带 `{}` 的那两条由 `_seal_error` 填**月份**：店长要知道
# 自己点的到底是哪一个月，以及它为什么封不了。`not_migrated` 复用上面那一份文案
# （缺的还是同一个 0027），所以这里只写封账自己的三条。
_SEAL_DETAILS = {
    "invalid_month": "月份格式应该是 YYYY-MM",
    "month_not_finished": "封账只能封已经过完的月份：{} 还没过完，等它过完再封",
    "already_sealed": "{} 已经封过账了：封过的月不再重算，也不用再封一次",
}

# 封过的月 = 这次的写入与台账里已经定稿的那一行**冲突**：409 比 400 诚实
# （同逐日改格的 `pending_request`）。
_SEAL_STATUS = {
    "month_not_finished": 400,
    "invalid_month": 400,
    "already_sealed": 409,
}


def _seal_error(exc: AttendanceError) -> HTTPException:
    """把封账的拒绝翻成一句能看懂的中文（票 04 验收 2/3）。

    `{}` 由服务层放在 `args[0]` 的月份填，用 `replace` 而不是 `format`（同
    `api/scheduling.py` 的 `_bad_request`）。没写文案的 code（`not_migrated`）退回
    考勤那一层的通用出口 —— 缺表要说的是「去应用哪个迁移」，那句话只有一份。
    """
    raw = exc.args[0] if exc.args else ""
    detail = _SEAL_DETAILS.get(exc.code) or _ERROR_DETAILS.get(exc.code, "这次封账没成")
    if "{}" in detail:
        detail = detail.replace("{}", str(raw) if raw and raw != exc.code else "这个月")
    logger.info("封账被拒: code=%s", exc.code)
    status = _SEAL_STATUS.get(exc.code) or _ERROR_STATUS.get(exc.code, 400)
    return HTTPException(status_code=status, detail=detail)


def _bad_request(exc: AttendanceError) -> HTTPException:
    detail = _ERROR_DETAILS.get(exc.code, "考勤参数不合法")
    # 带 `{}` 的模板由服务层放在 `args[0]` 的细节填（同 `_write_error`：用 `replace`
    # 而不是 `format`，模板里出现一个字面花括号也不会变成 500）。
    if "{}" in detail:
        raw = exc.args[0] if exc.args else ""
        detail = detail.replace("{}", str(raw) if raw else "？")
    logger.info("考勤请求被拒: code=%s", exc.code)
    return HTTPException(status_code=_ERROR_STATUS.get(exc.code, 400), detail=detail)


def _write_error(exc: SchedulingError) -> HTTPException:
    """把排班那层的错误翻成考勤表页上的一句话。

    带 `{}` 的模板由服务层放在 `args[0]` 的细节填（同 `api/scheduling.py` 的
    `_bad_request`：用 `replace` 而不是 `format`，模板里出现一个字面花括号也不会变成
    500）。没写文案的 code 不硬翻成「参数不合法」—— 那句对店长毫无用处，所以退回
    排班那一层的通用出口，至少口径与月历一致。
    """
    if exc.code not in _WRITE_DETAILS:
        return _scheduling_bad_request(exc)
    raw = exc.args[0] if exc.args else ""
    if raw == "pending_request":  # pragma: no cover - 服务层不该这么传
        raw = ""
    detail = _WRITE_DETAILS[exc.code]
    if "{}" in detail:
        if exc.code == "pending_request":
            filled = _PENDING_WORDS.get(str(raw), "申请")
        else:
            filled = str(raw) if raw and raw != exc.code else "？"
        detail = detail.replace("{}", filled)
    logger.info("逐日改格被拒: code=%s", exc.code)
    # 那天还有未决申请 = 这次的写入与另一条请求**冲突**：409 比 400 诚实
    # （`spec.md` 的 Testing Decisions 也点的是 409）。
    status = 409 if exc.code == "pending_request" else 400
    if exc.code == "not_migrated":
        status = 503
    return HTTPException(status_code=status, detail=detail)


class DayEditRequest(BaseModel):
    """逐日改格的输入：改成休（`is_rest`），换成某个班次（`shift_id`），或者标「休半天」
    （`half_rest`）。

    三项都有默认值，所以缺字段不会变成 pydantic 的英文 422：什么都不给会落到服务层
    那道闸上，回一句中文。前两项是票 06 的「改这一天」，`half_rest` 是票 07 的**半天休**
    —— 它是一次**单独**的动作（ADR 0114）：只改结果行上那个标志，班次、工作区、来源都
    不动，所以不能与 `is_rest` / `shift_id` 一起发（两者一起发是 400，不是「猜一个」）。
    """

    is_rest: bool = False
    shift_id: Optional[int] = None
    half_rest: Optional[bool] = None


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


class SealRequest(BaseModel):
    """封账的输入：什么都不给 = 封**上一个自然月**（页面上那颗按钮打的就是这一条）。

    `month` 是给「点名封某一个月」的调用方用的；当月与未来的月一律被拒（还没过完），
    已经封过的月也一律被拒。字段有默认值，所以缺字段不会变成 pydantic 的英文 422。
    """

    month: Optional[str] = None


@router.post("/seal")
async def seal_month(
    payload: Optional[SealRequest] = None,
    db=Depends(get_db),
    _session: str = Depends(require_session),
) -> dict:
    """把**已经过完的**某个自然月的存假结余写进月度考勤台账并定稿（票 04 / ADR 0113）。

    不给 `month` 就是上一个自然月 —— 页面上那颗「封账上月」按钮的全部输入就这一条。
    写下去的那一行（`closing_balance` + `sealed_at`）从此就是当月卡片上「上月存假」的
    来源：之后再改上月的排班，封下的结余不跟着漂。

    只封已经过完的月份（当月与未来 400）、封过的月不再封（409），那些理由都翻成中文
    由 `_seal_error` 给。缺表（0027 没应用）照旧是一个点名脚本的 503。

    **不广播 nudge**：封的是管理端这张表自己的账，员工手机上的班与假一个字都没变。
    """
    store = AttendanceStore(db)
    try:
        return await store.seal(payload.month if payload else None)
    except AttendanceError as exc:
        raise _seal_error(exc) from exc


@router.put("/sheet/{employee_id}/{business_date}")
async def set_attendance_day(
    employee_id: int,
    business_date: str,
    payload: DayEditRequest,
    db=Depends(get_db),
    _session: str = Depends(require_session),
) -> dict:
    """把某人某一天改成休、换成另一个启用中的班次（票 06），或标「休半天」（票 07）。

    **改这一天**（票 06）复用排班的单日覆盖（`SchedulingStore.set_day_override`）：不动
    他的轮转规则、不动同一个月的别的日子、不动下个月，超出自可补记窗口的日子照旧拒
    （`past_day`）。那天还有**没批的**换班 / 请假申请时**拒绝**（409）且一个字都不写 ——
    店长不该在这一页把员工刚提的请求悄悄冲掉。

    **「休半天」**（票 07，ADR 0114）走的是另一条路（`AttendanceStore.set_half_rest`）：
    它只改结果行上的那个标志，**不是**一次单日覆盖 —— 那天仍然挂本来那个班次、仍然只有
    一行、`source` 也不变（所以它没有「改完这天就跟规则不一样了」那个含义）。这是**全仓
    唯一**能设置它的地方：排班月历 / 周表 / 员工端只读地显示它。

    两条路写完都按排班既有惯例广播：`_scheduling_nudge` 一人一条、scope 带他自己的
    `employee_id`（员工连接只收得到与自己有关的那条，见 `services/realtime/hub.py`）。
    nudge 不带数据，员工手机收到后自己重拉 —— 与月历上改一天完全同一条链路。
    """
    if payload.half_rest is not None:
        return await _set_half_rest(
            db, employee_id, business_date, bool(payload.half_rest), payload
        )
    store = SchedulingStore(db)
    try:
        result = await store.set_day_override(
            employee_id,
            business_date,
            is_rest=payload.is_rest,
            shift_id=payload.shift_id,
        )
    except SchedulingError as exc:
        raise _write_error(exc) from exc
    # 被临时调走（或被改成休）的人手机上的「今天」页要跟着变 —— 跟月历那条 PUT 一样。
    await _scheduling_nudge("override_set", employee_id)
    return result


async def _set_half_rest(
    db, employee_id: int, business_date: str, half_rest: bool, payload: DayEditRequest
) -> dict:
    """「休半天」那一支：先挡住「一次改两件事」，再交给服务层那扇唯一的写口。

    `is_rest` 与 `shift_id` 与 `half_rest` 一起发是 400 —— 「改成休，但只休半天」不是
    一句说得通的话（那天到底上不上班？），服务层也不该替调用方猜。
    """
    if payload.is_rest or payload.shift_id is not None:
        raise HTTPException(
            status_code=400,
            detail="「休半天」是一次单独的动作：不要再带班次或「改成休」；"
            "要换班次 / 改成休就单独发一次",
        )
    store = AttendanceStore(db)
    try:
        result = await store.set_half_rest(employee_id, business_date, half_rest)
    except AttendanceError as exc:
        raise _bad_request(exc) from exc
    # 员工手机上那一屏要跟着变（班次没变，但那天从「上班」变成了「上半天」）。
    await _scheduling_nudge("half_rest_set", employee_id)
    return result
