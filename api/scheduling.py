#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班的 HTTP 适配层。规则在 `services/scheduling/store.py`，这里只管翻译。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.security import require_session, require_staff_session
from database import get_db
from services.scheduling import MAX_CYCLE_DAYS, MAX_SHIFT_NAME
from services.scheduling.store import SchedulingError, SchedulingStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scheduling", tags=["排班"])

# 服务层的 `SchedulingError.code` 是稳定标识，不是给店长看的文案。这里把每个 code
# 翻成一句能照着改的话 —— 「排班参数不合法」那种一句话会把「周期太长」和「这天日期
# 打错了」说成同一件事（票 04 的规则编辑页要能说清哪里错）。
_ERROR_DETAILS = {
    "invalid_cycle": f"轮转周期不合法：1 到 {MAX_CYCLE_DAYS} 天，每格是班次或休",
    "invalid_anchor": "轮转规则的起点日不合法（应该是 YYYY-MM-DD）：请重新配一遍这条规则",
    "invalid_month": "月份格式应该是 YYYY-MM",
    "invalid_business_date": "日期格式应该是 YYYY-MM-DD",
    "unknown_shift": "班次不存在或已停用",
    # 带一个 `{}`：服务层会把出错的那一格序号放在 `SchedulingError.args[0]`（票 04 要求
    # 「说清哪里错」——「排班参数不合法」说不出是第 3 格写错了）。同一个事实只在下面
    # `_bad_request` 里解释一次。
    "unknown_shift_in_cycle": "轮转周期第 {} 格引用的班次不存在或已停用：请重新选那一天的班次",
    "unknown_employee": "员工不存在",
    "unknown_zone": "责任区不存在：请先在卫生的责任区页面新建，或刷新本页",
    # 单日覆盖（票 07）三种「这天改不了」的原因，各说各的：
    "past_day": "已经过去的日子改不了：排班写下的历史不重写",
    # 带一个 `{}`：服务层把展开窗口的末日放在 `args[0]`。**不写死「90 天」** ——
    # 窗口长度是 `EXPANSION_DAYS` 的事，改那个数不该让文案说谎。
    "beyond_window": "这天还没排到：排班只铺到 {}，等它进窗口再改",
    "missing_shift": "要改班次就得给一个班次；班次留空表示那天休，请用「改成休」",
    "rest_with_details": "「改成休」的那天不能再带班次或责任区：请把这两项留空",
    # 请假申请（票 08）：日期没给、区间反了、事由太长、申请不存在 / 已经处理过，各说各的。
    # 字段整个没给（`{}`）也走这条：模型里 `start_date` 有默认值，缺字段不会变成
    # pydantic 的 422 —— 那个 `detail` 是一串英文的字段错误，员工看不懂。
    "missing_day": "请先选一个开始日期：请假从哪天开始",
    "bad_range": "结束日期不能早于开始日期：请重新选一遍这几天",
    # 「过去不改」这条口径跟票 07 是同一条，但话得对员工说：他不是在改排班，是在提申请。
    "past_leave": "已经过去的日子请不了假：请从今天起选",
    # 带一个 `{}`：服务层把展开窗口的末日放在 `args[0]`（跟 `beyond_window` 同一条口径）。
    "beyond_leave": "排班还没铺到那么远：最多请到 {}",
    # 带一个 `{}`：服务层把上限（`MAX_REQUEST_NOTE`）放在 `args[0]`。
    "note_too_long": "事由最多 {} 个字：请缩短一点再提交",
    # 「不存在」也用在「这条是别人的」上：不告诉员工「它在，但不是你的」。
    "unknown_request": "这条申请不存在：可能已经被撤回，刷新看看",
    "request_not_pending": "这条申请已经处理过了：刷新看看它现在到哪一步",
    # 换班（票 09）：找谁换、哪天换、那天有没有班换，各说各的。跟请假一样，字段整个没给
    # （`{}`）也落到服务层那道闸上，不变成 pydantic 的英文 422。
    "swap_with_self": "换班得找别人：不能跟自己换",
    "missing_peer": "请先选一位同事：换班得跟人说好",
    "unknown_peer": "找不到这位同事：刷新一下名单再选",
    "peer_unavailable": "这位同事现在不在排班名单里（已停用或还没批准）：换个人吧",
    "missing_swap_day": "请先选换哪一天",
    # 「过去不改」这条口径跟票 07、请假是同一条，话是对员工说的。
    "past_swap": "已经过去的日子换不了班：请从今天起选",
    # 带一个 `{}`：服务层把展开窗口的末日放在 `args[0]`（跟 `beyond_leave` 同一条口径）。
    "beyond_swap": "排班还没铺到那么远：最多换到 {}",
    "nothing_to_swap": "那天你手上没有班可换：先让店长给你配上轮转规则",
    "already_asked": "你刚跟这位同事提过这一天的换班：等对方回应，或先撤回那条",
    # 班次表的编辑（票 11）：改名、调顺序、停用、删除各自的拦法，各说各的。
    "missing_shift_name": "请先给班次起个名字",
    # 带一个 `{}`：服务层把上限（`MAX_SHIFT_NAME`）放在 `args[0]`（同 `note_too_long`）。
    "shift_name_too_long": "班次名最多 {} 个字：它要出现在月历和当天名单上，短一点",
    "shift_name_taken": "已经有一个同名的班次了：换个名字，或者把那一条改掉",
    # 带一个 `{}`：服务层把那个保留词放在 `args[0]`（点名的两句能照做）。
    "shift_name_reserved": "「{}」这个词留给「那天休息」：周期里写它表示不上班，换个名字",
    "invalid_shift_order": "显示顺序得是个整数",
    # 编辑班次表用的「没有这一行」：跟 `unknown_shift`（只能挑在用的班次）分开 ——
    # 停用的班次照样要能改名、能重新启用。
    "unknown_shift_id": "找不到这个班次：可能已经被删了，刷新看看",
    # 带一个 `{}`：服务层把「还有几个人」放在 `args[0]`（票 11 验收项：说清还有多少人在用）。
    "shift_in_use": "还有 {} 个人的轮转规则里排着这个班次：先改掉他们的规则，再停用",
    # 带一个 `{}`：服务层说清为什么算「用过」（排过多少天班、多少人的轮转里排着它）。
    "shift_used": "这个班次已经用过了（{}），删不掉：停用它就行，历史排班照旧显示",
    "shift_order_mismatch": "班次顺序对不上（可能刚有人加过或删过班次）：刷新一下再调",
    "last_active_shift": "至少得留一个能用的班次：不然谁都没班可排",
    "not_migrated": (
        "排班表还没建好：请在 Admin「系统更新 → 数据库迁移」应用 "
        "migrations/pg/0005_scheduling.sql、0006_scheduling_zone_defaults.sql、"
        "0007_scheduling_overrides.sql、0008_scheduling_requests.sql 与 "
        "0009_scheduling_swap.sql，然后刷新本页"
    ),
}

_ERROR_STATUS = {
    # 找不到的那一行：人、申请、班次。撤别人的申请也走这里（接口不区分「不存在」与「不是你的」）。
    "unknown_employee": 404,
    "unknown_request": 404,
    "unknown_shift_id": 404,
    # 其余一律 400（`request_not_pending` 就是这一类：那一行在，只是不再是「等你批」）；
    # `not_migrated` 不是「参数写错了」，是这台机器还没升级完 —— 503 比 400 诚实。
    "not_migrated": 503,
}


class SetRuleRequest(BaseModel):
    # 一格 = 一个营业日：班次 id 或 null（休）。周期天数 = 数组长度。
    cycle: list[Optional[int]] = Field(..., min_length=1, max_length=MAX_CYCLE_DAYS)
    anchor_date: Optional[str] = None


class CreateShiftRequest(BaseModel):
    """加一个班次（票 11）。`sort_order` 不给就排在最后。

    `name` 不是必填（同 `LeaveRequest.start_date` 的理由）：缺字段、空串、显式 `null`
    都落到服务层那道闸上，回一句中文的 400，而不是 pydantic 那串英文的 422 字段错误。
    """

    name: Optional[str] = None
    sort_order: Optional[int] = None


class UpdateShiftRequest(BaseModel):
    """改一个班次（票 11）：名字 / 显示顺序 / 启用停用。

    **`None` = 这一项不动**（不是「清空」）：`is_active` 显式给 `false` 才是停用，
    名字本来也不允许为空，所以「不给」与「给空」在这里是同一件事 —— 不动它。
    """

    name: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class ShiftOrderRequest(BaseModel):
    """整表重排（票 11）：所有班次 id 的新顺序（含停用的），一次说完。"""

    ids: list[int] = Field(default_factory=list)


class SetZoneDefaultRequest(BaseModel):
    # 哪个班次配哪个区；`zone_id=None` = 清掉这个配置（这个人这个班次「还没定在哪」）。
    shift_id: int
    zone_id: Optional[int] = None


class SetOverrideRequest(BaseModel):
    # 某人某一天改成什么。`is_rest=True` 时班次与责任区都得留空（那天就是休）；
    # 否则 `shift_id` 必给、`zone_id=None` 表示「跟这个班次的固定区」。
    is_rest: bool = False
    shift_id: Optional[int] = None
    zone_id: Optional[int] = None


class LeaveRequest(BaseModel):
    """员工请假：一天（不填 `end_date`）或一段日期（**两端都算**）。

    `note` 是可选的事由，长度上限在服务层（`MAX_REQUEST_NOTE`）—— 那边超了报
    `note_too_long`，这里不重复一遍数字，免得两处漂移。

    `start_date` 特意不是必填（默认 `None`）：缺字段、空串、显式 `null` 都落到服务层那道闸上，
    回一句中文的 400，而不是 pydantic 那串英文的 422 字段错误。
    """

    start_date: Optional[str] = None
    end_date: Optional[str] = None
    note: Optional[str] = None


class SwapRequest(BaseModel):
    """换班（票 09）：跟哪位同事换哪一天。

    只这一天（不是区间）：换班是「你对我的这一天、我对你的这一天」，跨天换班没有对应的
    现实场景。`note` 与请假共用一条上限（`MAX_REQUEST_NOTE`），超了报 `note_too_long`。

    `peer_employee_id` / `business_date` 都不是必填（同 `LeaveRequest.start_date` 的
    理由）：缺字段、空串落到服务层，回一句中文 400，而不是英文的 422 字段错误。
    """

    peer_employee_id: Optional[int] = None
    business_date: Optional[str] = None
    note: Optional[str] = None


def _bad_request(exc: SchedulingError) -> HTTPException:
    detail = _ERROR_DETAILS.get(exc.code, "排班参数不合法")
    if "{}" in detail:
        # `SchedulingError(code, message)` 的 message 放在 args[0]（默认等于 code），
        # 服务层用它带「哪里错」的细节，例如周期里出错的那一格序号。
        # 用 `str.replace` 而不是 `str.format`：模板里多一个花括号、或哪天文案里出现
        # 字面 `{...}`，`format` 会抛 KeyError/IndexError/ValueError 变成 500 —— `replace`
        # 没有这些语义。服务层没给细节时填「？」，绝不把字面的 `{}` 端给店长看。
        raw = exc.args[0] if exc.args else ""
        if not raw or raw == exc.code:
            logger.warning("排班错误缺细节，用「？」占位: code=%s", exc.code)
        detail = detail.replace("{}", raw if raw and raw != exc.code else "？")
    logger.info("排班请求被拒: code=%s", exc.code)
    return HTTPException(status_code=_ERROR_STATUS.get(exc.code, 400), detail=detail)


@router.get("/me")
async def my_days(
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """员工自己这几天的班（「今天」页读的就是这一条）。

    跟店长那扇门不是一回事：那边要管理端会话（`require_session`），这边要员工自己的手机端
    cookie（`require_staff_session`）。**接口上故意没有 `employee_id` 参数** —— 员工会话能读到的只有自己，
    想读别人的班也没地方填。
    """
    store = SchedulingStore(db)
    try:
        data = await store.my_days(employee["id"])
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    return {
        "employee": {"id": employee["id"], "name": employee["name"]},
        "today": data["today"],
        "days": data["days"],
    }


@router.get("/me/month")
async def my_month(
    month: Optional[str] = Query(None, description="YYYY-MM；不填就是本营业月"),
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """员工自己那一个月的班（「今天」页点「整月」进来）。

    跟 `/me` 同一扇门、同一套口径（`scheduled` / `shift_id` / `shift_name`），只是
    窗口一个月；`month` 只决定看哪个月，决定不了看谁 —— **接口上同样没有
    `employee_id`**。月份写错是 400（`invalid_month`），不是 500。
    """
    store = SchedulingStore(db)
    try:
        data = await store.my_month(employee["id"], month)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    return {
        "employee": {"id": employee["id"], "name": employee["name"]},
        "month": data["month"],
        "first_date": data["first_date"],
        "today": data["today"],
        "lead": data["lead"],
        "window_end": data["window_end"],
        "days": data["days"],
    }


@router.get("/me/requests")
async def my_requests(
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """我的申请，新的在前（「今天」页的「我的申请」看的就是这条）。

    跟 `/me`、`/me/month` 同一扇门，**接口上同样没有 `employee_id`**：员工会话读得到
    的只有自己的申请。每条带 `status`（等对方 / 等店长批 / 批了 / 驳了 / 撤回了）与
    `decided_at`；换班那条还带 `peer_name`（「等王五同意」要说得出是谁）。

    `incoming` 是**别人问我换班的**（票 09）：还没回应的那几条，带两个人那天的班 ——
    对方在手机上直接点同意 / 拒绝，不用去别处找。
    """
    store = SchedulingStore(db)
    try:
        data = await store.my_requests(employee["id"])
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    return {
        "employee": {"id": employee["id"], "name": employee["name"]},
        "today": data["today"],
        "requests": data["requests"],
        "incoming": data["incoming"],
    }


@router.post("/me/requests")
async def submit_leave(
    payload: LeaveRequest,
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """提一条请假：一天，或一段日期（两端都算）。

    落「等店长批」——**申请本身不改排班**：批了才写覆盖行（那几天变成请假），
    驳回与撤回一个字都不写。
    """
    store = SchedulingStore(db)
    try:
        request = await store.submit_leave(
            employee["id"], payload.start_date, payload.end_date, payload.note
        )
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    return {
        "employee": {"id": employee["id"], "name": employee["name"]},
        "request": request,
    }


@router.delete("/me/requests/{request_id}")
async def cancel_request(
    request_id: int,
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """撤回自己**还没被批**的申请。排班一个字不改。

    别人的申请一律 404（`unknown_request`）：接口不区分「不存在」与「不是你的」。
    """
    store = SchedulingStore(db)
    try:
        return await store.cancel_request(employee["id"], request_id)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.get("/me/colleagues")
async def my_colleagues(
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """换班能找谁（票 09）：名单里在上班的其他人，只给 id / 姓名 / 职位。

    **不给手机号，也不给别人的排班**：员工端要的是「选一个人」，不是一份花名册。
    """
    store = SchedulingStore(db)
    try:
        people = await store.colleagues(employee["id"])
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    return {"employee": {"id": employee["id"], "name": employee["name"]}, "colleagues": people}


@router.post("/me/swaps")
async def submit_swap(
    payload: SwapRequest,
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """提一条换班：跟哪位同事、换哪一天（票 09 的第一步）。

    落「等对方同意」（`pending_peer`）——**申请本身不改排班**：对方点了同意才轮到店长，
    店长批了两人那天的班才对调；对方拒绝或自己撤回，排班一个字都不写。
    """
    store = SchedulingStore(db)
    try:
        request = await store.submit_swap(
            employee["id"], payload.peer_employee_id, payload.business_date, payload.note
        )
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    return {
        "employee": {"id": employee["id"], "name": employee["name"]},
        "request": request,
    }


@router.post("/me/swaps/{request_id}/accept")
async def accept_swap(
    request_id: int,
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """同意跟我换班：这条申请这才进店长待办（票 09 的验收项）。

    两条没有 body 的路由（同意 / 拒绝）而不是一条带布尔值的：没有 body 就没有
    pydantic 校验，手机端点一下就完事，少一处能报 422 的地方。
    """
    store = SchedulingStore(db)
    try:
        return await store.answer_swap(employee["id"], request_id, True)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.post("/me/swaps/{request_id}/reject")
async def reject_swap(
    request_id: int,
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """拒绝跟我换班：这件事到此为止 —— 店长那边从头到尾看不到它，排班一个字不改。"""
    store = SchedulingStore(db)
    try:
        return await store.answer_swap(employee["id"], request_id, False)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.get("/shifts")
async def list_shifts(db=Depends(get_db), _: str = Depends(require_session)) -> dict:
    """班次表（白班、夜班……）。UI 按 N 个班次渲染，不写死两个。"""
    store = SchedulingStore(db)
    try:
        return {"shifts": await store.list_shifts()}
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.get("/shifts/manage")
async def manage_shifts(db=Depends(get_db), _: str = Depends(require_session)) -> dict:
    """班次表**连停用的一起**，每条带上「有多少人在用」：票 11 的编辑页读这一条。

    编辑页要看到停用的那几条（给它们改名、重新启用、看还有没有历史行），所以不能
    复用只出启用班次的 `/shifts`。`max_name` 随响应下去（同 `/roster` 的
    `max_cycle_days`）：班次名的上限是服务层的数，前端不再写死一份。
    `active_count` 是**还在用**的班次条数：删掉最后一条在用的就没人能排班了，
    页面照它把「删除」按钮灰掉（服务端还会再判一次，那份才是权威）。
    """
    store = SchedulingStore(db)
    try:
        usage = await store.shift_usage()
        shifts = [
            dict(shift, people=int(usage.get(shift["id"], {}).get("people", 0) or 0),
                 days=int(usage.get(shift["id"], {}).get("days", 0) or 0))
            for shift in await store.list_shifts(include_inactive=True)
        ]
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    return {
        "shifts": shifts,
        "max_name": MAX_SHIFT_NAME,
        "active_count": len([shift for shift in shifts if shift["is_active"]]),
    }


@router.post("/shifts")
async def create_shift(
    payload: CreateShiftRequest,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """加一个班次（票 11）。加完就是一条普通的班次：月历图例、配规则的下拉、员工端
    的卡片都按 N 个班次渲染，不需要改代码（验收项）。"""
    store = SchedulingStore(db)
    try:
        return {"shift": await store.create_shift(payload.name, payload.sort_order)}
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


# 声明在 `/shifts/{shift_id}` **之前**：两条都是 PUT，先声明的先匹配，否则
# `/shifts/order` 会被当成 `shift_id="order"` 去解析，回一个 422（英文的字段错误）。
@router.put("/shifts/order")
async def reorder_shifts(
    payload: ShiftOrderRequest,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """按给定的顺序重铺显示顺序（票 11 的「上移 / 下移」）。要**给全**所有班次 id。"""
    store = SchedulingStore(db)
    try:
        return {"shifts": await store.reorder_shifts(payload.ids)}
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.put("/shifts/{shift_id}")
async def update_shift(
    shift_id: int,
    payload: UpdateShiftRequest,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """改名 / 调顺序 / 启用停用（票 11）。停用还有人在上的班次会被拦下来说清人数。"""
    store = SchedulingStore(db)
    try:
        return {
            "shift": await store.update_shift(
                shift_id, payload.name, payload.sort_order, payload.is_active
            )
        }
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.delete("/shifts/{shift_id}")
async def delete_shift(
    shift_id: int,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """删掉刚建错、还没人用过的班次（票 11）。排过班的删不掉，那句话里说清为什么。"""
    store = SchedulingStore(db)
    try:
        return {"shift": await store.delete_shift(shift_id)}
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.get("/calendar")
async def month_calendar(
    month: str = Query(..., description="YYYY-MM"),
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """一个月的月历：每天各班几个人。打开就先补齐未来 90 天（幂等）。"""
    store = SchedulingStore(db)
    try:
        await store.expand()
        return await store.month_calendar(month)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.get("/day")
async def day_detail(
    date: str = Query(..., alias="date", description="YYYY-MM-DD"),
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """某一天各班是谁（月历点开那天看的就是这个）。"""
    store = SchedulingStore(db)
    try:
        await store.expand()
        return await store.day_detail(date)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.get("/roster")
async def roster(db=Depends(get_db), _: str = Depends(require_session)) -> dict:
    """全体花名册 + 每人当前那条规则 + 每人每班次的固定责任区（没配就是 `{}`）。

    名单面板会一起读固定责任区（票 03），所以缺 0006 时这里也要出 503 那句话 ——
    不然「已更新代码、还没应用迁移」这段窗口里，店长看到的是 500。
    """
    store = SchedulingStore(db)
    try:
        data = await store.roster_with_rules()
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    # 周期能写多少天由服务层的常量说了算：规则编辑页照这个数校验，别在前端再写死一份。
    data["max_cycle_days"] = MAX_CYCLE_DAYS
    return data


@router.put("/zone-defaults/{employee_id}")
async def set_zone_default(
    employee_id: int,
    payload: SetZoneDefaultRequest,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """给「某人 × 某班次」定一个固定责任区；今天以后已经铺好的行一起改。

    责任区名单是卫生建的那份（公共层读出来），所以这边新建完区不用重启就能选到。
    """
    store = SchedulingStore(db)
    try:
        return await store.set_zone_default(employee_id, payload.shift_id, payload.zone_id)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.put("/rules/{employee_id}")
async def set_rule(
    employee_id: int,
    payload: SetRuleRequest,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """给一个人配轮转规则；今天以后的规则行立刻重铺，月历随即可见。"""
    store = SchedulingStore(db)
    try:
        return await store.set_rule(employee_id, payload.cycle, payload.anchor_date)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.delete("/rules/{employee_id}")
async def clear_rule(
    employee_id: int,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """拿掉一个人的规则（连今天以后的规则行）。过去写过的不动。"""
    store = SchedulingStore(db)
    try:
        await store.clear_rule(employee_id)
    except SchedulingError as exc:  # pragma: no cover - 现在不会抛，留个一致的出口
        raise _bad_request(exc) from exc
    return {"employee_id": employee_id, "rule": None}


@router.put("/overrides/{employee_id}/{business_date}")
async def set_override(
    employee_id: int,
    business_date: str,
    payload: SetOverrideRequest,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """改某人某一天：换班次、改成休、或只换责任区 —— **只动这一天**（票 07）。

    改过的那天在月历上有标记，改规则不会把它冲掉；撤掉覆盖（`DELETE` 同一条路径）
    那天就回到规则铺出来的样子。过去的日子不给改（400 `past_day`）。
    """
    store = SchedulingStore(db)
    try:
        return await store.set_override(
            employee_id,
            business_date,
            is_rest=payload.is_rest,
            shift_id=payload.shift_id,
            zone_id=payload.zone_id,
        )
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.delete("/overrides/{employee_id}/{business_date}")
async def clear_override(
    employee_id: int,
    business_date: str,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """撤掉某人某天的覆盖，那天回到规则铺出来的样子（票 07）。

    今天以后按**现在的规则**重算；已经过去的日子只把覆盖记录摘掉，结果行不动
    （过去就是过去：撤销不重写历史，界面靠 `day_detail.undoable` 把按钮收起来）。
    """
    store = SchedulingStore(db)
    try:
        return await store.clear_override(employee_id, business_date)
    except SchedulingError as exc:
        # 日期格式不对、管理员没应用 0007 都会走到这里 —— 跟 PUT 那条一样的出口。
        raise _bad_request(exc) from exc


@router.get("/inbox")
async def inbox(db=Depends(get_db), _: str = Depends(require_session)) -> dict:
    """店长待办：等他批的申请 + 还没配规则的人（票 08 的待办页，票 09 起也收换班）。

    每条请假带一份预览（`days[*].after`：批了之后那天每个班次还剩几个人）。人手够不够
    **只摊开数字、不拦** —— 服务层没有「最少几个人」这个配置，批准是店长的事。

    换班那张卡摊开两个人那天的班（`peer_*`），批了就是对调 —— 对调走两个人、来两个人，
    哪个班次的人数都不变，所以没有「剩几个人」这一项。**对方还没点头的换班不在这里**。
    """
    store = SchedulingStore(db)
    try:
        return await store.inbox()
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.post("/inbox/{request_id}/approve")
async def approve_request(
    request_id: int,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """批一条申请：请假那几天变成请假，换班两个人那天对调。

    换班批的是「两个人都点过头」的那条：对方那一步（`answer_swap`）是它进这扇门的条件。
    已经过去的日子不重写（票 07 的「过去不改」）：响应里的 `applied_days` / `skipped_days`
    说清到底写了哪几天，界面照实说，不假装。
    """
    store = SchedulingStore(db)
    try:
        return await store.approve_request(request_id)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.post("/inbox/{request_id}/reject")
async def reject_request(
    request_id: int,
    db=Depends(get_db),
    _: str = Depends(require_session),
) -> dict:
    """驳回一条申请：排班一个字不改，只把申请记为驳回（票 08 的验收项；换班同理）。"""
    store = SchedulingStore(db)
    try:
        return await store.reject_request(request_id)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
