#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""考勤表的 HTTP 适配层。口径在 `services/attendance/store.py`，这里只管翻译。

票 01 原本只有**一扇门**：管理端会话（`require_session`）—— 考勤表是店长的表，
里面有全店人的底薪与假期。**2026-10-31 用户裁定把它并入人事那一项能力**（`schedule`，
显示名「人事管理」）：管理端会话照旧直接放行，员工会话则要花名册上勾了「人事管理」
（票 02 起走边界层的 `schedule_edit`，与排班三页同一个动作）。页面清单一侧同步成 `staffCap: schedule`，
所以「页面能打开」与「接口能读」仍是两处都判。

票 06 给这扇门加了**第一个写动作**：逐日改格（把某天改成休、或换成另一个启用中的
班次）。它**没有第二套写排班的方式** —— 走的是排班服务层的单日覆盖
（`SchedulingStore.set_day_override`，结果行来源 `override`），这里只做三件事：
挑出那扇门、把那层的错误翻成人话、按排班既有惯例（`services/realtime/workbench.py` 的
`nudge_scheduling`）广播**一人一条**的排班通知。手填列在票 05。

票 04 加的是**封账上月**（`POST /api/attendance/seal`）：超级管理员点一次，上一个自然月
的存假结余就落进月度考勤台账（`services/attendance/store.py` 的 `AttendanceStore.seal`）。
只封已经过完的月份，封过的月不再封；这一条**不广播 nudge** —— 它改的是管理端那张表自己
的账，员工手机上没有任何东西跟着变。

票 08 加的是**一键导出**（`GET /api/attendance/sheet.xlsx`）：把同一份 `sheet()` 响应投影成
那份《厨房排班表》.xlsx 直接下发（`services/attendance/export.py`），**不落服务器、不推送**。
文件名（`考勤表-YYYY-MM.xlsx`）与表内月份都用**响应上的 `month`** —— 不拿服务器时钟另算一个，
否则跨零点那一刻页面上的月份与文件名会各说各话。

票 09 让**期末存假（本月剩余）可人工定稿**：那条月级写口多认一个 `closing_balance`
（当月未封账前随时可填可改），封账把它定稿、导出件的「存休天数」跟着走。**没有新接口、
没有新列、没有迁移** —— 这一票只在既有那扇门上多开一位，多出来的只有「封过账的那一行
只拒这一位」那一道闸（409 `month_sealed`）。`leave.balance` 的含义因此从「公式值」变成
「**有效值**」（手填 ?? 公式）：导出件读的仍是它，所以导出自动跟着走。
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from api.workbench_boundary import (
    WorkbenchActor,
    WorkbenchErrors,
    require_action,
    staff_actor,
)
from database import get_db
from services.realtime.workbench import nudge_scheduling as _scheduling_nudge
from services.attendance import (
    LEDGER_WRITE_FIELDS,
    AttendanceError,
    AttendanceStore,
    from_actor,
)
from services.attendance.export import (
    EXPORT_CONTENT_TYPE,
    attendance_export_filename,
    build_attendance_workbook,
)
from services.scheduling.store import (
    HALF_REST_NEEDS_SHIFT,
    HALF_REST_NEEDS_SHIFT_MESSAGE,
    SchedulingError,
    SchedulingStore,
)

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
    # 手填列（票 05）：四列一项都没点名。这不是「参数写错了」那么难懂 —— 页面上那颗
    # 保存按钮本来就是四项一起发，走到这里说明请求是手搓的（或表单全空没拿住）。
    "nothing_to_write": "这次没有任何一项要存：把要改的那几项填上再存一次",
    # 手填列那条月级写口（返工 ④）：`employee_id` 上没有人。文案与逐日改格那条
    # `unknown_employee`（`_WRITE_DETAILS`）逐字相同 —— 店长看到的是同一件事。
    "unknown_employee": "找不到这位员工：刷新页面重新选一次",
    # 期初存假（故事 35）那一位是数字：给了读不出来的东西就直说该怎么写。
    "invalid_opening_balance": (
        "期初存假要写一个数字（可以是负的、也可以带 .5）；要清空就把那一格留空"
    ),
    # 期末存假（票 09）同一条规矩 —— 两位的文案刻意各说各的名字，店长才分得清是哪一格。
    "invalid_closing_balance": (
        "期末存假要写一个数字（可以是负的、也可以带 .5）；要清空就把那一格留空"
    ),
    # 逐日写口（票 07 的「休半天」）那三句。带 `{}` 的那几句由 `_ATTENDANCE_ERRORS` 填细节。
    "invalid_business_date": "日期格式应该是 YYYY-MM-DD",
    "out_of_month": "考勤表只算当月：只能改 {} 所在那个月的格子",
    # 逐日写口的另外两道闸（收尾修复 ③）：故事 45 与故事 33 各一句。
    "future_day": "今天之后的日子不能改：{} 还没到，考勤表只记已经发生的班",
    # 封过账的月既锁逐日改格（票 06 的闸）也锁**期末存假**那一格（票 09 的写口）：
    # 同一句话把两件事都说到，所以点的是「格子与存假」，不只是格子。
    "month_sealed": "{} 已经封账了：封过的月只读，它里面的格子与存假都不再改",
    HALF_REST_NEEDS_SHIFT: HALF_REST_NEEDS_SHIFT_MESSAGE,
    # 员工端读自己那个月（ADR 0124）：月份格式写错直说该怎么写。
    "invalid_month": "月份格式应该是 YYYY-MM（例如 2026-10）",
    # 月份选择器（R2-A）：能回看历史月，但不预读还没开始的月。`{}` 是服务层放在
    # `args[0]` 的目标月份。
    "future_month": "只能看已经开始的月份：{} 还没到，考勤表不预读未来月",
}

_ERROR_STATUS = {
    # `not_migrated`（503）与 `month_sealed`（409）由共享例外表提供，本域不再复写。
    # 手填列的写口上那个 id **压根没有人**（返工 ④）：缺的是**资源本身**，不是参数格式
    # —— 404 比 400 诚实，也正是排班（`api/scheduling.py`）与加班（`api/overtime.py`）
    # 里同名 code 的状态码。
    "unknown_employee": 404,
}

# 逐日改格（票 06）的拒绝理由：单日覆盖那几条 + 这一页多出来的那一条。文案跟排班
# 月历**刻意不一样**：那边是「在月历上刷格子」，这边是「在考勤表卡片上点某一天」，
# 说「月历」会让店长去找一个他不在的页面。带 `{}` 的那几条由 `_WRITE_ERRORS` 填细节。
_WRITE_DETAILS = {
    "invalid_business_date": "日期格式应该是 YYYY-MM-DD",
    "unknown_employee": "找不到这位员工：刷新页面重新选一次",
    "unknown_shift": "班次不存在或已停用：请重新选一个班次",
    "unknown_zone": "工作区不存在：请先在卫生的工作区页面新建，或刷新本页",
    "past_day": "超出可补记窗口：{} 之前的日子不再改写",
    "beyond_window": "这天还没排到：排班只铺到 {}，等它进窗口再改",
    "rest_with_details": "「改成休」的那天不能再带班次或工作区：请把这两项留空",
    # 这一页多出来的那条闸（票 06 验收 3）：口头说清楚「谁在等、等什么」，
    # 让店长知道该先去待办里把那条申请处理掉。`{}` 由 `_WRITE_ERRORS` 填。
    "pending_request": "{}在等管理员批：先把它批掉或驳回，再改这一天",
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

# 封账（票 04）的拒绝理由。带 `{}` 的那两条由 `_SEAL_ERRORS` 填**月份**：店长要知道
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


# 三个 HTTP 出口的域对象（票 02）：文案表与状态覆盖仍是原来那三份数据，边界只多
# 了一件事 —— 每个由这里翻出来的拒绝都带 `X-Error-Code: <code>`。三条出口的缺省句、
# `{}` 的填法、`_PENDING_WORDS` 的说法都保持原样（ADR-0134）。
_ATTENDANCE_ERRORS = WorkbenchErrors(
    domain="考勤请求",
    texts=_ERROR_DETAILS,
    status_overrides=_ERROR_STATUS,
    default_text="考勤参数不合法",
)

_WRITE_ERRORS = WorkbenchErrors(
    domain="逐日改格",
    texts={
        **_WRITE_DETAILS,
        # 旧 `_write_error` 对未登记的 code 回退排班 `_bad_request`。逐日可达的 code
        # 里只有 `resigned_employee` 不在本表（离职日之后补一天），把它收进来之后
        # 那条回退不再可达；文案与 400 与排班那一层逐字一致。
        "resigned_employee": "这个人已经离职：离职日之后不再排班（当天仍是他最后一天）",
    },
    status_overrides={
        **_ERROR_STATUS,
        # 未决申请 = 与另一条请求冲突：409 比 400 诚实（`spec.md` Testing Decisions）。
        "pending_request": 409,
        # 共享表把 `unknown_*` 通配成 404；逐日写口上这两个一直是 **400** ——
        # 那里错的是「选错了班次 / 工作区」，不是「资源不存在」。
        "unknown_shift": 400,
        "unknown_zone": 400,
    },
    # 旧映射器对未登记的 code 还会回退排班那句「排班参数不合法」；补上
    # `resigned_employee` 之后没有可达的未登记 code，这条防御路径只剩共享缺省句。
    pending_words=_PENDING_WORDS,
)

_SEAL_ERRORS = WorkbenchErrors(
    domain="封账",
    texts={**_ERROR_DETAILS, **_SEAL_DETAILS},
    status_overrides={**_ERROR_STATUS, **_SEAL_STATUS},
    default_text="这次封账没成",
    missing_word="这个月",
)

# 管理端那一侧的五条路由共用这一个依赖（管理端放行；员工要「人事管理」）。
_schedule_edit = require_action("schedule_edit")


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


class ManualColumnsRequest(BaseModel):
    """那条**月级写口**的输入：四个**手填列**（票 05）+ **期初存假**（故事 35）+
    **期末存假**（票 09）。

    系统里没有这几项（花名册里压根没有「是否住宿 / 借调门店 / 是否离职」这几个字段），
    所以由超管在卡片上填一次、存在月度考勤台账**当月那一行**上。

    **`None` 是「清空」，缺字段是「不碰」** —— 两者在 JSON 里都是「没给值」之外的两件事，
    但 `Optional[...] = None` 分不开它们，所以这一层用 `model_fields_set` 拿「这次点名了
    哪几列」，只把那几列交给服务层。清空存 NULL，与从没填过同义：页面上显示为空，
    「本月班次」那一格退回当月众数。

    `opening_balance`（期初存假 / 上月末余额）是故事 35 让超管**上线当月手工给一次**的
    那一位：写在本月那一行上，可为负（欠班）、可带 .5，显式 `null` 即清空。
    `closing_balance`（期末存假 / 本月剩余，票 09）是它的另一半：当月随时可填可改，
    填了就以它为准（卡片上的 `leave.balance` = 有效值、导出件的「存休天数」跟着走），
    显式 `null` 即清空回公式值。**它落在封账也写的那一列上**：那一行封过账之后只拒它
    这一个字段（409 `month_sealed`），同一次点名别的字段照旧可写（05 的裁定）。
    它与其他几位共用这一条 upsert —— 一次点名一列，谁也不覆盖谁。数值本身由 `float`
    这一档把着：给了非数字是 pydantic 的 422，不是悄悄存一个 0。
    """

    shift_name: Optional[str] = None
    lodging: Optional[str] = None
    borrowed_store: Optional[str] = None
    resigned: Optional[str] = None
    opening_balance: Optional[float] = None
    # 期末存假（本月剩余，票 09）：与期初对称的那一位，写在本月那一行的
    # `closing_balance` 上（封账也写这一列，见 `_ERROR_DETAILS` 的 `month_sealed`）。
    closing_balance: Optional[float] = None


@router.get("/sheet")
async def attendance_sheet(
    month: Optional[str] = None,
    db=Depends(get_db),
    _actor: WorkbenchActor = Depends(_schedule_edit),
) -> dict:
    """某一个月那一张考勤表（一人一张卡）。

    `month=YYYY-MM` 可选：省略就是**本月**（与 2026-11-10 之前的行为一致）；给了
    就是那一个月 —— 历史月只读回看，数字仍按现有排班/加班/台账**重算**，不是封账
    快照（封账状态/时间只从台账的 `sealed_at` 读）。未来月拒绝（400 `future_month`），
    格式不对 400 `invalid_month`。权限与这一页其它门口径相同（`schedule_edit`）。
    """
    store = AttendanceStore(db)
    try:
        return await store.sheet(month)
    except AttendanceError as exc:
        raise _ATTENDANCE_ERRORS.http(exc) from exc


@router.get("/me")
async def my_leave(
    month: Optional[str] = None,
    db=Depends(get_db),
    actor: WorkbenchActor = Depends(staff_actor),
) -> dict:
    """员工看**自己**某个月的存假（整月页那张卡，ADR 0124）。

    与上面那条是两扇门、两个口径：`/sheet` 是店长的全店表（要「人事管理」能力），
    这一条只认员工会话，**接口上没有 `employee_id`** —— 员工读得到的只有自己那一行
    （同 `/api/scheduling/me/month` 的边界）。`month` 只决定看哪个月，决定不了看谁；
    整月页能翻月，所以月份是参数，不给就是本月。
    """
    store = AttendanceStore(db)
    try:
        return await store.leave_summary(from_actor(actor), month)
    except AttendanceError as exc:
        raise _ATTENDANCE_ERRORS.http(exc) from exc


@router.get("/sheet.xlsx")
async def export_attendance_sheet(
    month: Optional[str] = None,
    db=Depends(get_db),
    _actor: WorkbenchActor = Depends(_schedule_edit),
) -> Response:
    """一键导出某个月的考勤表（票 08）：一份 `.xlsx` 直接下发，**不落服务器、不推送**。

    读的还是 `AttendanceStore.sheet()` 那**同一个读口** —— 页面上看到的与导出的表是同一份
    投影，不存在「导出的数跟页面对不上」这条路。版式在 `services/attendance/export.py`。
    `month` 与页面的月份选择器同步：回看哪个月就导出哪个月，省略仍是本月。

    月份取**响应上的** `month`（不拿服务器时钟另算）：文件名与表内月份因此与那张卡片
    同源。文件名按 RFC 5987 的 `filename*=UTF-8''…` 给（中文名不靠 ASCII 兜底），前端
    `api.download` 优先读它。

    鉴权与这一页的其它门一样是 `schedule_edit`：管理端会话直接放行，员工会话要在
    花名册上勾了「人事管理」—— 这一份表里有全店人的底薪与假期，所以它与页面同一道闸。
    """
    store = AttendanceStore(db)
    try:
        sheet = await store.sheet(month)
    except AttendanceError as exc:
        raise _ATTENDANCE_ERRORS.http(exc) from exc

    filename = attendance_export_filename(str(sheet["month"]))
    logger.info("导出考勤表: month=%s rows=%s", sheet["month"], len(sheet.get("employees") or []))
    return Response(
        content=build_attendance_workbook(sheet),
        media_type=EXPORT_CONTENT_TYPE,
        headers={
            # `quote` 只做一次编码：文件名里没有引号/换行，但仍按 RFC 3986 转义一遍
            # （同 `api/recipes.py` 的导出：将来改名也不会把响应头写坏）。
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            # 导出件是「此刻的暂算值」，别让反向代理/浏览器缓存住它。
            "Cache-Control": "no-store",
        },
    )


@router.put("/sheet/{employee_id}/manual")
async def set_attendance_manual_columns(
    employee_id: int,
    payload: ManualColumnsRequest,
    db=Depends(get_db),
    _actor: WorkbenchActor = Depends(_schedule_edit),
) -> dict:
    """把某人**本月的四个手填列**（外加期初 / 期末存假）存进月度考勤台账（票 05 + 35 + 09）。

    写在本月那一行上（`UNIQUE(租户, 员工, 月份)`，upsert）：这一次点名了哪几列就只写哪几
    列，没点名的一列都不碰 —— 票 04 写的 `sealed_at` 也在这同一行上，谁也不整行覆盖谁。
    清空某一项存 NULL（与从没填过同义）。

    `opening_balance`（期初存假）走的是**同一条** upsert：写的是**本月那一行**的
    `opening_balance`（票 03 的裁定：人工期初读的就是本月这一行），只在上线当月手工给一次。

    `closing_balance`（期末存假 / 本月剩余，票 09）也走这一条：当月未封账前随时可填可改，
    填了卡片上的 `leave.balance`（有效值）与导出件的「存休天数」就以它为准。它落在封账
    也写的那一列上 —— 那一行 `sealed_at` 非空时**只拒它这一个字段**（409 `month_sealed`，
    文案见 `_ERROR_DETAILS`），同一次点名别的字段照旧可写（05 的裁定）。

    **这里没有任何 nudge**：这几个值只进卡片与（票 08 的）导出件，员工手机上不显示它们，
    所以没有第二个端要重拉。它们也**不写回员工档案** —— 档案里没有这几个字段。

    `employee_id` 上必须真有一个员工（返工 ④）：不然这条写口会在台账里建出一行**孤儿**
    （`sheet()` 念花名册，永远不显示它）。不存在的 id 一律 **404**（`unknown_employee`，
    同排班 / 加班那两条），而不是 400。
    """
    store = AttendanceStore(db)
    # `model_fields_set` 是「这一次点名了哪几列」的判据：缺字段 = 不碰，显式 `null` = 清空。
    values = {
        field: getattr(payload, field)
        for field in payload.model_fields_set
        if field in LEDGER_WRITE_FIELDS
    }
    try:
        return await store.set_manual_columns(employee_id, values)
    except AttendanceError as exc:
        raise _ATTENDANCE_ERRORS.http(exc) from exc


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
    _actor: WorkbenchActor = Depends(_schedule_edit),
) -> dict:
    """把**已经过完的**某个自然月的存假结余写进月度考勤台账并定稿（票 04 / ADR 0113）。

    不给 `month` 就是上一个自然月 —— 页面上那颗「封账上月」按钮的全部输入就这一条。
    写下去的那一行（`closing_balance` + `sealed_at`）从此就是当月卡片上「上月存假」的
    来源：之后再改上月的排班，封下的结余不跟着漂。

    只封已经过完的月份（当月与未来 400）、封过的月不再封（409），那些理由都翻成中文
    由 `_SEAL_ERRORS` 给。缺表（0027 没应用）照旧是一个点名脚本的 503。

    **不广播 nudge**：封的是管理端这张表自己的账，员工手机上的班与假一个字都没变。
    """
    store = AttendanceStore(db)
    try:
        return await store.seal(payload.month if payload else None)
    except AttendanceError as exc:
        raise _SEAL_ERRORS.http(exc) from exc


@router.put("/sheet/{employee_id}/{business_date}")
async def set_attendance_day(
    employee_id: int,
    business_date: str,
    payload: DayEditRequest,
    db=Depends(get_db),
    _actor: WorkbenchActor = Depends(_schedule_edit),
) -> dict:
    """把某人某一天改成休、换成另一个启用中的班次（票 06），或标「休半天」（票 07）。

    **改这一天**（票 06）复用排班的单日覆盖（`SchedulingStore.set_day_override`）：不动
    他的轮转规则、不动同一个月的别的日子、不动下个月，超出自可补记窗口的日子照旧拒
    （`past_day`）。那天还有**没批的**换班 / 请假申请时**拒绝**（409）且一个字都不写 ——
    店长不该在这一页把员工刚提的请求悄悄冲掉。

    **「休半天」**（票 07，ADR 0114）走的是另一条路（`SchedulingStore.set_half_rest`，
    ADR 0143）：它只改结果行上的那个标志，**不是**一次单日覆盖 —— 那天仍然挂本来那个
    班次、仍然只有一行、`source` 也不变（所以它没有「改完这天就跟规则不一样了」那个
    含义）。这是考勤表页这扇单日门；排班周表另有批量口（ADR 0122）。写实现与判据都在
    排班域，这一层只做翻译与广播。

    两条路写完都按排班既有惯例广播：`_scheduling_nudge` 一人一条、scope 带他自己的
    `employee_id`（员工连接只收得到与自己有关的那条，见 `services/realtime/hub.py`）。
    nudge 不带数据，员工手机收到后自己重拉 —— 与月历上改一天完全同一条链路。

    **收尾修复 ③：分派之前先过三道服务端闸**（`AttendanceStore.ensure_day_editable`）——
    非当月（这一页只算当月）、今天之后（故事 45）、那个月已经有封账行（故事 33）。
    它们加在**分派之前**，所以上面两条支路的门是同一条：改班次、改成休、标「休半天」
    一个都绕不过去。写实现搬进排班域之后这几道闸更不能再少（ADR 0143）。
    """
    # 三道闸在**任何写动作之前**：被拒的那一次一个字节都不写。
    try:
        day = await AttendanceStore(db).ensure_day_editable(business_date)
    except AttendanceError as exc:
        raise _ATTENDANCE_ERRORS.http(exc) from exc
    if payload.half_rest is not None:
        # 用闸返回的规整日期：旧实现（`AttendanceStore.set_half_rest`）就接受
        # `date.fromisoformat` 认得的写法再回显 `YYYY-MM-DD`；路径参数本身没有
        # 格式约束，直接透传会让 `20260901` 这类旧调用在排班侧的严格正则上变 400。
        return await _set_half_rest(
            db, employee_id, day, bool(payload.half_rest), payload
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
        raise _WRITE_ERRORS.http(exc) from exc
    # 被临时调走（或被改成休）的人手机上的「今天」页要跟着变 —— 跟月历那条 PUT 一样。
    await _scheduling_nudge("override_set", employee_id)
    return result


async def _set_half_rest(
    db, employee_id: int, business_date: str, half_rest: bool, payload: DayEditRequest
) -> dict:
    """「休半天」那一支：先挡住「一次改两件事」，再交给排班域唯一的写口。

    `is_rest` 与 `shift_id` 与 `half_rest` 一起发是 400 —— 「改成休，但只休半天」不是
    一句说得通的话（那天到底上不上班？），服务层也不该替调用方猜。

    写实现在 `SchedulingStore.set_half_rest`（ADR 0143）：这一层只把「那天没有班次」
    翻成 `_ATTENDANCE_ERRORS` 里那一句并广播。三道服务端闸已经在 `set_attendance_day`
    分派之前跑过，这里不重复；传进来的 `business_date` 是闸规整过的 `YYYY-MM-DD`。
    """
    if payload.is_rest or payload.shift_id is not None:
        raise HTTPException(
            status_code=400,
            detail="「休半天」是一次单独的动作：不要再带班次或「改成休」；"
            "要换班次 / 改成休就单独发一次",
        )
    store = SchedulingStore(db)
    try:
        reason = await store.set_half_rest(employee_id, business_date, half_rest)
    except SchedulingError as exc:
        # 写实现搬进排班域后，缺表 / 缺列由那边的 `_needs_migration` 换成了
        # `SchedulingError("not_migrated")`；这一层仍按考勤的错误表翻回 503，
        # 不能让店长看到一个 500 traceback（ADR 0143）。
        raise _ATTENDANCE_ERRORS.http(AttendanceError(exc.code, str(exc))) from exc
    if reason is not None:
        raise _ATTENDANCE_ERRORS.http(AttendanceError(reason, business_date))
    # 员工手机上那一屏要跟着变（班次没变，但那天从「上班」变成了「上半天」）。
    await _scheduling_nudge("half_rest_set", employee_id)
    return {
        "employee_id": int(employee_id),
        "business_date": business_date,
        "half_rest": bool(half_rest),
    }
