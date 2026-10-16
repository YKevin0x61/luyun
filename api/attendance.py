#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""考勤表的 HTTP 适配层。口径在 `services/attendance/store.py`，这里只管翻译。

票 01 只有**一扇门**：管理端会话（`require_session`）。考勤表是店长的表 —— 里面有
全店人的底薪与假期，员工那一档连页面都进不去（页面清单里 `audience: admin`）。

票 06 给这扇门加了**第一个写动作**：逐日改格（把某天改成休、或换成另一个启用中的
班次）。它**没有第二套写排班的方式** —— 走的是排班服务层的单日覆盖
（`SchedulingStore.set_day_override`，结果行来源 `override`），这里只做三件事：
挑出那扇门、把那层的错误翻成人话、按排班既有惯例（`api/scheduling.py` 的
`_scheduling_nudge`）广播**一人一条**的排班通知。手填列在票 05、封账在票 04。
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
from services.attendance import MANUAL_FIELDS, AttendanceError, AttendanceStore
from services.scheduling.store import SchedulingError, SchedulingStore

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
    # 手填列（票 05）：四列一项都没点名。这不是「参数写错了」那么难懂 —— 页面上那颗
    # 保存按钮本来就是四项一起发，走到这里说明请求是手搓的（或表单全空没拿住）。
    "nothing_to_write": "这次没有任何一项要存：把要改的那几项填上再存一次",
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


def _bad_request(exc: AttendanceError) -> HTTPException:
    detail = _ERROR_DETAILS.get(exc.code, "考勤参数不合法")
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
    """逐日改格的输入：改成休（`is_rest`），或者换成某个班次（`shift_id`）。

    两项都有默认值，所以缺字段不会变成 pydantic 的英文 422：什么都不给会落到服务层
    那道闸上，回一句中文。
    """

    is_rest: bool = False
    shift_id: Optional[int] = None


class ManualColumnsRequest(BaseModel):
    """四个**手填列**（票 05）的输入：本月班次 / 是否住宿 / 借调门店 / 是否离职。

    系统里没有这几项（花名册里压根没有「是否住宿 / 借调门店 / 是否离职」这几个字段），
    所以由超管在卡片上填一次、存在月度考勤台账当月那一行上。

    **`None` 是「清空」，缺字段是「不碰」** —— 两者在 JSON 里都是「没给值」之外的两件事，
    但 `Optional[...] = None` 分不开它们，所以这一层用 `model_fields_set` 拿「这次点名了
    哪几列」，只把那几列交给服务层。清空存 NULL，与从没填过同义：页面上显示为空，
    「本月班次」那一格退回当月众数。
    """

    shift_name: Optional[str] = None
    lodging: Optional[str] = None
    borrowed_store: Optional[str] = None
    resigned: Optional[str] = None


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


@router.put("/sheet/{employee_id}/manual")
async def set_attendance_manual_columns(
    employee_id: int,
    payload: ManualColumnsRequest,
    db=Depends(get_db),
    _session: str = Depends(require_session),
) -> dict:
    """把某人**本月的四个手填列**存进月度考勤台账（票 05）。

    写在本月那一行上（`UNIQUE(租户, 员工, 月份)`，upsert）：这一次点名了哪几列就只写哪几
    列，没点名的一列都不碰 —— 票 04 写的 `closing_balance` / `sealed_at` 也在这同一行上，
    谁也不整行覆盖谁。清空某一项存 NULL（与从没填过同义）。

    **这里没有任何 nudge**：这几个值只进卡片与（票 08 的）导出件，员工手机上不显示它们，
    所以没有第二个端要重拉。它们也**不写回员工档案** —— 档案里没有这几个字段。
    """
    store = AttendanceStore(db)
    # `model_fields_set` 是「这一次点名了哪几列」的判据：缺字段 = 不碰，显式 `null` = 清空。
    values = {
        field: getattr(payload, field)
        for field in payload.model_fields_set
        if field in MANUAL_FIELDS
    }
    try:
        return await store.set_manual_columns(employee_id, values)
    except AttendanceError as exc:
        raise _bad_request(exc) from exc


@router.put("/sheet/{employee_id}/{business_date}")
async def set_attendance_day(
    employee_id: int,
    business_date: str,
    payload: DayEditRequest,
    db=Depends(get_db),
    _session: str = Depends(require_session),
) -> dict:
    """把某人某一天改成休、或换成另一个启用中的班次 —— **只动那一天**（票 06）。

    复用排班的单日覆盖（`SchedulingStore.set_day_override`）：不动他的轮转规则、
    不动同一个月的别的日子、不动下个月，超出自可补记窗口的日子照旧拒（`past_day`）。
    那天还有**没批的**换班 / 请假申请时**拒绝**（409）且一个字都不写 —— 店长不该在
    这一页把员工刚提的请求悄悄冲掉。

    写完按排班既有惯例广播：`_scheduling_nudge` 一人一条、scope 带他自己的
    `employee_id`（员工连接只收得到与自己有关的那条，见 `services/realtime/hub.py`）。
    nudge 不带数据，员工手机收到后自己重拉 —— 与月历上改一天完全同一条链路。
    """
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
