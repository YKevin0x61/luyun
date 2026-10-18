#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班的 HTTP 适配层。规则在 `services/scheduling/store.py`，这里只管翻译。"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.security import require_any_identity_session, require_staff_session
from database import get_db
from services.identity.capabilities import CAP_SCHEDULE, has_cap
from services.realtime.hub import realtime_hub
from services.scheduling import MAX_CYCLE_DAYS, MAX_REQUEST_NOTE, MAX_SHIFT_NAME
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
    "unknown_zone": "工作区不存在：请先在卫生的工作区页面新建，或刷新本页",
    # 单日覆盖「这天改不了」的原因，各说各的。票 14 起**过去 30 天内是可以补记的**
    # （ADR 0108）：这句话说的不再是「历史不重写」，而是「太旧了，超出可补记窗口」。
    # 带一个 `{}`：服务层把可补记窗口的第一天放在 `args[0]`（同 `beyond_window`，
    # 不把「30 天」这个数写死在文案里 —— 那个数是 `BACKFILL_DAYS` 的事）。
    "past_day": "超出可补记窗口：{} 之前的日子不再改写",
    # 过去的日子只能改成某个班，不能退回规则 —— 以前那种「摘掉覆盖记录、结果行不动」
    # 是没人看得出发生了什么的半个动作（ADR 0108 决策 6）。
    # 2026-10-18 起不再产生（过去的日子也能清除）；词条留着兼容老客户端的错误码。
    "past_clear": "过去的日子只能改成某个班（旧口径，现已放开）",
    # 带一个 `{}`：服务层把展开窗口的末日放在 `args[0]`。**不写死「90 天」** ——
    # 窗口长度是 `EXPANSION_DAYS` 的事，改那个数不该让文案说谎。
    "beyond_window": "这天还没排到：排班只铺到 {}，等它进窗口再改",
    "missing_shift": "要改班次就得给一个班次；班次留空表示那天休，请用「改成休」",
    "rest_with_details": "「改成休」的那天不能再带班次或工作区：请把这两项留空",
    # 批量落笔（票 14）：只有这四种是**整批拒绝**的（其余「这一格刷不了」一律跳过并点名 ——
    # 部分成功是那张票的预期语义）。带 `{}` 的那条由服务层填 `MAX_BATCH_CELLS`。
    "batch_too_large": "一次最多刷 {} 格：请缩小选区再试",
    "invalid_batch_action": "这条批量动作不认识：只能是选一个班次、改成休，或清除班次",
    "invalid_batch_items": "这一批格子没看懂：请刷新页面重新选一次",
    "invalid_employee_id": "格子里的员工编号不合法：请刷新页面重新选一次",
    # 请假申请（票 08）：日期没给、区间反了、事由太长、申请不存在 / 已经处理过，各说各的。
    # 字段整个没给（`{}`）也走这条：模型里 `start_date` 有默认值，缺字段不会变成
    # pydantic 的 422 —— 那个 `detail` 是一串英文的字段错误，员工看不懂。
    "missing_day": "请先选一个开始日期：请假从哪天开始",
    "bad_range": "结束日期不能早于开始日期：请重新选一遍这几天",
    # 请假只能从今天起 —— 跟店长手改排班那条 30 天回溯窗口**不是一条**：员工不是在改
    # 排班，是在提申请，过去的日子补提没有意义（话得对员工说）。
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
    # 跟请假同一条（`past_leave`）：不能给过去的日子提换班，话是对员工说的。
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
    # 班次名的字符集（票 11 的收口）：周期编辑器把店长写的字**按空白与这几个符号切词**
    # （`SchedulingCalendarView.vue` 的 `parseCycle`），名字里带了它们，这条班次在界面上
    # 永远写不进周期；名字恰好切出两个合法班次名时（「白班/夜班」），还会把「固定上这个
    # 班」静默读成两班轮转。文案把不能用的字符**列全**，别让店长一个个试。
    # 带一个 `{}`：服务层把撞上的那个字符放在 `args[0]`（空白回的是「空格」两个字）。
    "shift_name_charset": (
        "班次名里不能有空格、, ， 、 · / 这些字符：轮转周期按它们切词，"
        "名字里带了就永远写不进周期。请去掉「{}」再试"
    ),
    "invalid_shift_order": "显示顺序得是个整数",
    # 卫生档位（票 10）：只能是不出日常 / 白班档 / 夜班档 三种。
    "invalid_duty_slot": "卫生档位只能选白班档、夜班档，或者不出日常：请重新选一次",
    # 编辑班次表用的「没有这一行」：跟 `unknown_shift`（只能挑在用的班次）分开 ——
    # 停用的班次照样要能改名、能重新启用。
    "unknown_shift_id": "找不到这个班次：可能已经被删了，刷新看看",
    # 带一个 `{}`：服务层把「还有几个人」放在 `args[0]`（票 11 验收项：说清还有多少人在用）。
    "shift_in_use": "还有 {} 个人的轮转规则里排着这个班次：先改掉他们的规则，再停用",
    # 带一个 `{}`：服务层说清为什么算「用过」（排过多少天班、多少人的轮转里排着它）。
    "shift_used": "这个班次已经用过了（{}），删不掉：停用它就行，历史排班照旧显示",
    "shift_order_mismatch": "班次顺序对不上（可能刚有人加过或删过班次）：刷新一下再调",
    "last_active_shift": "至少得留一个能用的班次：不然谁都没班可排",
    # **逐条点名**是票 02 起的验收（503 要说清该跑哪个脚本，测试也按文件名断言），
    # 所以每加一条排班迁移都要回来补一句 —— 0010 就是收尾时补的。这条注释就是那个提醒。
    "not_migrated": (
        "排班表还没建好：请在 Admin「系统更新 → 数据库迁移」应用 "
        "migrations/pg/0005_scheduling.sql、0006_scheduling_zone_defaults.sql、"
        "0007_scheduling_overrides.sql、0008_scheduling_requests.sql、"
        "0009_scheduling_swap.sql、0010_shift_duty_slot.sql 与 "
        "0028_staff_assignments_half_rest.sql，然后刷新本页"
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

    `duty_slot` 是卫生的档位（票 10）：`'day'` / `'night'` / 不给或空 = 这条班次不出
    日常检查。新加的班次默认不出 —— 加一个「中班」时还没有对应的日常检查项，等店长
    想清楚它是白班档还是夜班档再标。

    `name` 不是必填（同 `LeaveRequest.start_date` 的理由）：缺字段、空串、显式 `null`
    都落到服务层那道闸上，回一句中文的 400，而不是 pydantic 那串英文的 422 字段错误。
    """

    name: Optional[str] = None
    sort_order: Optional[int] = None
    duty_slot: Optional[str] = None


class UpdateShiftRequest(BaseModel):
    """改一个班次（票 11、票 10）：名字 / 显示顺序 / 启用停用 / 卫生档位。

    **`None` = 这一项不动**（不是「清空」）：`is_active` 显式给 `false` 才是停用，
    名字本来也不允许为空，所以「不给」与「给空」在这里是同一件事 —— 不动它。

    `duty_slot` 是唯一的例外：「不动」和「清空成不出日常」得分开说，所以
    **`null` = 不动、空串 `""` = 清空**、`'day'` / `'night'` = 设成那一档。
    """

    name: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None
    # 卫生档位（票 10）：见上面的例外说明 —— `None` 不动、空串清空、day/night 是设置。
    duty_slot: Optional[str] = None


class ShiftOrderRequest(BaseModel):
    """整表重排（票 11）：所有班次 id 的新顺序（含停用的），一次说完。"""

    ids: list[int] = Field(default_factory=list)


class SetZoneDefaultRequest(BaseModel):
    # 哪个班次配哪个区；`zone_id=None` = 清掉这个配置（这个人这个班次「还没定在哪」）。
    shift_id: int
    zone_id: Optional[int] = None


class SetOverrideRequest(BaseModel):
    # 某人某一天改成什么。`is_rest=True` 时班次与工作区都得留空（那天就是休）；
    # 否则 `shift_id` 必给、`zone_id=None` 表示「跟这个班次的固定区」。
    is_rest: bool = False
    shift_id: Optional[int] = None
    zone_id: Optional[int] = None


class BatchOverrideItem(BaseModel):
    """批量刷的一格（票 14）：谁、哪天。

    两个字段都不是必填（同 `LeaveRequest.start_date` 的理由）：缺字段、空串、显式 `null`
    都落到服务层那道闸上，回一句中文的 400，而不是 pydantic 那串英文的 422 字段错误。
    """

    employee_id: Optional[int] = None
    business_date: Optional[str] = None


class BatchOverrideRequest(BaseModel):
    """一次刷一片（票 14；ADR 0107 决策 5/6）：`action` 整批同一个，`items` 最多 200 格。

    `action` = `shift`（`shift_id` 必给）/ `rest` / `clear`。`zone_id` 只对 `shift` 有意义，
    不给（或 `null`）= 每格跟**这个人这个班次**的固定区。`action` 特意不是必填：缺字段、
    写个不认识的值都由服务层报一句中文的 400（同 `CreateShiftRequest.name`）。

    `items` 的形状是**固定**的（谁、哪天）；格数上限不在这里判 —— 上限是服务层的数
    （`MAX_BATCH_CELLS`），超了要给一句中文话，而不是 pydantic 的 422。
    """

    action: Optional[str] = None
    shift_id: Optional[int] = None
    zone_id: Optional[int] = None
    items: list[BatchOverrideItem] = Field(default_factory=list)


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


async def _scheduling_nudge(reason: str, *employee_ids: Optional[int]) -> None:
    """排班或申请动了，叫相关的人自己来拉一次（nudge 不带数据，见 `services/realtime/hub.py`）。

    scope 里带 `employee_id`：员工连接只会收到**自己**那条（hub 的 `_staff_owns_scope`），
    店长那侧不受限 —— 他本来就要看全店。换班牵两个人、批假牵一个人，所以这里收可变参数：
    一人一条，不为「多人」在 hub 里开特例（那会让派发侧多一种要判的形状）。

    读路径不广播：`expand()` 那种顺手补行不值得叫醒谁 —— 数据形状没变，谁也没多看到什么。
    """
    for employee_id in {int(item) for item in employee_ids if item is not None}:
        await realtime_hub.broadcast_nudge(
            "scheduling",
            {"reason": reason, "employee_id": employee_id},
        )


async def _require_scheduler(identity: dict = Depends(require_any_identity_session)) -> dict:
    """店长那一面的门：管理端会话，**或**员工会话加「排班管理」（`schedule`）能力。

    2026-10-14 用户裁定：排班不再只给超级管理员 —— 花名册上勾了「排班管理」的店长用
    **员工会话**也能改全店排班（月历 / 待办 / 班次表三页）。他本来就有员工账号，不该为了
    改一个班次专门去电脑上登录管理端。判据与加班审批那扇门（`api/overtime.py` 的
    `_require_reviewer`）同一形状：员工会话 + 一项能力，每次请求现读 `admin_caps`。

    **花名册不在这扇门后面**：`roster`（审批入职、停用、改权限）是另一个键，仍只给
    超级管理员 —— 身份证明细与底薪按 `docs/adr/0098` 不下发员工端，所以「能改排班」
    不能连带拿到花名册页。

    被拒是 **403**（「你没这个权限」），不是 404：后者会把「权限不够」说成「这一笔不
    存在」。收回能力后下一次请求就 403 —— 权限不在登录时快照。
    """
    if identity.get("kind") == "admin":
        return identity
    employee = identity.get("employee") or {}
    if not has_cap(employee.get("admin_caps"), CAP_SCHEDULE):
        raise HTTPException(
            status_code=403,
            detail="只有开了「排班管理」权限的人能改排班：请找超级管理员在花名册里开通",
        )
    return identity


@router.get("/me")
async def my_days(
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """员工自己这几天的班（「今天」页读的就是这一条）。

    跟店长那扇门不是一回事：那边是 `_require_scheduler`（管理端会话，或带「排班管理」
    能力的员工会话 —— 2026-10-14 起），这边要员工自己的手机端 cookie
    （`require_staff_session`），读的永远是自己。**接口上故意没有 `employee_id` 参数** ——
    员工会话能读到的只有自己，想读别人的班也没地方填。
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

    票 13 起每天多两个字段：`staff_count` 是那天**全店**上班几个人（月历格子上
    那个数），`zone_name` 是我那天的工作区（格子不写，点开那天的名单里写）。
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


@router.get("/me/day")
async def my_day(
    date: str = Query(..., alias="date", description="YYYY-MM-DD"),
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """员工点开某一天看到的**全店**名单（票 13）：各班是谁、在哪个区，以及谁不上班。

    跟店长那版 `/day` 是同一份数据（同一个 `day_detail`），但**只出员工该看的那几项**：
    没有 `zone_id`、没有 `overridden`（这天谁跟规则不一样）、没有 `undoable`（能不能
    撤销覆盖）。放出去的是姓名、班次、工作区名，以及休 / 请假两种「没有班」的区分。

    **不是「谁都能读某一天」的通用口**：它仍然只认员工自己的 cookie，路径上也没有
    任何 `employee_id` —— 这一天全店都有谁，本来就是贴在墙上那张排班表上的东西；
    手机号、职位、申请记录这些不在其中（`services/scheduling/store.py` 的 `my_day`）。

    进门先补未来 90 天（幂等，跟店长那几条读路径一样）：不补的话，别人还没被铺到的
    那几天在名单里会凭空少人。
    """
    store = SchedulingStore(db)
    try:
        await store.expand()
        data = await store.my_day(date)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    return {"employee": {"id": employee["id"], "name": employee["name"]}, **data}


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
        # 事由的长度上限随这一条下去（同 `/roster` 的 `max_cycle_days`、`/shifts/manage`
        # 的 `max_name`）：前端拿它设 `maxlength` 与文案，不再写死第二份 50。
        "max_request_note": MAX_REQUEST_NOTE,
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
    # 店长那边等着批：叫他自己来拉一次（nudge 不带数据，只说「有你的事」）。
    await _scheduling_nudge("leave_submitted", employee["id"])
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
        result = await store.cancel_request(employee["id"], request_id)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    # 撤回也要说一声：店长待办里那条该消失（他可能正开着那一页）。
    await _scheduling_nudge("request_cancelled", employee["id"])
    return result


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
    # **对方**要马上知道有人找他换班 —— 这条最需要实时：他不开页面就永远不知道，
    # 申请人也就一直等（票 09 的整条链路卡在这儿）。一人一条，各收各的。
    await _scheduling_nudge("swap_submitted", employee["id"], payload.peer_employee_id)
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
        result = await store.answer_swap(employee["id"], request_id, True)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    # 申请人要知道对方回话了（他那边没有别的途径看到）；同意之后这条接着进店长待办。
    await _scheduling_nudge("swap_answered", result.get("employee_id"), employee["id"])
    return result


@router.post("/me/swaps/{request_id}/reject")
async def reject_swap(
    request_id: int,
    db=Depends(get_db),
    employee: dict = Depends(require_staff_session),
) -> dict:
    """拒绝跟我换班：这件事到此为止 —— 店长那边从头到尾看不到它，排班一个字不改。"""
    store = SchedulingStore(db)
    try:
        result = await store.answer_swap(employee["id"], request_id, False)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    # 同上：拒绝了也要让申请人知道（这件事到此为止，别让他一直等）。
    await _scheduling_nudge("swap_answered", result.get("employee_id"), employee["id"])
    return result


@router.get("/shifts")
async def list_shifts(db=Depends(get_db), _: dict = Depends(_require_scheduler)) -> dict:
    """班次表（白班、夜班……）。UI 按 N 个班次渲染，不写死两个。"""
    store = SchedulingStore(db)
    try:
        return {"shifts": await store.list_shifts()}
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.get("/shifts/manage")
async def manage_shifts(db=Depends(get_db), _: dict = Depends(_require_scheduler)) -> dict:
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
    _: dict = Depends(_require_scheduler),
) -> dict:
    """加一个班次（票 11）。加完就是一条普通的班次：月历图例、配规则的下拉、员工端
    的卡片都按 N 个班次渲染，不需要改代码（验收项）。"""
    store = SchedulingStore(db)
    try:
        return {"shift": await store.create_shift(payload.name, payload.sort_order, payload.duty_slot)}
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


# 声明在 `/shifts/{shift_id}` **之前**：两条都是 PUT，先声明的先匹配，否则
# `/shifts/order` 会被当成 `shift_id="order"` 去解析，回一个 422（英文的字段错误）。
@router.put("/shifts/order")
async def reorder_shifts(
    payload: ShiftOrderRequest,
    db=Depends(get_db),
    _: dict = Depends(_require_scheduler),
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
    _: dict = Depends(_require_scheduler),
) -> dict:
    """改名 / 调顺序 / 启用停用（票 11）。停用还有人在上的班次会被拦下来说清人数。"""
    store = SchedulingStore(db)
    try:
        return {
            "shift": await store.update_shift(
                shift_id, payload.name, payload.sort_order, payload.is_active, payload.duty_slot
            )
        }
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.delete("/shifts/{shift_id}")
async def delete_shift(
    shift_id: int,
    db=Depends(get_db),
    _: dict = Depends(_require_scheduler),
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
    _: dict = Depends(_require_scheduler),
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
    _: dict = Depends(_require_scheduler),
) -> dict:
    """某一天各班是谁（月历点开那天看的就是这个）。"""
    store = SchedulingStore(db)
    try:
        await store.expand()
        return await store.day_detail(date)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.get("/week")
async def week(
    start: Optional[str] = Query(None, description="YYYY-MM-DD"),
    db=Depends(get_db),
    _: dict = Depends(_require_scheduler),
) -> dict:
    """「员工 × 周」矩阵：这一周每人每天上什么班、在哪个区（票 A，只读）。

    跟 `/calendar`、`/day` 一样进门先补未来 90 天（幂等）：不补的话，翻到还没铺的
    那一周看到的是一片没有行的空格，而不是规则铺出来的结果。

    `start` 归一化到那一周的周一，缺省 = 今天所在那周；日期格式坏是 400。
    **读路径不广播 nudge**（见 `_scheduling_nudge` 的 docstring）。
    """
    store = SchedulingStore(db)
    try:
        await store.expand()
        return await store.week_grid(start)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc


@router.get("/roster")
async def roster(db=Depends(get_db), _: dict = Depends(_require_scheduler)) -> dict:
    """全体花名册 + 每人当前那条规则 + 每人每班次的固定工作区（没配就是 `{}`）。

    名单面板会一起读固定工作区（票 03），所以缺 0006 时这里也要出 503 那句话 ——
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
    _: dict = Depends(_require_scheduler),
) -> dict:
    """给「某人 × 某班次」定一个固定工作区；今天以后已经铺好的行一起改。

    工作区名单是卫生建的那份（公共层读出来），所以这边新建完区不用重启就能选到。
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
    _: dict = Depends(_require_scheduler),
) -> dict:
    """给一个人配轮转规则；今天以后的规则行立刻重铺，月历随即可见。"""
    store = SchedulingStore(db)
    try:
        result = await store.set_rule(employee_id, payload.cycle, payload.anchor_date)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    # 店长改了排班：当事人手机上的「今天」页该跟着变 —— 票 10 验收 4 在人机两侧都成立。
    await _scheduling_nudge("rule_changed", employee_id)
    return result


@router.delete("/rules/{employee_id}")
async def clear_rule(
    employee_id: int,
    db=Depends(get_db),
    _: dict = Depends(_require_scheduler),
) -> dict:
    """拿掉一个人的规则（连今天以后的规则行）。过去写过的不动。"""
    store = SchedulingStore(db)
    try:
        await store.clear_rule(employee_id)
    except SchedulingError as exc:  # pragma: no cover - 现在不会抛，留个一致的出口
        raise _bad_request(exc) from exc
    await _scheduling_nudge("rule_cleared", employee_id)
    return {"employee_id": employee_id, "rule": None}


@router.post("/overrides/batch")
async def batch_overrides(
    payload: BatchOverrideRequest,
    db=Depends(get_db),
    _: dict = Depends(_require_scheduler),
) -> dict:
    """先选一片、再一次刷完（票 14；ADR 0107 决策 5/6）。

    **一次校验、一次写锁、一次提交**，写完只播一次 nudge（前端照它整批只重拉一次周表）。
    能写的写、不能写的逐格进 `skipped`（`reason` 是定死的词表）—— 部分成功是预期语义，
    不是整批拒绝。`reminders` 是「这一格的人那天有申请等着批」：照刷，只提醒。

    整批拒绝只有三种：格数超上限、动作不认识、输入本身写错（日期 / 人员编号 / 班次 /
    工作区）—— 那些不是「某一格刷不了」，是这次请求不成立。

    让位给批量：`POST /overrides/batch` 与单格的 `PUT /overrides/{employee_id}/{business_date}`
    路径段数不同，不会互相遮住；声明顺序仍是批量在前，跟 `/shifts/order` 那条一个道理。
    """
    store = SchedulingStore(db)
    try:
        result = await store.set_overrides_batch(
            payload.action,
            payload.shift_id,
            payload.zone_id,
            [
                {"employee_id": item.employee_id, "business_date": item.business_date}
                for item in payload.items
            ],
        )
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    # 真写下去的那些人一人一条（`_scheduling_nudge` 自己去重）：nudge 不带数据，员工
    # 自己重拉那几天。`employee_ids` 只给这一行用，响应里不带出去 —— 契约就三个键。
    await _scheduling_nudge("overrides_batched", *result.pop("employee_ids", ()))
    return result


@router.put("/overrides/{employee_id}/{business_date}")
async def set_override(
    employee_id: int,
    business_date: str,
    payload: SetOverrideRequest,
    db=Depends(get_db),
    _: dict = Depends(_require_scheduler),
) -> dict:
    """改某人某一天：换班次、改成休、或只换工作区 —— **只动这一天**（票 07）。

    改过的那天在月历上有标记，改规则不会把它冲掉；撤掉覆盖（`DELETE` 同一条路径）
    那天就回到规则铺出来的样子。**过去 30 天内的日子照样能改**（票 14；ADR 0108）：
    漏排、补记发生在上周，这是店长唯一能补上的地方；只有超出可补记窗口的日子给 400
    `past_day`，未来那一头仍是展开窗口（`beyond_window`）。
    """
    store = SchedulingStore(db)
    try:
        result = await store.set_override(
            employee_id,
            business_date,
            is_rest=payload.is_rest,
            shift_id=payload.shift_id,
            zone_id=payload.zone_id,
        )
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    # 被临时调走（或被改成休）的人手机上的「今天」页要跟着变 —— 票 10 验收 6 的同一件事。
    await _scheduling_nudge("override_set", employee_id)
    return result


@router.delete("/overrides/{employee_id}/{business_date}")
async def clear_override(
    employee_id: int,
    business_date: str,
    db=Depends(get_db),
    _: dict = Depends(_require_scheduler),
) -> dict:
    """撤掉某人某天的覆盖，那天回到规则铺出来的样子（票 07）。

    **过去的日子也能撤**（2026-10-18 用户裁定，放开 ADR 0108 决策 6）：可补记窗口内
    （过去 30 天）与今天以后一样按现在的规则重算；更早的日子仍是 `past_day`。
    界面靠 `day_detail.undoable` 决定按钮显不显示。
    """
    store = SchedulingStore(db)
    try:
        result = await store.clear_override(employee_id, business_date)
    except SchedulingError as exc:
        # 日期格式不对、管理员没应用 0007 都会走到这里 —— 跟 PUT 那条一样的出口。
        raise _bad_request(exc) from exc
    await _scheduling_nudge("override_cleared", employee_id)
    return result


@router.get("/inbox")
async def inbox(db=Depends(get_db), _: dict = Depends(_require_scheduler)) -> dict:
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
    _: dict = Depends(_require_scheduler),
) -> dict:
    """批一条申请：请假那几天变成请假，换班两个人那天对调。

    换班批的是「两个人都点过头」的那条：对方那一步（`answer_swap`）是它进这扇门的条件。
    批准**只补写今天与以后的天**（跟店长手改的 30 天回溯窗口不是同一条：批得晚了就是
    晚了）：响应里的 `applied_days` / `skipped_days` 说清到底写了哪几天，界面照实说，
    不假装。
    """
    store = SchedulingStore(db)
    try:
        result = await store.approve_request(request_id)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    # 批完两个人都要知道：申请人（他那几天变了）、换班的对方（两个人的班对调了）。
    await _scheduling_nudge(
        "request_approved", result.get("employee_id"), result.get("peer_employee_id")
    )
    return result


@router.post("/inbox/{request_id}/reject")
async def reject_request(
    request_id: int,
    db=Depends(get_db),
    _: dict = Depends(_require_scheduler),
) -> dict:
    """驳回一条申请：排班一个字不改，只把申请记为驳回（票 08 的验收项；换班同理）。"""
    store = SchedulingStore(db)
    try:
        result = await store.reject_request(request_id)
    except SchedulingError as exc:
        raise _bad_request(exc) from exc
    # 驳回也要让申请人知道（他提的那条有结果了）。
    await _scheduling_nudge("request_rejected", result.get("employee_id"))
    return result
