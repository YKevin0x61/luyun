#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加班与补钟的 HTTP 适配层。台账在 `services/overtime/ledger.py`，这里只管翻译。

票 01 只开**员工自己那一面**（`/api/overtime/me`）：提交、看自己的记录、撤回。
管理端的审批与统计在票 02，店长在手机上的审批面在票 04 —— 都不在这里提前开。
三组端点的身份门由 `api.workbench_boundary` 的动作声明给出（员工 `/me`、员工 +
「人事管理」能力 `/review`、管理端 `/admin`），**员工端接口上也没有 `employee_id`**：
员工会话读得到的、动得了的只有自己那几笔。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.workbench_boundary import (
    WorkbenchActor,
    WorkbenchErrors,
    admin_session_actor,
    require_action,
    staff_actor,
)
from database import get_db
from services.identity import EmployeeAccounts
from services.identity.capabilities import CAP_SCHEDULE
from services.overtime.ledger import (
    HALF_HOURS_MAX,
    MAX_REASON,
    MAX_REJECT_REASON,
    EntryActor,
    OvertimeError,
    OvertimeLedger,
)
from services.realtime.workbench import nudge_overtime as _overtime_nudge
from services.wecom_request_notice import (
    notify_overtime_decision,
    notify_overtime_deduction,
    notify_overtime_deduction_decision,
    notify_overtime_entry,
)

router = APIRouter(prefix="/api/overtime", tags=["加班与补钟"])

# 服务层的 `OvertimeError.code` 是稳定标识，不是给员工看的文案。这里把每个 code 翻成
# 一句能照着改的话 —— 「参数不合法」那种一句话会把「前天补不了」和「时长打错了」
# 说成同一件事，而这两件事员工要做的事完全不一样。
_ERROR_DETAILS = {
    "invalid_date": "日期格式应该是 YYYY-MM-DD：请重新选一天",
    # 未来与过去分开说：一个是「这事还没发生」，一个是「窗口过了、得找店长」。
    "future_day": "还没到的日子登不了：加班记的是已经发生的事",
    "past_window": "只能登记今天和昨天的加班：更早的请找管理员补录",
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
    # 票 03：金额要用底薪算，底薪没补就批不了 —— 说清去哪儿补，别只说「参数不合法」。
    "salary_missing": "这位同事的底薪还没补：先到花名册补上底薪，再回来批这一笔",
    # 提交人不能批自己的登记（同卫生的 `cannot_self_accept` 口径）：钱与留痕都是自己
    # 给自己。判据看「这一笔算谁的」，所以代别人补录的那一笔仍然批得动。
    "cannot_self_accept": "交这笔的人不能自己批：请让另一位有「人事管理」权限的管理员来处理",
    # 逐条点名迁移文件（同排班的 `not_migrated`）：503 要说清该应用哪一个脚本。
    # 票 05 加表时也在这句里补上自己的文件名。
    "not_migrated": (
        "加班登记表还没建好：请在 Admin「系统更新 → 数据库迁移」应用 "
        "migrations/pg/0021_overtime_entries.sql、"
        "migrations/pg/0022_overtime_salary_snapshots.sql 与 "
        "migrations/pg/0042_overtime_leave_deductions.sql，然后刷新本页"
    ),
    # ── 补钟用存假抵扣（ADR 0124）──────────────────────────────────────────
    "invalid_days": "抵扣天数要填整天：1 天起，不能填半天或 0",
    # 带一个 `{}`：服务层把上限（`MAX_DEDUCTION_DAYS`）放在 `args[0]`。
    "days_too_large": "一次最多抵 {} 天：超过了请分几次申请",
    "deduction_exceeds_makeup": "这个月能抵的补钟不够：最多还能抵 {} 天",
    "deduction_exceeds_leave": "存假不够扣：最多还能抵 {} 天",
    "month_sealed": "这个月的账已经封了：封过的月份不能再抵扣，补钟只能按净时长算钱",
    "employee_resigned": "这位同事已经离职：离职后的账请由超级管理员在考勤表上结",
    "unknown_deduction": "这张抵扣单不存在：可能已经被撤回或作废，刷新看看",
    "deduction_not_pending": "这张抵扣单已经处理过了：刷新看看它现在到哪一步",
    "deduction_not_approved": "只有已批准的抵扣单能作废：这一张还没批，或已经被作废了",
    "makeup_in_use": "这笔补钟里有已经抵扣出去的小时：请先作废对应的抵扣单，再作废补钟",
}

_ERROR_STATUS = {
    # 找不到的那一行：这里只有「这条登记」。撤别人的登记也走它。
    "unknown_entry": 404,
    "unknown_employee": 404,
    "unknown_deduction": 404,
    # 「你没这个权限」不是「参数写错了」：403 与卫生的 `cannot_self_accept` 同一个口径。
    "cannot_self_accept": 403,
    # 那个月的账封了 = 状态冲突，不是参数错：409 让前端能单独特判（封账提示）。
    "month_sealed": 409,
    # `not_migrated` 不是「参数写错了」，是这台机器还没升级完 —— 503 比 400 诚实。
    "not_migrated": 503,
}

# 24 个 `raise` 出口共用这一份翻译：状态码查 `_ERROR_STATUS`，未登记的 code 默认 400，
# 文案缺省句与旧出口逐字相同；映射出来的响应带 `X-Error-Code` 头。
_OVERTIME_ERRORS = WorkbenchErrors(
    domain="加班登记请求",
    texts=_ERROR_DETAILS,
    status_overrides=_ERROR_STATUS,
    default_text="加班登记参数不合法",
)


class EntryRequest(BaseModel):
    """提一笔登记。三个字段都给了默认值：缺字段落到服务层那道闸上、回一句中文 400，
    而不是 pydantic 的英文 422（同排班 `LeaveRequest` 的理由）。

    `half_hours` 收 float 是为了让「1.25 小时不是 0.5 的整数倍」也说中文（服务层的
    `_require_half_hours` 认得出来它）；前端传的就是半小时的整数倍。
    """

    entry_date: Optional[str] = None
    half_hours: Optional[float] = None
    reason: Optional[str] = None


class DeductionRequest(BaseModel):
    """提一张「用存假抵补钟」的抵扣单（ADR 0124）。

    `month` 是**归月**（= 补钟登记月），不给就是本月；`days` 是整天数。两个字段都给默认值
    的理由同 `EntryRequest`：缺了落到服务层的 `invalid_month` / `invalid_days` 上，回一句
    中文 400，而不是 pydantic 的英文 422。
    """

    month: Optional[str] = None
    days: Optional[float] = None


def _who(actor: WorkbenchActor) -> dict:
    return {"id": actor.employee_id, "name": actor.name}


# 店长那一面的动作门：员工会话 + 「人事管理」能力。管理端 cookie 在这 8 条上是 401
# （店长没有管理端账号，`docs/adr/0103` / 0134 的裁定）；能力每次请求现读，收回与
# 开通一样下一次请求生效。
_overtime_review = require_action("overtime_review")


# 上限随每条列表响应一起下去（员工端与管理端都带）：前端拿它设输入与文案，不再写死
# 第二份 50 / 12 / 100（同 `/me/requests` 的 `max_request_note`）。服务层是唯一出处。
_LIMITS = {
    "max_reason": MAX_REASON,
    "max_half_hours": HALF_HOURS_MAX,
    "max_reject_reason": MAX_REJECT_REASON,
}


@router.get("/me")
async def my_entries(
    month: Optional[str] = Query(None, description="YYYY-MM；不填就是本月"),
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(staff_actor),
) -> dict:
    """我的登记（新的在前）+ 某个月的净时长（员工端那一页读的就是它）。

    `month` 只决定月度卡看哪个月，决定不了看谁 —— 同排班 `my_month` 的口径。
    上限随这一条下去（`max_reason` / `max_half_hours`）：前端拿它设输入与文案，
    不再写死第二份 50 与 12（同 `/me/requests` 的 `max_request_note`）。
    """
    ledger = OvertimeLedger(db)
    try:
        data = await ledger.list_mine(actor.employee_id, month)
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    return {
        "employee": _who(actor),
        # 员工端那一页据此决定渲不渲染「待审批」区：判据在服务端算一次，前端不把
        # caps 那套判据再写一遍（写两遍就有两个答案，改口径时必漏一处）。
        "can_review": actor.has_cap(CAP_SCHEDULE),
        **_LIMITS,
        **data,
    }


@router.post("/me")
async def submit_entry(
    payload: EntryRequest,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(staff_actor),
) -> dict:
    """提一笔加班（正数）或补钟（负数）。落「待审批」——票 02 才有人点头。"""
    ledger = OvertimeLedger(db)
    try:
        entry = await ledger.submit(
            EntryActor.from_actor(actor),
            payload.entry_date,
            payload.half_hours,
            payload.reason,
        )
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    # 提上去了：他自己那台设备（另一部手机 / 另一个标签页）与管理端的待办都该刷新。
    await _overtime_nudge("entry_submitted", entry["employee_id"])
    # 群里也知会一声（只有订阅了「加班/补钟申请」的群会发）。失败只记日志：登记已经落库，
    # 不该因为群通知发不出去而让员工看到「提交失败」。
    await notify_overtime_entry(db, entry, employee_name=actor.name)
    return {"employee": _who(actor), "entry": entry}


@router.delete("/me/{entry_id}")
async def cancel_entry(
    entry_id: int,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(staff_actor),
) -> dict:
    """撤回自己**还没被审批**的那一笔。台账是流水：撤回不是删除。

    别人的登记一律 404（`unknown_entry`）：接口不区分「不存在」与「不是你的」。
    """
    ledger = OvertimeLedger(db)
    try:
        entry = await ledger.cancel(actor.employee_id, entry_id)
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    # 撤回了：管理端的待办里那一行自己就不在了，但要叫它重读一次才看得见。
    await _overtime_nudge("entry_cancelled", entry["employee_id"])
    return {"employee": _who(actor), "entry": entry}


# ── 员工端：补钟用存假抵扣（ADR 0124）──────────────────────────────────────


@router.get("/me/deductions")
async def my_deductions(
    month: Optional[str] = Query(None, description="YYYY-MM；不填就是本月"),
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(staff_actor),
) -> dict:
    """我的抵扣单（全部历史）+ 某个月**还能抵几天**（申请弹窗的上限）。

    「可抵几天」只在服务端算一份（`deduction_capacity`）：补钟与存假两头封顶、封过账的
    月为 0。前端不把这条口径再写一遍 —— 弹出「可抵 2 天」、点下去却被拒是最糟的一种。
    """
    ledger = OvertimeLedger(db)
    try:
        target = month or ledger.today()[:7]
        return {
            "employee": _who(actor),
            "today": ledger.today(),
            "month": target,
            "deductions": (
                await ledger.list_deductions(employee_id=actor.employee_id)
            )["deductions"],
            "capacity": await ledger.deduction_capacity(actor.employee_id, target),
            "can_review": actor.has_cap(CAP_SCHEDULE),
            **_LIMITS,
        }
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc


@router.post("/me/deductions")
async def submit_deduction(
    payload: DeductionRequest,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(staff_actor),
) -> dict:
    """提一张抵扣单（待店长/超管批）。归月 = 补钟登记月，只能整天。"""
    ledger = OvertimeLedger(db)
    try:
        deduction = await ledger.submit_deduction(
            EntryActor.from_actor(actor), payload.month, payload.days
        )
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    await _overtime_nudge("deduction_submitted", deduction["employee_id"])
    # 抵扣单走的是同一条审批线（人事「加班统计」），所以也归「加班/补钟申请」这一类订阅。
    await notify_overtime_deduction(
        db, deduction, employee_name=actor.name
    )
    return {"employee": _who(actor), "deduction": deduction}


@router.delete("/me/deductions/{deduction_id}")
async def cancel_deduction(
    deduction_id: int,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(staff_actor),
) -> dict:
    """撤回自己**还没被审批**的那张抵扣单。别人的一律 404（不区分「不存在」与「不是你的」）。"""
    ledger = OvertimeLedger(db)
    try:
        deduction = await ledger.cancel_deduction(actor.employee_id, deduction_id)
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    await _overtime_nudge("deduction_cancelled", deduction["employee_id"])
    return {"employee": _who(actor), "deduction": deduction}


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
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(admin_session_actor),
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
                # 离职（2026-10-29 取代「停用」）：到今天为止算不算已离职。
                "resigned": bool(employee.get("resigned")),
                "resigned_at": employee.get("resigned_at"),
            }
            for employee in roster
        ]
    }


@router.get("/admin/pending")
async def admin_pending(
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(admin_session_actor),
) -> dict:
    """全店**等审批**的队列，旧的在前（先来先处理，同排班 `inbox` 的口径）。

    每条带 `employee_name`：队列上要写成「李四 · 9/24 · +6.5 小时 · 中秋加班」。
    """
    ledger = OvertimeLedger(db)
    try:
        entries = await ledger.list_pending()
        deductions = await ledger.list_pending_deductions()
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    return {
        **entries,
        # 抵扣单跟加班登记同一个待办队列（ADR 0124）：店长一次看完、一次处理完，
        # 不要为了「抵一天」再开第二个页面。`deduction_count` 给导航角标用。
        "deductions": deductions["deductions"],
        "deduction_count": deductions["count"],
        **_LIMITS,
    }


@router.get("/admin/entries")
async def admin_entries(
    month: Optional[str] = Query(None, description="YYYY-MM；按登记日期所在自然月筛"),
    employee_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(admin_session_actor),
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
        raise _OVERTIME_ERRORS.http(exc) from exc
    return {**data, **_LIMITS}


@router.get("/admin/month")
async def admin_month(
    month: Optional[str] = Query(None, description="YYYY-MM；不给就是本月"),
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(admin_session_actor),
) -> dict:
    """全店某个月的加班费账单（票 03）：每人一行 + 合计行。

    金额只在管理端出现 —— 员工门没有这一页，店长在手机上的审批面也没有它
    （`docs/adr/0098` 那条边界的延伸：算钱要用底薪，而底薪不下发员工端）。

    不给月份就是**本月**：`ledger.today()` 是自然日的今天，与登记窗口同一个口径。
    """
    ledger = OvertimeLedger(db)
    try:
        stats = await ledger.monthly_stats(month or ledger.today()[:7])
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    return {"stats": stats, **_LIMITS}


@router.post("/admin/entries")
async def admin_backfill(
    payload: BackfillRequest,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(admin_session_actor),
) -> dict:
    """代员工补录一笔（任意过去日期）。

    actor 是 `EntryActor()`：`created_by` 写成 `super`，台账上分得清「员工自己提的」
    与「超管替他补的」（票 02 的验收 4）。
    """
    ledger = OvertimeLedger(db)
    try:
        entry = await ledger.submit(
            EntryActor.from_actor(actor),
            payload.entry_date,
            payload.half_hours,
            payload.reason,
            target_employee_id=payload.employee_id,
        )
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    # 代录也是一笔新登记：那个人自己手机上要出现它，管理端列表也要重读。
    await _overtime_nudge("entry_submitted", entry["employee_id"])
    return {"entry": entry}


@router.post("/admin/entries/{entry_id}/approve")
async def admin_approve(
    entry_id: int,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(admin_session_actor),
) -> dict:
    """批准一笔待审批的登记。状态谓词在服务层的写锁里：重复点只有第一次算数。"""
    ledger = OvertimeLedger(db)
    try:
        entry = await ledger.approve(EntryActor.from_actor(actor), entry_id)
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    await _overtime_nudge("entry_approved", entry["employee_id"])
    # 群里也知会一声「批了」：店长勾了「加班/补钟申请」订阅，就该同时看到「谁提了」
    # 与「批没批」（2026-11-02 的审批结果通知）。
    await notify_overtime_decision(db, entry, approved=True)
    return {"entry": entry}


@router.post("/admin/entries/{entry_id}/reject")
async def admin_reject(
    entry_id: int,
    payload: RejectRequest,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(admin_session_actor),
) -> dict:
    """驳回一笔待审批的登记 —— **必须写理由**，理由会出现在员工手机上。"""
    ledger = OvertimeLedger(db)
    try:
        entry = await ledger.reject(EntryActor.from_actor(actor), entry_id, payload.reason)
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    # 驳回的理由就在这条登记上：员工端重读一次才看得到为什么。
    await _overtime_nudge("entry_rejected", entry["employee_id"])
    # 群里也带上那句理由 —— 「没批」不写为什么，员工只能来问人。
    await notify_overtime_decision(db, entry, approved=False, note=payload.reason)
    return {"entry": entry}


@router.post("/admin/entries/{entry_id}/void")
async def admin_void(
    entry_id: int,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(admin_session_actor),
) -> dict:
    """作废一笔**已批准**的登记：批准之后员工动不了它，只有这里能救回一个批错的。

    作废不是删除 —— 那一行还在（状态 `voided`），员工手机上看得见它被作废过。
    """
    ledger = OvertimeLedger(db)
    try:
        entry = await ledger.void(EntryActor.from_actor(actor), entry_id)
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    # 作废也要让员工知道：他手机上那一笔从「已批准」变成了「已作废」。
    await _overtime_nudge("entry_voided", entry["employee_id"])
    return {"entry": entry}


# ── 店长那一面（票 04）：员工会话 + 「人事管理」能力 ───────────────────────────
#
# 与上面 `/admin/*` 那组**能力相同、门不同**：超管走管理端 cookie，店长走员工 cookie
# 加一个能力键。两扇门落在同一套服务层方法上（`list_pending` / `approve` / `reject` /
# `submit`），所以「批一笔」只有一份实现，不存在两种口径 —— 这也正是 `docs/adr/0103`
# 选的形状：不发明第三种身份、也不新开第二套登录。
#
# 这一面**没有金额**：店长用的就是员工会话，而底薪按 `docs/adr/0098` 不下发员工端。
# 他判断一笔该不该认，靠日期、时长、事由与这个人的历史记录。


@router.get("/review/pending")
async def review_pending(
    db=Depends(get_db), actor: WorkbenchActor = Depends(_overtime_review)
) -> dict:
    """全店**等审批**的队列，旧的在前 —— 与管理端那条同一个服务层方法。

    **自己提的那几笔不进来**：他不能批自己的登记（服务层的 `cannot_self_accept`），
    摆在队列里就是一颗点了必被拒的「批准」。管理端那条（超管）仍然看全店。
    """
    ledger = OvertimeLedger(db)
    try:
        entries = await ledger.list_pending(exclude_employee_id=actor.employee_id)
        deductions = await ledger.list_pending_deductions(
            exclude_employee_id=actor.employee_id
        )
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    return {
        **entries,
        "deductions": deductions["deductions"],
        "deduction_count": deductions["count"],
        **_LIMITS,
    }


@router.get("/review/employees")
async def review_employees(
    db=Depends(get_db), _actor: WorkbenchActor = Depends(_overtime_review)
) -> dict:
    """代录时要选人：只回员工号、姓名、停用与否。

    **新开的窄口**，不借花名册那个带身份证号与底薪的端点 —— 店长用的是员工会话，
    那两样按 `docs/adr/0098` 不下发员工端。停用的人也在名单里（历史登记可能还要补在
    他头上），由页面标出来：这一层不替业务决定「停用的人还能不能被补录」。
    """
    roster = await EmployeeAccounts(db).list_roster()
    return {
        "employees": [
            {
                "id": int(employee["id"]),
                "name": employee.get("name") or "",
                # 离职（2026-10-29 取代「停用」）：到今天为止算不算已离职。
                "resigned": bool(employee.get("resigned")),
                "resigned_at": employee.get("resigned_at"),
            }
            for employee in roster
        ]
    }


@router.post("/review/entries")
async def review_backfill(
    payload: BackfillRequest,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(_overtime_review),
) -> dict:
    """店长代员工补录一笔（任意过去日期）—— 与管理端那条走同一个服务层方法。

    `created_by` 记的是这位店长自己（`staff:<员工号>`）：台账上分得清「他自己提的」
    与「别人替他补的」。
    """
    ledger = OvertimeLedger(db)
    try:
        entry = await ledger.submit(
            EntryActor.from_actor(actor),
            payload.entry_date,
            payload.half_hours,
            payload.reason,
            target_employee_id=payload.employee_id,
        )
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    await _overtime_nudge("entry_submitted", entry["employee_id"])
    return {"entry": entry}


@router.post("/review/{entry_id}/approve")
async def review_approve(
    entry_id: int,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(_overtime_review),
) -> dict:
    """批准一笔待审批的登记。状态谓词在服务层的写锁里：重复点只有第一次算数。"""
    ledger = OvertimeLedger(db)
    try:
        entry = await ledger.approve(EntryActor.from_actor(actor), entry_id)
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    await _overtime_nudge("entry_approved", entry["employee_id"])
    # 群里也知会一声「批了」：店长勾了「加班/补钟申请」订阅，就该同时看到「谁提了」
    # 与「批没批」（2026-11-02 的审批结果通知）。
    await notify_overtime_decision(db, entry, approved=True)
    return {"entry": entry}


@router.post("/review/{entry_id}/reject")
async def review_reject(
    entry_id: int,
    payload: RejectRequest,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(_overtime_review),
) -> dict:
    """驳回一笔待审批的登记 —— **必须写理由**，理由会出现在员工手机上。"""
    ledger = OvertimeLedger(db)
    try:
        entry = await ledger.reject(EntryActor.from_actor(actor), entry_id, payload.reason)
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    await _overtime_nudge("entry_rejected", entry["employee_id"])
    # 群里也带上那句理由 —— 「没批」不写为什么，员工只能来问人。
    await notify_overtime_decision(db, entry, approved=False, note=payload.reason)
    return {"entry": entry}


# ── 抵扣单的审批与作废（ADR 0124）──────────────────────────────────────────
#
# 与加班登记那套**同一份状态机、同一批门**：员工提/撤在 `/me/*`（员工门），店长批在
# `/review/*`（员工会话 + 人事管理能力），超管批/作废在 `/admin/*`（管理端门）。
# 两条审批门落在同一套服务层方法上，所以「怎么判一张抵扣单」只有一份实现。


@router.get("/admin/deductions")
async def admin_deductions(
    month: Optional[str] = Query(None, description="YYYY-MM；按归月筛"),
    employee_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(admin_session_actor),
) -> dict:
    """全店抵扣单（管理端）：可按归月 / 按人 / 按状态筛，新的在前。"""
    ledger = OvertimeLedger(db)
    try:
        data = await ledger.list_deductions(
            month=month, employee_id=employee_id, status=status
        )
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    return {**data, **_LIMITS}


@router.post("/admin/deductions/{deduction_id}/approve")
async def admin_approve_deduction(
    deduction_id: int,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(admin_session_actor),
) -> dict:
    """批准一张待审批的抵扣单。状态谓词与上限校验都在服务层的写锁里。"""
    ledger = OvertimeLedger(db)
    try:
        deduction = await ledger.approve_deduction(EntryActor.from_actor(actor), deduction_id)
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    await _overtime_nudge("deduction_approved", deduction["employee_id"])
    await notify_overtime_deduction_decision(db, deduction, approved=True)
    return {"deduction": deduction}


@router.post("/admin/deductions/{deduction_id}/reject")
async def admin_reject_deduction(
    deduction_id: int,
    payload: RejectRequest,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(admin_session_actor),
) -> dict:
    """驳回一张待审批的抵扣单 —— **必须写理由**，理由会出现在员工手机上。"""
    ledger = OvertimeLedger(db)
    try:
        deduction = await ledger.reject_deduction(
            EntryActor.from_actor(actor), deduction_id, payload.reason
        )
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    await _overtime_nudge("deduction_rejected", deduction["employee_id"])
    await notify_overtime_deduction_decision(
        db, deduction, approved=False, note=payload.reason
    )
    return {"deduction": deduction}


@router.post("/admin/deductions/{deduction_id}/void")
async def admin_void_deduction(
    deduction_id: int,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(admin_session_actor),
) -> dict:
    """作废一张**已批准**的抵扣单（封账前）。封过账的月份锁死 —— 服务层里判。"""
    ledger = OvertimeLedger(db)
    try:
        deduction = await ledger.void_deduction(EntryActor.from_actor(actor), deduction_id)
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    await _overtime_nudge("deduction_voided", deduction["employee_id"])
    return {"deduction": deduction}


@router.get("/review/deductions/pending")
async def review_pending_deductions(
    db=Depends(get_db), actor: WorkbenchActor = Depends(_overtime_review)
) -> dict:
    """店长手机上的抵扣待办 —— 自己提的不进来（不能批自己的，同加班登记）。"""
    ledger = OvertimeLedger(db)
    try:
        return {
            **await ledger.list_pending_deductions(exclude_employee_id=actor.employee_id),
            **_LIMITS,
        }
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc


@router.post("/review/deductions/{deduction_id}/approve")
async def review_approve_deduction(
    deduction_id: int,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(_overtime_review),
) -> dict:
    ledger = OvertimeLedger(db)
    try:
        deduction = await ledger.approve_deduction(
            EntryActor.from_actor(actor), deduction_id
        )
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    await _overtime_nudge("deduction_approved", deduction["employee_id"])
    await notify_overtime_deduction_decision(db, deduction, approved=True)
    return {"deduction": deduction}


@router.post("/review/deductions/{deduction_id}/reject")
async def review_reject_deduction(
    deduction_id: int,
    payload: RejectRequest,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(_overtime_review),
) -> dict:
    ledger = OvertimeLedger(db)
    try:
        deduction = await ledger.reject_deduction(
            EntryActor.from_actor(actor), deduction_id, payload.reason
        )
    except OvertimeError as exc:
        raise _OVERTIME_ERRORS.http(exc) from exc
    await _overtime_nudge("deduction_rejected", deduction["employee_id"])
    await notify_overtime_deduction_decision(
        db, deduction, approved=False, note=payload.reason
    )
    return {"deduction": deduction}
