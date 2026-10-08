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

from api.security import require_session, require_staff_session
from database import get_db
from services.identity import EmployeeAccounts
from services.overtime.ledger import (
    HALF_HOURS_MAX,
    MAX_REASON,
    MAX_REJECT_REASON,
    EntryActor,
    OvertimeError,
    OvertimeLedger,
)
from services.realtime.hub import realtime_hub

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
    # 驳回的理由是给员工看的那句话：没有它，员工只会来问人（票 02 的验收 1）。
    "missing_reject_reason": "驳回要写一句理由：员工要知道为什么不算",
    # 带一个 `{}`：服务层把上限（`MAX_REJECT_REASON`）放在 `args[0]`。
    "reject_reason_too_long": "驳回理由最多 {} 个字：请缩短一点再提交",
    # 作废撤销的是「已经点过的头」：还没批的不叫作废，那叫「先别急」。
    "entry_not_approved": "只有已批准的登记能作废：这一笔还没批，或已经被作废了",
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


# 上限随每条列表响应一起下去（员工端与管理端都带）：前端拿它设输入与文案，不再写死
# 第二份 50 / 12 / 100（同 `/me/requests` 的 `max_request_note`）。服务层是唯一出处。
_LIMITS = {
    "max_reason": MAX_REASON,
    "max_half_hours": HALF_HOURS_MAX,
    "max_reject_reason": MAX_REJECT_REASON,
}


async def _overtime_nudge(reason: str, *employee_ids: Optional[int]) -> None:
    """台账动了，叫相关的人自己来拉一次（nudge 不带数据，见 `services/realtime/hub.py`）。

    scope 里带 `employee_id`：员工连接只会收到**自己**那条（hub 的 `_staff_owns_scope`），
    管理端那侧不受限 —— 它本来就要看全店。一人一条，不为「多人」在 hub 里开特例
    （同 `api/scheduling.py` 的 `_scheduling_nudge`）：一笔登记只牵一个人，所以这里
    通常只有一条；写成可变参数是为了跟排班那条同一个形状，读的人不用想「为什么它不一样」。

    读路径不广播：列表被谁刷新都不改变数据形状。
    """
    for employee_id in {int(item) for item in employee_ids if item is not None}:
        await realtime_hub.broadcast_nudge(
            "overtime", {"reason": reason, "employee_id": employee_id}
        )


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
        **_LIMITS,
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
    # 提上去了：他自己那台设备（另一部手机 / 另一个标签页）与管理端的待办都该刷新。
    await _overtime_nudge("entry_submitted", entry["employee_id"])
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
    # 撤回了：管理端的待办里那一行自己就不在了，但要叫它重读一次才看得见。
    await _overtime_nudge("entry_cancelled", entry["employee_id"])
    return {"employee": _who(employee), "entry": entry}


# ── 管理端（票 02）：待办队列、批 / 驳 / 作废、代员工补录 ──────────────────────
#
# 两扇门各认各的 cookie：上面那三条只认员工会话，下面这些只认管理端会话。员工端
# **没有**审批入口（那是票 04 的店长面，走能力键 + 员工手机端），所以这一票也不给
# 员工门加任何管理端点 —— 「谁能批」在这份代码里是路径级别的，不是页面级别的。


class BackfillRequest(BaseModel):
    """超管替员工补一笔。

    `employee_id` 必填：超管没有员工号，「这笔算谁的」不能猜（缺了会落到服务层的
    `unknown_employee` 上，回一句中文 400）。日期不设窗口 —— 补录的意义就在这儿。
    """

    employee_id: Optional[int] = None
    entry_date: Optional[str] = None
    half_hours: Optional[float] = None
    reason: Optional[str] = None


class RejectRequest(BaseModel):
    """驳回一笔。`reason` 必填 —— 缺了落到服务层的 `missing_reject_reason` 上。

    给默认值而不是必填字段：pydantic 的缺失是英文 422，而这里要的是那句中文 400
    （同 `EntryRequest` 的理由）。
    """

    reason: Optional[str] = None


@router.get("/admin/employees")
async def admin_employees(
    db=Depends(get_db), _: str = Depends(require_session)
) -> dict:
    """代录时要选人：只回员工号、姓名、停用与否。

    **不借排班或花名册那两个端点** —— 它们各自带一堆这一页用不到的东西（排班规则、
    身份证号、底薪）；加班页只需要「选谁」。这一条也不读 `overtime_entries`，所以
    缺 0021 那段时间里它照样能用：代录表单不该跟着那张表一起 503。

    停用的人也在名单里（历史登记可能还要补在他头上），由前端标出来 —— 这一层不替
    业务决定「停用的人还能不能被补录」。
    """
    roster = await EmployeeAccounts(db).list_roster()
    return {
        "employees": [
            {
                "id": int(employee["id"]),
                "name": employee.get("name") or "",
                "disabled": bool(employee.get("disabled")),
            }
            for employee in roster
        ]
    }


@router.get("/admin/pending")
async def admin_pending(
    db=Depends(get_db), _: str = Depends(require_session)
) -> dict:
    """全店**等审批**的队列，旧的在前（先来先处理，同排班 `inbox` 的口径）。

    每条带 `employee_name`：队列上要写成「李四 · 9/24 · +6.5 小时 · 中秋加班」。
    """
    ledger = OvertimeLedger(db)
    try:
        return {**await ledger.list_pending(), **_LIMITS}
    except OvertimeError as exc:
        raise _bad_request(exc) from exc


@router.get("/admin/entries")
async def admin_entries(
    month: Optional[str] = Query(None, description="YYYY-MM；按登记日期所在自然月筛"),
    employee_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """全店台账，**新的在前**。

    管理端拿它做两件事：找一条**已经批过**的来作废（待办队列里只有待审批的），
    以及代录之后回头确认写对了没有。
    """
    ledger = OvertimeLedger(db)
    try:
        data = await ledger.list_entries(
            month=month, employee_id=employee_id, status=status
        )
    except OvertimeError as exc:
        raise _bad_request(exc) from exc
    return {**data, **_LIMITS}


@router.post("/admin/entries")
async def admin_backfill(
    payload: BackfillRequest,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """代员工补录一笔（任意过去日期）。

    actor 是 `EntryActor()`：`created_by` 写成 `super`，台账上分得清「员工自己提的」
    与「超管替他补的」（票 02 的验收 4）。
    """
    ledger = OvertimeLedger(db)
    try:
        entry = await ledger.submit(
            EntryActor(),
            payload.entry_date,
            payload.half_hours,
            payload.reason,
            target_employee_id=payload.employee_id,
        )
    except OvertimeError as exc:
        raise _bad_request(exc) from exc
    # 代录也是一笔新登记：那个人自己手机上要出现它，管理端列表也要重读。
    await _overtime_nudge("entry_submitted", entry["employee_id"])
    return {"entry": entry}


@router.post("/admin/entries/{entry_id}/approve")
async def admin_approve(
    entry_id: int,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """批准一笔待审批的登记。状态谓词在服务层的写锁里：重复点只有第一次算数。"""
    ledger = OvertimeLedger(db)
    try:
        entry = await ledger.approve(EntryActor(), entry_id)
    except OvertimeError as exc:
        raise _bad_request(exc) from exc
    await _overtime_nudge("entry_approved", entry["employee_id"])
    return {"entry": entry}


@router.post("/admin/entries/{entry_id}/reject")
async def admin_reject(
    entry_id: int,
    payload: RejectRequest,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """驳回一笔待审批的登记 —— **必须写理由**，理由会出现在员工手机上。"""
    ledger = OvertimeLedger(db)
    try:
        entry = await ledger.reject(EntryActor(), entry_id, payload.reason)
    except OvertimeError as exc:
        raise _bad_request(exc) from exc
    # 驳回的理由就在这条登记上：员工端重读一次才看得到为什么。
    await _overtime_nudge("entry_rejected", entry["employee_id"])
    return {"entry": entry}


@router.post("/admin/entries/{entry_id}/void")
async def admin_void(
    entry_id: int,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """作废一笔**已批准**的登记：批准之后员工动不了它，只有这里能救回一个批错的。

    作废不是删除 —— 那一行还在（状态 `voided`），员工手机上看得见它被作废过。
    """
    ledger = OvertimeLedger(db)
    try:
        entry = await ledger.void(EntryActor(), entry_id)
    except OvertimeError as exc:
        raise _bad_request(exc) from exc
    # 作废也要让员工知道：他手机上那一笔从「已批准」变成了「已作废」。
    await _overtime_nudge("entry_voided", entry["employee_id"])
    return {"entry": entry}
