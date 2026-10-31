#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班：规则 → 未来 90 天的排班结果。

排班是**独立系统**（`spec.md` 的「分层」）：这里不 import 卫生（`services.hygiene.*`），
也不认识卫生的表名；它从公共层取「这个人是谁」（`services.identity`）、算自己的规则，
把结果**物化**进 `staff_assignments`。下游只读那份数据，两边靠数据对接而不靠调用。

五件事在这一层：

- **班次**（`staff_shifts`）：白班、夜班是**数据**不是常量，加第三个班次是改数据。
  票 11 给了店长编辑它的界面：改名、调显示顺序、启用停用、删掉刚建错的那一条
  （用过的删不掉，只能停用 —— 历史排班照旧显示）。
- **规则**（`scheduling_rules`）：一人一条轮转规则 —— `cycle` 的长度就是周期天数，
  每格是班次 id、`None` 表示休；`anchor_date` 是周期起点，相位由它和营业日之差算。
- **展开**（`expand`）：把规则铺成 `staff_assignments` 的行，滚动铺未来 90 天。
- **请假**（`scheduling_requests`，`kind=leave`）：员工提申请、店长批 / 驳（票 08）。批准的那几天
  写成**单日覆盖**（`kind=leave`、没有班次），于是「请假」与「本来就休」在结果表里有据可查
  —— 两者的 `shift_id` 都是空，区别只在覆盖记录的 `kind`。
- **换班**（同一张表，`kind=swap`）：员工指一个同事和一天，**对方先同意**才进店长待办，
  店长批了两人那天的班对调（票 09；工作区跟着各自的新班次走）。对方拒绝或申请人撤回，
  排班一个字不变。

**已经写下的行不重算**：展开时窗口内哪天（哪个人）已经有行就跳过。所以「改规则只影响
今天以后」是这个做法本身的结果，不是额外加的一段逻辑 —— 过去那些行早就写在那儿，
展开不会回头补写（`spec.md` 的「展开」）。它们仍可以被**补记**：店长手改一格
（`set_override`）或一次刷一片（`set_overrides_batch`）在近 `BACKFILL_DAYS` 天内动得了，
再往前就不改（ADR 0108）。

营业日复用 `services.business_day.py`（06:00 切日），不另起一套。
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

import asyncpg

from db_core.utils import CHINA_TZ
from services.business_day import current_workbench_date, shift_business_date
from services.identity import EmployeeAccounts, ZoneDirectory, serialized_write

logger = logging.getLogger(__name__)


def _needs_migration(method):
    """表还没建（0005 没应用）时，把 asyncpg 的缺表异常换成一个能照做的错。

    `prepare()` 已经不在启动期拦人（见那里的注释）。真正用到排班的那一刻也不该是
    一个 500 traceback —— 店长要看到的是「去 Admin『系统更新 → 数据库迁移』应用
    0005」。顺带把失败的事务回滚掉，否则这条连接后面的查询会全跟着报
    `current transaction is aborted`。
    """

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        try:
            return await method(self, *args, **kwargs)
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            raise SchedulingError("not_migrated", str(exc)) from exc

    return wrapper

__all__ = [
    "BACKFILL_DAYS",
    "DEFAULT_SHIFTS",
    "DUTY_SLOTS",
    "EXPANSION_DAYS",
    "KIND_LEAVE",
    "KIND_MANUAL",
    "KIND_SWAP",
    "MAX_BATCH_CELLS",
    "MAX_CYCLE_DAYS",
    "MAX_REQUEST_NOTE",
    "MAX_SHIFT_NAME",
    "REST",
    "SHIFT_COLORS",
    "SHIFT_COLOR_KEYS",
    "SHIFT_COLOR_NEUTRAL",
    "SOURCE_OVERRIDE",
    "SOURCE_RULE",
    "STATUS_APPROVED",
    "STATUS_CANCELLED",
    "STATUS_PENDING_MANAGER",
    "STATUS_PENDING_PEER",
    "STATUS_REJECTED",
    "SchedulingError",
    "SchedulingStore",
]

# 排班窗口：从今天起铺这么多天（含今天）。
EXPANSION_DAYS = 90

# 可补记窗口：今天**往前**这么多天（含）以内，过去的日子还能补记 / 改（ADR 0108）。
# **一处定义**：单日覆盖的 `past_day` 与批量端点的逐格跳过都读它。
# 与 `EXPANSION_DAYS` 是两个方向的窗口，别并成一个数 —— 未来那头照旧只到展开窗口。
BACKFILL_DAYS = 30

# 一次批量落笔的上限（格）。超了**整批拒绝**：那不是「某一格刷不了」，是这次请求本身
# 太大（前端照这个数把选区卡住，服务端这道才是权威）。
MAX_BATCH_CELLS = 200

# 排班结果这一行是谁写的。
SOURCE_RULE = "rule"
SOURCE_OVERRIDE = "override"

# 单日覆盖是谁写的（`scheduling_overrides.kind`）：`manual` 是店长在月历上点着改的，
# `leave` 是批准请假写下的（票 08）。票 09 的换班写 `swap` —— 它们进的是同一张表
# （见 `migrations/pg/0007_scheduling_overrides.sql`），不用再出一次迁移。
KIND_MANUAL = "manual"
KIND_LEAVE = "leave"
KIND_SWAP = "swap"

# 批量落笔（`POST /overrides/batch`）的动作：整批同一个。
#
# `restore` 是**退回轮转规则**（摘掉覆盖记录 + 按现在的规则重算那天），不是「那天休」
# —— 后者是 `rest`。`clear` 是它的**兼容别名**（2026-10-29 前的前端只会发这个），
# 两个词落到同一段逻辑上，别再长出第二套。
#
# `void` 是**清空这一天**（2026-10-29 用户裁定）：「还没排到」的空白，不是休、也不是
# 退回规则。它写一条 `voided=1` 的覆盖行并删掉那天的结果行（见 0040 迁移的头注释）。
#
# `half_rest` 只改那天的「休半天」标志（ADR 0114），不换班次、不换工作区、不改来源
# —— 与考勤表页 `AttendanceStore.set_half_rest` 同一口径。
BATCH_SHIFT = "shift"
BATCH_REST = "rest"
BATCH_CLEAR = "clear"
BATCH_RESTORE = "restore"
BATCH_VOID = "void"
BATCH_HALF_REST = "half_rest"
BATCH_ACTIONS = (
    BATCH_SHIFT,
    BATCH_REST,
    BATCH_CLEAR,
    BATCH_RESTORE,
    BATCH_VOID,
    BATCH_HALF_REST,
)

# 批量回执里「这个人这天有申请等着批」那一条的 kind（前端照它渲染提醒）。
# 提醒**不阻断**写入：申请批下来会把那天换成请假 / 对调（`_write_override_row` 的既有
# 语义），刷不刷由店长定（ADR 0107 决策 6）。
REMINDER_PENDING_REQUEST = "pending_request"

# 申请的状态（`spec.md` 的状态机）。`pending_peer` 是换班专用（票 09）：提出来先等对方
# 点头，对方同意了才轮到店长（`pending_manager`）；请假从「等店长批」直接到批 / 驳，
# 申请人自己撤回落 `cancelled`。
STATUS_PENDING_PEER = "pending_peer"
STATUS_PENDING_MANAGER = "pending_manager"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"

# 事由限长：界面上那张卡是一行话的地方，不是留言板。超了报错而不是悄悄截断 ——
# 员工写的东西不该被系统改掉。
MAX_REQUEST_NOTE = 50

# 申请的下班点（2026-10-29 用户裁定）：
#  · **22:00 之后不能再提「次日」的假或换班** —— 店长这个点已经把人排定了，批下来等于
#    半夜改明天的班。截止时刻是「那一天的前一晚 22:00」，**当天的假不受限**（生病没法
#    提前请，这条口子得留着；判据只看 `day > today`）。
#  · **2 天以上的假要提前 7 天**（从营业日算：最早只能从那一天起请）。
NEXT_DAY_CUTOFF_HOUR = 22
MULTI_DAY_LEAVE_LEAD_DAYS = 7

# 读申请时选的列：`_request_rows` 与 `_request_by_id` 共用一份，免得两处列名漂移。
# `peer_employee_id`（0009）另有一份「没有它」的写法：半迁移（0008 应用了、0009 没应用）
# 时申请要照常读得出来，见 `_select_request_rows`。
_REQUEST_COLUMNS = (
    "id, employee_id, kind, start_date, end_date, status, note, decided_at, created_at, updated_at,"
    " peer_employee_id"
)
_REQUEST_COLUMNS_NO_PEER = (
    "id, employee_id, kind, start_date, end_date, status, note, decided_at, created_at, updated_at"
)

# 员工「今天」页往后看几天：今天、明天、后天、大后天（原型 A 的「往后三天」）。
# 窗口长度是服务层的数，前端不再写一份 —— 以后要改成「往后一周」只动这里。
MY_WINDOW_DAYS = 4

# 规则里「那天休」那一格。
REST = None

# 周期长度的上限。轮转是给人看的（「白白白夜夜休休」= 7 天），几十天已经荒谬；
# 定一个上限是为了让 `cycle` 这个 JSON 文本有界（输入校验，不是性能考虑）。
MAX_CYCLE_DAYS = 60

# 首次启动时放进班次表的默认两条（`migrations/pg/0005_scheduling.sql` 里也有同样的
# 两行，那是给「装完就看」的；见 `prepare()` 为什么还留了一手）。第三个值是**卫生档位**
# （票 10）：`0010` 迁移回填的是同样两条，表被手工清空后由这里重建出来的也得一样 ——
# 少了档位，那家店的员工当天就交不了任何日常检查。
DEFAULT_SHIFTS = (("白班", 10, "day"), ("夜班", 20, "night"))

# 班次名的上限（票 11）：这个名字要出现在月历图例、规则编辑的胶囊、当天名单里，
# 都是窄地方。上限是服务层的数，编辑页从 `/shifts/manage` 拿到它（`max_name`），
# 前端不再写死一份 —— 跟 `MAX_CYCLE_DAYS` 随 `/roster` 下去是同一个口径。
MAX_SHIFT_NAME = 12

# 显示顺序的步长：新班次排在最后（当前最大 + 10）；「上移 / 下移」是整表重排，
# 按 10、20、30… 重新铺一遍 —— 留出空档，手工插一行不用把后面全改。
SHIFT_ORDER_STEP = 10

# 班次颜色（0043，契约 `.scratch/scheduling-shift-color/spec.md`）：8 个**语义色键**，
# 具体色值在 `theme.workbench.css` 的 `--shift-<key>-*` 里，深浅两主题共用同一套。
# 前 6 个是主色，后 2 个只在前 6 个都被占用时才启用 —— 排除红与琥珀（那是 danger / warn
# 的地盘）之后，8 个色相拉不开足够的色差，所以分了两档优先级。
# `neutral` 是哨兵：可重复、不占唯一额，「第 9 个启用班次」与读取端兜底都用它。
SHIFT_COLORS = ("moss", "mint", "tide", "sky", "pine", "rose", "grass", "lilac")
SHIFT_COLOR_NEUTRAL = "neutral"
SHIFT_COLOR_KEYS = SHIFT_COLORS + (SHIFT_COLOR_NEUTRAL,)

# 读班次行时用的列。`_query_shifts` 会在 `color_key` 这一列还不存在时退回不带它的那份。
_SHIFT_COLUMNS = "id, name, sort_order, is_active, duty_slot"
_SHIFT_COLUMNS_COLOR = _SHIFT_COLUMNS + ", color_key"

# 「休」这一族写法留给「那天休息」：月历的周期编辑页**先认班次名、认不出才当休息**
# （`SchedulingCalendarView.vue` 的 `REST_WORDS`），所以店里真有个班次叫「休」的话，
# 那家店就再也没法用这些词写休息了。票 04 审查把这件事转出给票 11，这里收口：
# 建班次与改名都不许用这些词（停用的班次一样不许改叫它们）。
RESERVED_SHIFT_NAMES = ("休", "休息", "空", "x", "X", "-", "—")

# 卫生档位（票 10）：排到这条班次的人，做哪一档卫生日常检查。**按班次 id 认，不按名字** ——
# 店长把「夜班」改成「晚班」，卫生那边不该跟着断（`migrations/pg/0010_shift_duty_slot.sql`
# 里写了这一列为什么存在）。空 = 这条班次不出日常检查：新班次默认如此，店长没说是哪一档，
# 就不替他决定 —— 那天的人交不了日常、页面上说去找店长，比猜错一档、把白班的检查单
# 发给夜班的人要好。
DUTY_SLOTS = ("day", "night")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

# 班次名里不许出现的东西：周期编辑器把店长写的那串字**按这些字符切词**
# （`SchedulingCalendarView.vue` 的 `parseCycle` 用 `/[\s,，、·/]+/`），名字里带了它们，
# 这条班次在界面上就永远写不进周期；而名字恰好切出两个合法班次名时（「白班/夜班」），
# 编辑器还会把「固定上这个班」静默读成两班轮转。所以挡在写入这一侧。
# 空白一律算（含全角空格、NBSP）：JS 的 `\s` 认的那些，Python 的 Unicode `\s` 只多不少。
_SHIFT_NAME_SEPARATORS = re.compile(r"[\s,，、·/]")


class SchedulingError(ValueError):
    """排班层的输入错误。``code`` 是给 API 层用的稳定标识，不是给员工看的文案。"""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


def _unique_shift_error(exc: Exception) -> SchedulingError:
    """班次表的唯一冲突分两种：重名（0005 的 UNIQUE）与撞色（0043 的部分唯一索引）。

    按约束名分辨，不按错误文本 —— 文本随 PostgreSQL 语言设置变。
    """
    if getattr(exc, "constraint_name", "") == "uq_staff_shifts_active_color":
        return SchedulingError("shift_color_taken", "")
    return SchedulingError("shift_name_taken", "shift_name_taken")


def _shift_payload(mapping: dict, color_ready: bool = True) -> dict:
    """一行班次 → 接口形状。`color_key` 为 `None` 表示「库里还没有这一列」，
    前端据此降级成中性色 + 提示条（跟「显式选了 neutral」是两件事）。"""
    return {
        "id": int(mapping["id"]),
        "name": mapping["name"],
        "sort_order": int(mapping["sort_order"]),
        "is_active": bool(int(mapping["is_active"] or 0)),
        # 卫生档位（票 10）：'day' / 'night' / None（这条班次不出日常检查）。
        "duty_slot": mapping.get("duty_slot"),
        # 班次颜色（0043）：色键见 SHIFT_COLORS；None = 迁移还没应用。
        "color_key": (mapping.get("color_key") if color_ready else None) or None,
    }


def _require_business_date(value: Any) -> str:
    text = str(value or "")
    if not _DATE_RE.match(text):
        raise SchedulingError("invalid_business_date", "invalid_business_date")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise SchedulingError("invalid_business_date", "invalid_business_date")


def _is_half_rest(shift_id: Any, value: Any) -> bool:
    """这一行是不是「休半天」（票 07 / ADR 0114）：**那天得有班次**，标志才作数。

    `half_rest` 是 BIGINT 0/1（仓库口径的布尔，同 `staff_shifts.is_active`）。写路径
    （`_write_day_row`）把「改成休」那天的标志清成 0，所以正常情况下不会出现
    「`shift_id` 空 + 标志为 1」的行；这里对读到的行再挡一次 —— 手工 SQL / 历史数据里
    那种行不该在页面上变成「休半天」（`休 + 休半天` 是自相矛盾的一句话）。
    """
    if shift_id is None:
        return False
    return bool(int(value or 0))


def _clean_shift_name(value: Any) -> str:
    """班次名去掉两头空白；空、太长、保留词、带分隔符各报一句能照做的错（票 11）。

    不悄悄截断：名字是店长写下的东西，被系统改掉比报错更难受（事由 `_clean_note` 同理）。

    **分隔符（`_SHIFT_NAME_SEPARATORS`）必须在写入这一侧挡住**：周期编辑器按它们切词，
    带分隔符的名字写不进周期。报错时把那个字符回给接口层（空白回「空格」，好读），
    文案在 `api/scheduling.py` 的 `_ERROR_DETAILS` 里点名。
    """
    text = str(value or "").strip()
    if not text:
        raise SchedulingError("missing_shift_name", "missing_shift_name")
    if len(text) > MAX_SHIFT_NAME:
        raise SchedulingError("shift_name_too_long", str(MAX_SHIFT_NAME))
    if text in RESERVED_SHIFT_NAMES:
        # 把那个词回给接口层，好让文案点名（「休」和「空」的处理办法不一样）。
        raise SchedulingError("shift_name_reserved", text)
    separator = _SHIFT_NAME_SEPARATORS.search(text)
    if separator is not None:
        found = separator.group()
        raise SchedulingError("shift_name_charset", "空格" if found.isspace() else found)
    return text


def _shift_order(value: Any) -> Optional[int]:
    """显示顺序：空 = 让服务层决定（新班次排最后）；非整数报一句人话，不猜。"""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise SchedulingError("invalid_shift_order", "invalid_shift_order")


def _clean_duty_slot(value: Any) -> Optional[str]:
    """卫生档位（票 10）：`day` / `night` / 空（这条班次不出日常检查）。

    空串与 `None` 都是「不出日常」—— 调用方用 `None` 表示「这一项不动」（跟班次名、
    顺序、启用位一个口径），用空串表示「清空成不出日常」，所以这里两种都收。
    """
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text not in DUTY_SLOTS:
        raise SchedulingError("invalid_duty_slot", "invalid_duty_slot")
    return text


def _clean_color_key(value: Any) -> Optional[str]:
    """班次颜色键（0043）：`moss` / `mint` / … / `neutral`。

    `None` = **这一项不动**（跟班次名、顺序、档位一个口径）；空串与 `neutral` 都是
    「中性色」（超出调色板时的兜底值）；不在白名单里报 `invalid_shift_color`。
    新建班次时 `None` 另有含义：交给 `_next_free_color()` 自动挑一个空闲主色。
    """
    if value is None:
        return None
    text = str(value).strip()
    if text in ("", SHIFT_COLOR_NEUTRAL):
        return SHIFT_COLOR_NEUTRAL
    if text not in SHIFT_COLOR_KEYS:
        raise SchedulingError("invalid_shift_color", "invalid_shift_color")
    return text


def _month_start(month: Any) -> date:
    text = str(month or "")
    if not _MONTH_RE.match(text):
        raise SchedulingError("invalid_month", "invalid_month")
    try:
        return date.fromisoformat(f"{text}-01")
    except ValueError:
        raise SchedulingError("invalid_month", "invalid_month")


def _month_bounds(month: Any) -> tuple[date, date]:
    """月历的两端：本月第一天、下月第一天（店长与员工两版月历共用）。

    下月靠 `date` 自己进位：先把日期挪到 28 号再加 4 天，一定落到下个月，
    所以不用管本月是 28/29/30/31 天。格式不对由 `_month_start` 抛 `invalid_month`。
    """
    start = _month_start(month)
    return start, (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def _each_day(start: date, next_month: date):
    """按月历顺序吐出这个月的每一天（含 `start`、不含 `next_month`）。"""
    day = start
    while day < next_month:
        yield day
        day += timedelta(days=1)


def _each_day_between(first: str, last: str) -> list[str]:
    """吐出 `first` 到 `last` 之间的每一天（**两端都算**，ISO 文本进、ISO 文本出）。

    请假的区间是闭区间：员工说「9/28 到 9/29」就是这两天都不上班
    （原型 C 的「请假 9/28–9/29」也是这个说法）。
    """
    start = date.fromisoformat(first)
    end = date.fromisoformat(last)
    out = []
    day = start
    while day <= end:
        out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def _clean_note(note: Any) -> Optional[str]:
    """事由：去掉两端空白；空串就是「没写」（存 NULL，不存一个空字符串）。"""
    text = str(note or "").strip()
    if not text:
        return None
    if len(text) > MAX_REQUEST_NOTE:
        raise SchedulingError("note_too_long", str(MAX_REQUEST_NOTE))
    return text


def _swap_couple_text(row: dict, people: dict) -> str:
    """「谁 和 谁（到哪一步了）」—— 换班撞车时端给员工的那半句。

    名字从花名册里取（`people` 是 `{id: employee}`），取不到就退回手机号，再不行给个
    「员工 12」：这句话的用处是让人看懂「这天谁跟谁已经在换了」，缺名字就不成立了，
    但也不能为了一个名字把整条申请打成 500。
    """
    def name(employee_id: Any) -> str:
        person = people.get(int(employee_id)) or {}
        return person.get("name") or person.get("phone") or f"员工{employee_id}"

    state = (
        "还在等对方同意"
        if row["status"] == STATUS_PENDING_PEER
        else "还在等店长批"
    )
    return f"{name(row['employee_id'])} 和 {name(row['peer_employee_id'])}（{state}）"


def _rule_anchor(rule: dict) -> date:
    """规则的周期起点。

    `anchor_date` 是 TEXT 列，人工 SQL 能写进任何东西 —— 跟 `_decode_cycle` 一样，
    脏数据在这里变成一条明确的输入错误，而不是一路冒到接口层变成 500。
    """
    try:
        return date.fromisoformat(rule["anchor_date"])
    except (TypeError, ValueError):
        raise SchedulingError("invalid_anchor", "invalid_anchor")


def _peer_id(row: dict) -> int:
    """申请里「对方」那一个员工号；请假没有这一项（回 -1，谁都不是）。

    `peer_employee_id` 是 0009 才有的列：读路径在没有它时会退回另一份列清单
    （`_select_request_rows`），此时 `row` 里根本没有这个键。
    """
    peer = row.get("peer_employee_id")
    return -1 if peer in (None, "") else int(peer)


def _request_touches(row: dict, employee_id: int, day: str) -> bool:
    """这条申请管不管「这个人这一天」（区间两端都算）。

    两个方向都算：我提的（``employee_id`` 是我）、别人问我换班的（``peer_employee_id``
    是我）—— 票 09 的对方点头之前那条申请还没作数，但它已经挂在那一天的两个人身上了。
    区间按 ISO 文本比就是按日期比（同 `_covers`）。状态由调用方先筛（只留还在等的
    那几种），这里只管「人」与「天」。
    """
    employee_id = int(employee_id)
    if employee_id not in (int(row["employee_id"]), _peer_id(row)):
        return False
    return row["start_date"] <= day <= row["end_date"]


def _cycle_shift(rule: dict, business_date: str, anchor: Optional[date] = None) -> Optional[int]:
    """规则在那一天排的是哪个班（`None` = 休）。

    **相位只有这一份算法**：展开（`_expand_rows`）和撤销覆盖后重算（`_restore_day`）
    都走它 —— 两边算出不一样的班次是这套东西最不该有的事。

    `anchor` 已经解析过就传进来（展开一天算一次，别在循环里反复 `fromisoformat`）；
    Python 的 `%` 对负数也给非负结果，所以起点在未来（先有规则、后把起点改回来）
    时照样算得对。
    """
    start = _rule_anchor(rule) if anchor is None else anchor
    cycle = rule["cycle"]
    day = date.fromisoformat(business_date)
    return cycle[(day - start).days % len(cycle)]


class SchedulingStore:
    """排班的读写口。构造要一个已打开的库连接（同 `EmployeeAccounts` 的口径）。"""

    def __init__(self, conn_or_db, now: Optional[Callable[[], datetime]] = None):
        conn = getattr(conn_or_db, "_conn", conn_or_db)
        if conn is None:
            raise RuntimeError("SchedulingStore requires an open database connection")
        self._conn = conn
        self._now = now or (lambda: datetime.now(CHINA_TZ))
        self._write_lock_owner = conn_or_db
        self._local_write_lock = None
        # 读申请时先按「有换班那一列」（0009）读；缺列就退一步、记住这一次降级，
        # 别每读一次都白撞一回（半迁移时请假要照常能用，见 `_select_request_rows`）。
        self._requests_have_peer = True

    @property
    def _write_lock(self):
        shared = getattr(self._write_lock_owner, "_write_lock", None)
        if shared is not None:
            return shared
        if self._local_write_lock is None:
            self._local_write_lock = asyncio.Lock()
        return self._local_write_lock

    def _now_dt(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TZ)
        return value

    def _now_iso(self) -> str:
        return self._now_dt().isoformat()

    def today(self) -> str:
        """当前工作台日（**自然日，00:00 切**，北京时）。

        2026-11-01 之前这里是 06:00 的营业日（ADR 0125）。店里没有跨零点的班次，06:00
        切在排班这边没有任何业务依据，只会在 0–6 点把「今天」说成昨天：11/1 00:19 打开
        整月页，页面写「今天 10/31」、手机写 11/1，同一个词指两天。
        """
        return current_workbench_date(self._now_dt())

    def _window(self) -> tuple[str, str]:
        """展开窗口：``[今天, 今天 + EXPANSION_DAYS - 1]``，两端都含。"""
        return self._window_from(self.today())

    @staticmethod
    def _window_from(today: str) -> tuple[str, str]:
        """给一个营业日算它那一版的展开窗口（`today` 是窗口第一天，两端都含）。

        写路径一律拿**请求开头取的那一个** today 来调它 —— 各步各读一次钟的话，
        请求正好跨过 06:00 的切日点时删和铺会算在不同的营业日上（见 `set_rule`）。
        """
        return today, shift_business_date(today, EXPANSION_DAYS - 1)

    @staticmethod
    def _backfill_floor(today: str) -> str:
        """可补记窗口的第一天：``today - BACKFILL_DAYS``（含这一天）。

        只有这一处算它 —— 单日覆盖的 `past_day` 与批量端点的逐格跳过读的是同一个数、
        同一种算法；`BACKFILL_DAYS` 一改，两处一起跟着走。
        """
        return shift_business_date(today, -BACKFILL_DAYS)

    # ── 申请的截止（2026-10-29 用户裁定）──────────────────────────────────
    @staticmethod
    def _apply_deadline(day: str) -> datetime:
        """提「``day`` 那一天」的假 / 换班的截止时刻：**前一晚 22:00**（北京时）。

        店长 22:00 之后就把第二天的人定下来了；过了这个点再提，批下来等于半夜改明天的班。
        """
        previous = date.fromisoformat(day) - timedelta(days=1)
        return datetime(
            previous.year, previous.month, previous.day,
            NEXT_DAY_CUTOFF_HOUR, tzinfo=CHINA_TZ,
        )

    def next_day_locked(self, today: str, now: Optional[datetime] = None) -> bool:
        """「次日」是不是已经过了截止（= 明天那一天的申请现在提不了）。

        员工端拿它把明天的「请假 / 换班」在控件上先挡掉（`/me/month` 与 `/me/day`
        响应里的 `next_day_locked`）—— 服务层这一头才是权威，前端只是别让人白填一次表。
        """
        moment = now or self._now_dt()
        return moment >= self._apply_deadline(shift_business_date(today, 1))

    def _check_apply_window(
        self, first: str, last: str, today: str, now: datetime, *, days: int,
    ) -> None:
        """申请日期的两条新规矩（2026-10-29 用户裁定），**都在服务层判**：

        1. 22:00 之后不能再提「次日」（`first > today` 且已过 `_apply_deadline(first)`）。
           当天的假不受限：生病没法提前请，那条路得留着。
        2. 2 天以上的假要提前 `MULTI_DAY_LEAVE_LEAD_DAYS` 天（从营业日算）。
        """
        if days >= 2:
            earliest = shift_business_date(today, MULTI_DAY_LEAVE_LEAD_DAYS)
            if first < earliest:
                # 文案里那句「提前 7 天」与 `MULTI_DAY_LEAVE_LEAD_DAYS` 是一件事，
                # 改数字时两处一起改（模板只能带一个 `{}`，装不下日期和天数两个值）。
                raise SchedulingError("leave_too_soon", earliest)
        if first > today and now >= self._apply_deadline(first):
            # 报「现在最早能提的那一天」：今天 22:00 之后就是后天（次日被锁掉）。
            raise SchedulingError("next_day_locked", shift_business_date(today, 2))

    # ── 班次 ────────────────────────────────────────────────────────────

    @serialized_write
    async def prepare(self) -> bool:
        """启动期一次性维护：班次表**空**的时候放进默认两条（白班 / 夜班）。

        只在表为空时写：店里把「白班」改成「早班」之后重启，不该被变回来；表非空
        （正常门店，或刚跑过 `0005` 迁移）时什么也不做。

        迁移里已经 seed 过同样的两行，这里是为两件事留的：测试库每个用例都会
        `TRUNCATE`（seed 留不住），以及门店手工清空过班次表。

        **表不存在不算启动失败**：门店的正常顺序是「先更新代码、再在 Admin 里应用
        增量迁移」，而应用起不来就进不了那个面板（`0004_logs` 也是同样的口径，见
        `services/log_storage.py` 的 `_warn_if_table_missing`）。所以这里只报一条带
        动作的错误日志并返回 False，把失败留给真正用到排班的那一刻。
        """
        try:
            cur = await self._conn.execute("SELECT COUNT(*) AS n FROM staff_shifts")
            row = await cur.fetchone()
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
            await self._rollback_quietly()
            logger.error(
                "❌ staff_shifts 表不存在：请在 Admin「系统更新 → 数据库迁移」应用 "
                "migrations/pg/0005_scheduling.sql（PG 不在启动期改结构，见 ADR 0089）"
            )
            return False
        # 班次表在、固定工作区表不在：半迁移（0005 应用了、0006 没应用）。启动照旧，
        # 但名单面板会 503，日志里先把该跑哪个脚本说清楚。
        try:
            await (await self._conn.execute("SELECT 1 FROM scheduling_zone_defaults LIMIT 1")).fetchone()
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
            await self._rollback_quietly()
            logger.error(
                "❌ scheduling_zone_defaults 表不存在：请在 Admin「系统更新 → 数据库迁移」"
                "应用 migrations/pg/0006_scheduling_zone_defaults.sql"
                "（缺它时排班的名单面板打不开，配不了固定工作区）"
            )
            return False
        if row is not None and int(dict(row)["n"] or 0) > 0:
            return True
        stamp = self._now_iso()
        for index, (name, sort_order, duty_slot) in enumerate(DEFAULT_SHIFTS):
            # 默认班次也带色（0043）：不带的话新装的门店两条默认班次都是中性灰，
            # 「按班次分色」这件事在第一次打开周表时就看不到。
            color = SHIFT_COLORS[index % len(SHIFT_COLORS)]
            try:
                await self._conn.execute(
                    """INSERT INTO staff_shifts
                           (name, sort_order, is_active, duty_slot, color_key, created_at, updated_at)
                       VALUES (?, ?, 1, ?, ?, ?, ?)""",
                    (name, sort_order, duty_slot, color, stamp, stamp),
                )
            except asyncpg.UndefinedColumnError:
                # 0043 还没应用的窗口里启动：默认班次照旧铺，颜色等迁移跑完由回填补上。
                await self._rollback_quietly()
                await self._conn.execute(
                    """INSERT INTO staff_shifts (name, sort_order, is_active, duty_slot, created_at, updated_at)
                       VALUES (?, ?, 1, ?, ?, ?)""",
                    (name, sort_order, duty_slot, stamp, stamp),
                )
        await self._conn.commit()
        logger.info("排班班次表为空，已放入默认班次 %s", "、".join(name for name, _, _ in DEFAULT_SHIFTS))
        return True

    async def _rollback_quietly(self) -> None:
        """丢弃未提交/aborted 的事务；失败不影响调用方（连接可能已经不可用）。"""
        try:
            await self._conn.rollback()
        except Exception:  # pragma: no cover - 连接已经坏了的话，没别的可做
            pass

    @_needs_migration
    async def list_shifts(self, include_inactive: bool = False) -> list[dict]:
        """班次表，按排序位。UI 按 N 个班次写，不写死两个。

        默认只出启用的 —— 配规则只能挑在用的班次。`include_inactive=True` 连停用的
        一起出：月历和「这天是谁」要用它，否则停用一个班次会让**已经写下的历史行**
        在界面上凭空消失（票 11 的验收项）。
        """
        where = "" if include_inactive else " WHERE is_active = 1"
        rows, color_ready = await self._query_shifts(where, " ORDER BY sort_order ASC, id ASC")
        return [_shift_payload(row, color_ready) for row in rows]

    # ── 班次表的编辑（票 11）─────────────────────────────────────────────
    #
    # 班次是**数据**不是常量（见文件头）：加一个班次、改个名字、调顺序、停用一条，
    # 都是店长在这个界面上做的事，不用改库。四件事共同的纪律：
    #
    # - 纯输入校验（名字空/太长/保留词、顺序不是整数）在锁外做，为了快也给得早；
    #   而**「读现状 - 判 - 写」这三步一律在 `@serialized_write` 里**（停用闸、
    #   删除闸、整表重排的全集校验）—— 中间被别的写请求插一下，判的就作废了
    #   （例如 `set_rule` 正好把要停用的班次排进某个人的轮转）。
    # - 报错给人话：能照做的那种（「还有 3 个人的轮转里排着它」），不是「参数不合法」。

    async def _query_shifts(self, where: str = "", order: str = "", params: tuple = ()) -> tuple[list[dict], bool]:
        """读班次行，顺带回答「`color_key` 这一列在不在」。

        读路径对缺列**降级**而不是整页 503：代码可能先于迁移生效（发行包里
        `applying_migrations` 阶段失败、或者有人手工换了代码树），这时周表还得能看能改，
        只是格子退回中性色、页面顶部说一句去应用 0043。写 `color_key` 的那条路不降级 ——
        没有列就直说是 `not_migrated`（`_needs_migration` 管的正是这件事）。
        """
        sql = f"SELECT {_SHIFT_COLUMNS_COLOR} FROM staff_shifts{where}{order}"
        try:
            cur = await self._conn.execute(sql, params)
            return [dict(row) for row in await cur.fetchall()], True
        except asyncpg.UndefinedColumnError:
            await self._rollback_quietly()
        cur = await self._conn.execute(
            f"SELECT {_SHIFT_COLUMNS} FROM staff_shifts{where}{order}", params
        )
        return [dict(row) for row in await cur.fetchall()], False

    async def _next_free_color(self) -> str:
        """新建班次的默认色：按 `SHIFT_COLORS` 顺序取第一个「启用班次里没人用」的键。

        都用完了就 `neutral`（可重复）：宁可不给色，也不让两个班次撞成一样 —— 那正是
        这次要修的毛病。第 9 个班次建出来就是中性灰，页面在班次表上说明这一点。
        """
        used = {
            shift["color_key"]
            for shift in await self.list_shifts()
            if shift.get("color_key")
        }
        for key in SHIFT_COLORS:
            if key not in used:
                return key
        return SHIFT_COLOR_NEUTRAL

    async def _require_color_free(self, color_key: str, exclude_id: Optional[int] = None) -> None:
        """撞色先在这里挡一道，为的是给一句人话；最后一道是库里的部分唯一索引。"""
        if color_key == SHIFT_COLOR_NEUTRAL:
            return
        for shift in await self.list_shifts():
            if shift["color_key"] == color_key and shift["id"] != exclude_id:
                raise SchedulingError("shift_color_taken", shift["name"])

    async def _shift_row(self, shift_id: int) -> dict:
        """按 id 取一行班次（**含停用的**）；没有就报 `unknown_shift_id`。

        跟 `unknown_shift`（工作区那条路：只能挑在用的班次）分开：编辑班次表必须
        能改到停用的那一条（给它改个名、重新启用），不能因为停用就「不存在」。
        """
        rows, color_ready = await self._query_shifts(" WHERE id = ?", "", (int(shift_id),))
        if not rows:
            raise SchedulingError("unknown_shift_id", "unknown_shift_id")
        return _shift_payload(rows[0], color_ready)

    async def _require_shift_name_free(self, name: str, exclude_id: Optional[int] = None) -> None:
        """重名先在这里挡一道，为的是给一句人话；最后一道是库里的 UNIQUE。"""
        for shift in await self.list_shifts(include_inactive=True):
            if shift["name"] == name and shift["id"] != exclude_id:
                raise SchedulingError("shift_name_taken", "shift_name_taken")

    @_needs_migration
    async def shift_usage(self) -> dict[int, dict]:
        """每个班次有多少人在用（票 11 的编辑页、停用与删除两道闸都读它）。

        - `people`：轮转规则里排着这个班次的人数 —— **未来新排班的来源**。停用前要知道
          这个数：还有人排着它就停用，等于「停用后新排班不再用它」这句话不成立。
        - `days`：`staff_assignments` 里这个班次的行数（历史 + 已铺的未来）——
          「已经被排班用过的班次删不掉」看的是这个数。

        每个班次都有一项（哪怕都是 0）：页面照着自己的班次列表取值，不用补默认值。
        """
        people: dict[int, set] = {}
        for employee_id, rule in (await self.list_rules()).items():
            for shift_id in rule["cycle"]:
                if shift_id is not REST:
                    people.setdefault(int(shift_id), set()).add(int(employee_id))
        days: dict[int, int] = {}
        for row in await (await self._conn.execute(
            """SELECT shift_id, COUNT(*) AS n FROM staff_assignments
               WHERE shift_id IS NOT NULL GROUP BY shift_id"""
        )).fetchall():
            mapping = dict(row)
            days[int(mapping["shift_id"])] = int(mapping["n"] or 0)
        return {
            shift["id"]: {
                "people": len(people.get(shift["id"], ())),
                "days": days.get(shift["id"], 0),
            }
            for shift in await self.list_shifts(include_inactive=True)
        }

    @_needs_migration
    async def create_shift(
        self,
        name: Any,
        sort_order: Any = None,
        duty_slot: Any = None,
        color_key: Any = None,
    ) -> dict:
        """加一个班次（票 11）。不给顺序就排在最后（当前最大 + `SHIFT_ORDER_STEP`）。

        新班次一律**启用**：店长刚建它就是要用它，建完再停用是没有意义的绕路。
        卫生档位（票 10）不给就是「不出日常检查」：加一个「中班」时还没有对应的
        日常检查项，等店长想清楚它是白班档还是夜班档再在这一页上标。

        颜色（0043）不给就**自动挑一个空闲主色**（`_next_free_color`）：店长建班次时
        最不关心的就是色号，但拿到一个跟别人相同的颜色等于没上色。给了就按给定的存，
        撞色报 `shift_color_taken`。
        """
        text = _clean_shift_name(name)
        order = _shift_order(sort_order)
        slot = _clean_duty_slot(duty_slot)
        wanted = _clean_color_key(color_key)
        await self._require_shift_name_free(text)
        color = await self._next_free_color() if wanted is None else wanted
        return await self._insert_shift(text, order, slot, color)

    @serialized_write
    async def _insert_shift(
        self,
        name: str,
        sort_order: Optional[int],
        duty_slot: Optional[str],
        color_key: str,
    ) -> dict:
        stamp = self._now_iso()
        if sort_order is None:
            cur = await self._conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS top FROM staff_shifts")
            row = await cur.fetchone()
            sort_order = int(dict(row)["top"] or 0) + SHIFT_ORDER_STEP
        # 自动挑色是锁外读的（`_next_free_color` 在 `create_shift` 里），同一瞬间两条
        # 新建会挑到同一个键 —— 这里补一道锁内查重，最后一道是库里的部分唯一索引。
        await self._require_color_free(color_key)
        try:
            cur = await self._conn.execute(
                """INSERT INTO staff_shifts
                       (name, sort_order, is_active, duty_slot, color_key, created_at, updated_at)
                   VALUES (?, ?, 1, ?, ?, ?, ?) RETURNING id""",
                (name, sort_order, duty_slot, color_key, stamp, stamp),
            )
            new_id = int(dict(await cur.fetchone())["id"])
        except asyncpg.UniqueViolationError as exc:
            # 上面那次查重在写锁外，挡不住同一瞬间进来的第二条：库里的 UNIQUE 兜底。
            raise _unique_shift_error(exc) from None
        await self._conn.commit()
        return {
            "id": new_id,
            "name": name,
            "sort_order": sort_order,
            "is_active": True,
            "duty_slot": duty_slot,
            "color_key": color_key,
        }

    @_needs_migration
    async def update_shift(
        self,
        shift_id: int,
        name: Any = None,
        sort_order: Any = None,
        is_active: Any = None,
        duty_slot: Any = None,
        color_key: Any = None,
    ) -> dict:
        """改一个班次：名字 / 显示顺序 / 启用停用 / 卫生档位（票 11、票 10）。

        `None` = 这一项不动。**卫生档位是唯一的例外**：它对「不动」和「清空成不出日常」
        得分开说 —— `None` 不动，**空串清空**，`'day'` / `'night'` 是设成那一档
        （见 `_clean_duty_slot`）。

        改名的安全性来自「别处引用的是 id」：`staff_assignments.shift_id` 没有外键、
        也没有谁按名字对齐（票 10 接线时同样别按名字对齐），所以改完名字，历史行
        跟着新名字显示，一行数据都不用动。

        **停用**是这里唯一有前提的一项（还有人排着它、或者它是最后一个在用的），
        两道判都在 `_update_shift_row` 的写锁里 —— 「读用量 - 判 - 写」中间被
        `set_rule` 插一下就白判了（那条规则会把停用掉的班次又铺回来）。
        """
        current = await self._shift_row(int(shift_id))
        text = current["name"] if name is None else _clean_shift_name(name)
        order = current["sort_order"] if sort_order is None else _shift_order(sort_order)
        wanted = current["is_active"] if is_active is None else bool(is_active)
        slot = current["duty_slot"] if duty_slot is None else _clean_duty_slot(duty_slot)
        # 颜色：`None` 不动、空串/`neutral` = 回中性；给了别的键就按给定的存。
        cleaned_color = _clean_color_key(color_key)
        color = current.get("color_key") if cleaned_color is None else cleaned_color
        if color is None:
            # 读路径降级过（`color_key` 为 None = 库里还没这一列）：写它必然缺列，
            # 交给下面的 UPDATE 报 `not_migrated`，这里先把值占住别让它变成 SQL NULL。
            color = SHIFT_COLOR_NEUTRAL
        if text != current["name"]:
            await self._require_shift_name_free(text, exclude_id=current["id"])
        return await self._update_shift_row(current["id"], text, order, wanted, slot, color)

    @serialized_write
    async def _update_shift_row(
        self,
        shift_id: int,
        name: str,
        sort_order: int,
        is_active: bool,
        duty_slot: Optional[str],
        color_key: str,
    ) -> dict:
        # 停用那两道闸在锁里读、锁里判（同 `_delete_shift_row`）：不然
        # `gather(update_shift, set_rule)` 能稳定造出「班次已停用、规则还排着它」
        # 的坏状态 —— 那正是这两道闸要拦的事。
        current = await self._shift_row(shift_id)
        if current["is_active"] and not is_active:
            usage = await self.shift_usage()
            in_use = int(usage.get(shift_id, {}).get("people", 0) or 0)
            if in_use:
                raise SchedulingError("shift_in_use", str(in_use))
            if not [shift for shift in await self.list_shifts() if shift["id"] != shift_id]:
                raise SchedulingError("last_active_shift", "last_active_shift")
        # 撞色也在锁里判：单独查一次，给的是「这档已经给 X 班用了」这类能照做的说法；
        # 库里那条部分唯一索引兜底（并发两条各改各的，或停用/启用恰好同时发生）。
        if color_key != SHIFT_COLOR_NEUTRAL:
            await self._require_color_free(color_key, exclude_id=shift_id)
        stamp = self._now_iso()
        try:
            await self._conn.execute(
                """UPDATE staff_shifts
                   SET name = ?, sort_order = ?, is_active = ?, duty_slot = ?,
                       color_key = ?, updated_at = ?
                   WHERE id = ?""",
                (name, sort_order, 1 if is_active else 0, duty_slot, color_key, stamp, shift_id),
            )
        except asyncpg.UniqueViolationError as exc:
            raise _unique_shift_error(exc) from None
        await self._conn.commit()
        return {
            "id": shift_id,
            "name": name,
            "sort_order": sort_order,
            "is_active": is_active,
            "duty_slot": duty_slot,
            "color_key": color_key,
        }

    @_needs_migration
    async def delete_shift(self, shift_id: int) -> dict:
        """删一个班次（票 11）。**用过的删不掉**：那些天在月历上会找不到班次。

        「用过」= `staff_assignments` 里有这个班次的行（历史或已铺的未来），或者还有
        人的轮转规则排着它。「删」只留给刚建错了、还没人用过的那一条；已经排过班的
        班次停用就够了（历史照旧显示），这一条写在错误文案里。
        """
        return await self._delete_shift_row(int(shift_id))

    @serialized_write
    async def _delete_shift_row(self, shift_id: int) -> dict:
        # 读-判-删都在锁里：删之前那个「还没人用」的判断不能被人插进来用掉。
        current = await self._shift_row(shift_id)
        numbers = (await self.shift_usage()).get(shift_id, {"people": 0, "days": 0})
        reasons = []
        if numbers["days"]:
            reasons.append(f"已经排过 {numbers['days']} 天班")
        if numbers["people"]:
            reasons.append(f"还有 {numbers['people']} 个人的轮转里排着它")
        if reasons:
            raise SchedulingError("shift_used", "，".join(reasons))
        rows = await self.list_shifts(include_inactive=True)
        # 删掉它之后一个**在用**的班次都不剩 = 谁都没班可排。这里跟 `_update_shift_row`
        # 的停用闸同一个口径（数还在用的），不能数「一共还剩几条」—— 否则
        # 「先把一条停用、再把唯一在用的删掉」就溜过去了。
        if not [shift for shift in rows if shift["id"] != shift_id and shift["is_active"]]:
            raise SchedulingError("last_active_shift", "last_active_shift")
        await self._conn.execute("DELETE FROM staff_shifts WHERE id = ?", (shift_id,))
        await self._conn.commit()
        return current

    @_needs_migration
    async def reorder_shifts(self, ids: Any) -> list[dict]:
        """按给定顺序重铺显示顺序（票 11 的「上移 / 下移」）。

        要求**给全**（所有班次，含停用的）：只给一部分的话「剩下的排哪儿」就成了一个
        说不清的问题，而页面本来就拿着全表。顺序对不上时报「刷新一下再调」，不猜 ——
        可能是刚有人加过或删过班次（校验在锁里，见 `_apply_shift_order`）。
        """
        if not isinstance(ids, (list, tuple)):
            raise SchedulingError("shift_order_mismatch", "shift_order_mismatch")
        order: list[int] = []
        for item in ids:
            try:
                order.append(int(item))
            except (TypeError, ValueError):
                raise SchedulingError("shift_order_mismatch", "shift_order_mismatch")
        if len(set(order)) != len(order):
            raise SchedulingError("shift_order_mismatch", "shift_order_mismatch")
        return await self._apply_shift_order(order)

    @serialized_write
    async def _apply_shift_order(self, order: list[int]) -> list[dict]:
        known = {shift["id"] for shift in await self.list_shifts(include_inactive=True)}
        if set(order) != known:
            raise SchedulingError("shift_order_mismatch", "shift_order_mismatch")
        stamp = self._now_iso()
        for index, shift_id in enumerate(order, start=1):
            await self._conn.execute(
                "UPDATE staff_shifts SET sort_order = ?, updated_at = ? WHERE id = ?",
                (index * SHIFT_ORDER_STEP, stamp, shift_id),
            )
        await self._conn.commit()
        return await self.list_shifts(include_inactive=True)

    async def _shifts_for_display(self, seen_ids: set) -> list[dict]:
        """要展示的班次列：启用的全要；停用的只在**这个范围里真有行**的时候带上。

        少了后半句，停用班次的历史行就从月历的人数和当天名单里消失了；少了前半句，
        一个从没人排过的停用班次会白占一列。
        """
        all_shifts = await self.list_shifts(include_inactive=True)
        active = [shift for shift in all_shifts if shift["is_active"]]
        retired = [
            shift for shift in all_shifts
            if not shift["is_active"] and shift["id"] in seen_ids
        ]
        return active + retired

    # ── 工作区 ──────────────────────────────────────────────────────────

    @_needs_migration
    async def zone_defaults(self) -> dict[int, dict[int, Optional[int]]]:
        """``employee_id → {shift_id: zone_id}``：每人每班次一个**固定区**（票 03）。

        没配过的人/班次不在里面 —— 那表示「这个人这个班次在哪」还没定，不是错误。
        """
        defaults: dict[int, dict[int, Optional[int]]] = {}
        for row in await (await self._conn.execute(
            "SELECT employee_id, shift_id, zone_id FROM scheduling_zone_defaults"
        )).fetchall():
            mapping = dict(row)
            defaults.setdefault(int(mapping["employee_id"]), {})[int(mapping["shift_id"])] = (
                None if mapping["zone_id"] is None else int(mapping["zone_id"])
            )
        return defaults

    async def _zone_defaults_for(self, employee_id: int) -> dict[int, Optional[int]]:
        """一个人的固定区，铺行的时候用。不自己上锁（调用方管）。"""
        rows = await (await self._conn.execute(
            "SELECT shift_id, zone_id FROM scheduling_zone_defaults WHERE employee_id = ?",
            (int(employee_id),),
        )).fetchall()
        return {
            int(dict(row)["shift_id"]): (
                None if dict(row)["zone_id"] is None else int(dict(row)["zone_id"])
            )
            for row in rows
        }

    @_needs_migration
    async def set_zone_default(
        self,
        employee_id: int,
        shift_id: int,
        zone_id: Optional[int] = None,
    ) -> dict:
        """给「某人 × 某班次」定一个固定区（`zone_id=None` = 清掉这个配置）。

        配完要**顺手把今天以后那些已经铺出来的行改掉** —— 不然店长改完区，月历上
        还是旧的（那些行是老早铺的，`set_rule` 才会重铺）。过去一行不动，同
        `_delete_future_rule_rows` 的口径。

        校验在锁外，写 + 改行在**一次**写锁、一次提交里。
        """
        employee_id = int(employee_id)
        shift_id = int(shift_id)
        roster = await EmployeeAccounts(self._write_lock_owner).list_roster()
        if employee_id not in {employee["id"] for employee in roster}:
            raise SchedulingError("unknown_employee", "unknown_employee")
        if shift_id not in {shift["id"] for shift in await self.list_shifts()}:
            raise SchedulingError("unknown_shift", "unknown_shift")
        zone = None if zone_id in (None, "") else int(zone_id)
        if zone is not None and zone not in {
            item["id"] for item in await self._zone_directory().list_zones()
        }:
            raise SchedulingError("unknown_zone", "unknown_zone")

        await self._apply_zone_default(employee_id, shift_id, zone)
        return {"employee_id": employee_id, "shift_id": shift_id, "zone_id": zone}

    @serialized_write
    async def _apply_zone_default(
        self, employee_id: int, shift_id: int, zone_id: Optional[int]
    ) -> None:
        stamp = self._now_iso()
        if zone_id is None:
            # 清掉 = 把这行删掉，不留一条「值为空」的配置：`zone_defaults()` 里
            # 「有这个人这个班次」和「配了区」于是是同一件事。
            await self._conn.execute(
                "DELETE FROM scheduling_zone_defaults WHERE employee_id = ? AND shift_id = ?",
                (employee_id, shift_id),
            )
        else:
            cur = await self._conn.execute(
                "SELECT id FROM scheduling_zone_defaults WHERE employee_id = ? AND shift_id = ?",
                (employee_id, shift_id),
            )
            existing = await cur.fetchone()
            if existing is None:
                await self._conn.execute(
                    """INSERT INTO scheduling_zone_defaults
                           (employee_id, shift_id, zone_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (employee_id, shift_id, zone_id, stamp, stamp),
                )
            else:
                await self._conn.execute(
                    """UPDATE scheduling_zone_defaults SET zone_id = ?, updated_at = ?
                       WHERE employee_id = ? AND shift_id = ?""",
                    (zone_id, stamp, employee_id, shift_id),
                )
        await self._conn.execute(
            """UPDATE staff_assignments SET zone_id = ?, updated_at = ?
               WHERE employee_id = ? AND shift_id = ? AND business_date >= ?
                 AND source = ?""",
            (zone_id, stamp, employee_id, shift_id, self.today(), SOURCE_RULE),
        )
        await self._conn.commit()

    def _zone_directory(self) -> ZoneDirectory:
        """公共层的工作区名单。写锁跟着走 —— 它只读，但共用同一个连接。"""
        return ZoneDirectory(self._write_lock_owner)

    async def _zone_name_index(self) -> dict[int, str]:
        return {zone["id"]: zone["name"] for zone in await self._zone_directory().list_zones()}

    # ── 规则 ────────────────────────────────────────────────────────────

    def _normalize_cycle(self, cycle: Any) -> list:
        if not isinstance(cycle, (list, tuple)):
            raise SchedulingError("invalid_cycle", "invalid_cycle")
        normalized: list = []
        for item in cycle:
            if item is None or item == "" or item == "rest":
                normalized.append(REST)
                continue
            try:
                normalized.append(int(item))
            except (TypeError, ValueError):
                raise SchedulingError("invalid_cycle", "invalid_cycle")
        if not normalized or len(normalized) > MAX_CYCLE_DAYS:
            raise SchedulingError("invalid_cycle", "invalid_cycle")
        return normalized

    @staticmethod
    def _decode_cycle(raw: Any) -> list:
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            raise SchedulingError("invalid_cycle", "invalid_cycle")
        if not isinstance(value, list) or not value or len(value) > MAX_CYCLE_DAYS:
            raise SchedulingError("invalid_cycle", "invalid_cycle")
        try:
            # `cycle` 是 TEXT 列，人工 SQL 能写进任何东西 —— 脏数据在这里变成一条
            # 明确的输入错误，而不是一路冒到接口层变成 500。
            return [REST if item is None else int(item) for item in value]
        except (TypeError, ValueError):
            raise SchedulingError("invalid_cycle", "invalid_cycle")

    async def _rule_rows(self) -> dict[int, dict]:
        """`scheduling_rules` 的**原样行**：``employee_id → {cycle, anchor_date}``，不解码。

        `list_rules()` 拿它当底、逐条解码并照旧对脏数据抛错；要「一个人坏掉不拖累别人」
        的调用方用这一版自己降级 —— `expand()` 逐人解码、坏的那个跳过，
        `roster_with_rules()` 把它标成 `invalid` 显示出来，`inbox()` 只用它判
        「这个人配没配过规则」（配过就有行，字符串坏不坏它不关心）。
        """
        cur = await self._conn.execute(
            "SELECT employee_id, cycle, anchor_date FROM scheduling_rules",
        )
        rows: dict[int, dict] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            rows[int(mapping["employee_id"])] = {
                "cycle": mapping["cycle"],
                "anchor_date": mapping["anchor_date"],
            }
        return rows

    async def list_rules(self) -> dict[int, dict]:
        """``employee_id → {cycle, anchor_date}``；没配规则的人不在里面。

        **`cycle` 读不出来时照旧抛 `SchedulingError`**（`invalid_cycle`）—— 这是内部读的
        口径：拿到的每一格都能直接算，调用方不用自己判。`anchor_date` 只是原样带出来
        （相位在 `_rule_anchor` 里才算，脏起点在那一步抛）。要降级的调用方**自己做**：
        `expand()` 逐人 try（解码与起点都在里面）之后跳过那一个，`roster_with_rules()`
        把那条标成 `invalid` 显示出来（名单面板是唯一能重配周期的入口，它跟着 400
        就等于店长自锁）。

        `shift_usage()` 也走这一版且**不降级**：它要数「多少人的轮转里排着这个班次」，
        读不出来的那条算不清 —— 少算一个人就可能放行一次停用，宁可报错。
        """
        rules: dict[int, dict] = {}
        for employee_id, raw in (await self._rule_rows()).items():
            rules[employee_id] = {
                "cycle": self._decode_cycle(raw["cycle"]),
                "anchor_date": raw["anchor_date"],
            }
        return rules

    def _rule_for_display(self, raw: dict, known_shift_ids: set[int]) -> dict:
        """名单面板里一条规则的样子：读不出来就**标出来**，不抛错。

        读得出来的照旧是 ``{"cycle": [...], "anchor_date": "YYYY-MM-DD"}``（跟票 04
        定下的形状一模一样，前端不会因为多一个键而变样）；只要有一处读不出来 ——
        `cycle` 不是合法周期、`anchor_date` 不是日期、或者周期里引用了一个不存在 /
        已停用的班次 —— 整条给成::

            {"cycle": None, "anchor_date": None, "invalid": True}

        `cycle` 与 `anchor_date` 一起给 None 而不是只废掉坏的那个：起点读不出来的规则
        相位算不出来，留着 `cycle` 只会让页面以为它还能用。少了 `invalid` 这一键的老形状
        就是「读得出来」的规则 —— 前端照 `ruleLabel` 渲染，`invalid` 为真时说
        「这条规则读不出来，重配一下」。
        """
        try:
            cycle = self._decode_cycle(raw["cycle"])
            _rule_anchor(raw)
            if any(
                shift_id is not REST and int(shift_id) not in known_shift_ids
                for shift_id in cycle
            ):
                raise SchedulingError("unknown_shift_in_cycle", "unknown_shift_in_cycle")
        except SchedulingError:
            return {"cycle": None, "anchor_date": None, "invalid": True}
        return {"cycle": cycle, "anchor_date": raw["anchor_date"]}

    def _cycle_lead_shift(self, raw: dict) -> Optional[int]:
        """这条路规则认出来的「起点班次」：``cycle`` 里第一个不是「休」的班次 id。

        周表行序的分片依据（票 14；ADR 0107 决策 10）。**整轮全是休**给 `None` —— 那等于
        没规则（这个人每天都休，没有哪个片是他的）。`cycle` 读不出来时也给 `None`，
        **不抛错**：`week_grid` 是只读的一条路，而 `scheduling_rules` 是人工 SQL 也会碰的
        表，一条脏规则不该把整张周表变成 400（`expand()` 对它是跳过这个人、
        `roster_with_rules()` 是标成 `invalid`，这条排序跟着一起降级）。

        解码复用 `_decode_cycle`（唯一那份 JSON 解析），不在这里另写一份。
        """
        try:
            cycle = self._decode_cycle(raw["cycle"])
        except SchedulingError:
            return None
        for shift_id in cycle:
            if shift_id is not REST:
                return int(shift_id)
        return None

    @_needs_migration
    async def set_rule(
        self,
        employee_id: int,
        cycle: Any,
        anchor_date: Optional[str] = None,
    ) -> dict:
        """给一个人配轮转规则，并把**今天以后**的规则行重铺一遍。

        校验先做一遍（都是读，为了早报「第几格错了」）；真正落地的三步 —— 写规则 →
        删今天以后的旧行 → 重铺 —— 在**一次**写锁、一次提交里做完，中途不会留下
        「规则改了、行还是旧的」。**锁里还会再验一次班次**（见 `_require_shifts_usable`）：
        读出「哪些班次还在用」与写下这条规则之间，正好可能有人把那个班次停用了
        （票 11 的停用闸只挡反方向：先有规则、再停用）。

        **今天只在这里读一次**，往下传给删除与重铺两步：删和铺各读一次钟的话，请求正好
        跨过 06:00 的切日点时会删 ≥9/23 的行、却从 9/24 起铺 —— 刚结束的那个营业日被
        删掉而且没人补，月历和员工页上那天变成「还没排到」。
        """
        employee_id = int(employee_id)
        normalized = self._normalize_cycle(cycle)
        roster = await EmployeeAccounts(self._write_lock_owner).list_roster()
        person = next((row for row in roster if row["id"] == employee_id), None)
        if person is None:
            raise SchedulingError("unknown_employee", "unknown_employee")
        # 离职的人不再配轮转规则（2026-10-29）：登记离职时规则已经被停掉了，这里挡住
        # 「又重新配一条」—— 否则下次铺排班又会长出离职日之后的行。
        if person.get("resigned_at") is not None:
            raise SchedulingError("resigned_employee", "")
        await self._require_shifts_usable(normalized)
        today = self.today()
        anchor = today if anchor_date in (None, "") else _require_business_date(anchor_date)

        await self._apply_rule(employee_id, normalized, anchor, today)
        return {
            "employee_id": employee_id,
            "cycle": normalized,
            "anchor_date": anchor,
        }

    async def _require_shifts_usable(self, cycle: list) -> None:
        """轮转里引用的班次都得是**还在用**的，不在就报是第几格。

        说清第几格是票 04 的验收项：规则编辑页照这个数字就能指出错在哪一天。
        `unknown_shift` 留给工作区那条路（`set_zone_default`：给某人某个班次配区时
        选了个不存在的班次）。`set_rule` 在锁外与锁里各调一次（理由见那里）；
        读路径也调：`_rule_for`（单人重取规则）用它挡住手工 SQL 写进去的幽灵班次 ——
        跳过那一个人，比铺 90 行指向不存在班次的行好（`expand()` 降级成前者）。
        """
        known = {shift["id"] for shift in await self.list_shifts()}
        for slot, shift_id in enumerate(cycle, start=1):
            if shift_id is not REST and shift_id not in known:
                raise SchedulingError("unknown_shift_in_cycle", str(slot))

    @serialized_write
    async def _apply_rule(
        self, employee_id: int, cycle: list, anchor_date: str, today: str
    ) -> None:
        """`today` 是调用方在请求开头取的那个营业日（见 `set_rule`）：删与铺共用它。"""
        # 锁里再验一次：这中间可能有人把某个班次停用了（票 11），那条规则一展开就会
        # 把停用的班次又铺回来 —— 正是停用那道闸要保证不会发生的事。
        await self._require_shifts_usable(cycle)
        await self._write_rule_row(employee_id, cycle, anchor_date)
        await self._delete_future_rule_rows(employee_id, today)
        await self._expand_rows(
            employee_id, {"cycle": cycle, "anchor_date": anchor_date}, today
        )
        await self._conn.commit()

    async def _write_rule_row(self, employee_id: int, cycle: list, anchor_date: str) -> None:
        payload = json.dumps(cycle, ensure_ascii=False)
        stamp = self._now_iso()
        cur = await self._conn.execute(
            "SELECT id FROM scheduling_rules WHERE employee_id = ?",
            (employee_id,),
        )
        existing = await cur.fetchone()
        if existing is None:
            await self._conn.execute(
                """INSERT INTO scheduling_rules (employee_id, cycle, anchor_date, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (employee_id, payload, anchor_date, stamp, stamp),
            )
        else:
            await self._conn.execute(
                """UPDATE scheduling_rules SET cycle = ?, anchor_date = ?, updated_at = ?
                   WHERE employee_id = ?""",
                (payload, anchor_date, stamp, employee_id),
            )

    async def _delete_future_rule_rows(self, employee_id: int, today: str) -> None:
        """规则改了 → 今天以后那些「按规则写出来的行」作废重铺；过去一行不动。

        `source = 'rule'` 这个条件是为后面的单日覆盖留的：手改/请假写下的行
        （`override`）不归规则管，改规则不该把它们抹掉。

        `today` 由调用方给（见 `set_rule`）：这里和后面的重铺必须是**同一个营业日**，
        否则请求跨过 06:00 时会删掉刚结束的那一天、却从新的一天起铺。
        """
        await self._conn.execute(
            """DELETE FROM staff_assignments
               WHERE employee_id = ? AND business_date >= ? AND source = ?""",
            (employee_id, today, SOURCE_RULE),
        )

    @_needs_migration
    async def clear_rule(self, employee_id: int) -> None:
        """把一个人的规则拿掉，连今天以后的规则行一起（一次写锁、一次提交）。"""
        await self._clear_rule(int(employee_id))

    @serialized_write
    async def _clear_rule(self, employee_id: int) -> None:
        await self._delete_rule_row(employee_id)
        await self._delete_future_rule_rows(employee_id, self.today())
        await self._conn.commit()

    async def _delete_rule_row(self, employee_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM scheduling_rules WHERE employee_id = ?",
            (employee_id,),
        )

    @serialized_write
    async def clear_after_resignation(
        self, employee_id: int, resigned_at: str, *, dry_run: bool = False
    ) -> dict:
        """登记离职时的排班清理（2026-10-29）：删离职日**之后**的行 + 停掉轮转规则。

        * 离职日**当天保留** —— 那天他还上班（判据与花名册的 `resigned_today` 同一条：
          「今天 < 离职日」算在职；「删日期之后」不是「删日期当天」）；
        * 轮转规则一并停掉：规则还在的话下一次铺排班又会给他长出新行，删了白删；
        * `dry_run=True` **只数不写** —— 抽屉里那句「会删掉 X 天排班」的确认框要这个数。

        **不带 `_needs_migration`**：它删的是 0005 就有的两张表，缺表时由调用方的
        路由报错；这里再包一层只会把「排班还没迁移」翻译成离职接口的一段怪话。

        返回 ``{"assignments_to_remove": n, "rule_cleared": bool}``。
        """
        cur = await self._conn.execute(
            """SELECT COUNT(*) AS n FROM staff_assignments
               WHERE employee_id = ? AND business_date > ?""",
            (employee_id, resigned_at),
        )
        row = await cur.fetchone()
        removed = int((row["n"] if row else 0) or 0)
        cur = await self._conn.execute(
            "SELECT COUNT(*) AS n FROM scheduling_rules WHERE employee_id = ?",
            (employee_id,),
        )
        row = await cur.fetchone()
        has_rule = bool(int((row["n"] if row else 0) or 0))
        if not dry_run:
            if removed:
                await self._conn.execute(
                    """DELETE FROM staff_assignments
                       WHERE employee_id = ? AND business_date > ?""",
                    (employee_id, resigned_at),
                )
            if has_rule:
                await self._delete_rule_row(employee_id)
            await self._conn.commit()
        return {"assignments_to_remove": removed, "rule_cleared": has_rule}

    # ── 单日覆盖 ────────────────────────────────────────────────────────

    @_needs_migration
    async def set_override(
        self,
        employee_id: int,
        business_date: str,
        is_rest: bool = False,
        shift_id: Optional[int] = None,
        zone_id: Optional[int] = None,
    ) -> dict:
        """把某人某一天改成「跟规则不一样」，**只改这一天**（票 07）。

        覆盖是**整天的快照**：这一天的班次和工作区由这次调用定下来 ——
        `scheduling_overrides` 记一条、`staff_assignments` 写一行（`source='override'`）。
        规则以后怎么变都不再动这一天（`_delete_future_rule_rows` 只删 `source='rule'`
        的行），想让它回到规则就撤掉覆盖（`clear_override`）。

        `is_rest=True` = 那天休，班次与工作区都要留空（同 `staff_assignments` 的口径：
        `shift_id IS NULL` 就是休）；否则 `shift_id` 必给。`zone_id` 不给时跟这个班次的
        **固定区**（`scheduling_zone_defaults`），跟展开时是同一条口径。

        **过去的日子在可补记窗口内照改**（票 14；ADR 0108）：漏排、补记发生在上周，
        这是店长唯一能补上的地方；只有早于 `_backfill_floor(today)` 的才拒（`past_day`）。
        未来那一头仍是展开窗口（`beyond_window`），不动。

        校验在锁外（都是读）；写覆盖记录与写结果行在**一次**写锁、一次提交里。

        **那天有已批的假就不写**（2026-10-18）：请假批下来就是一条 `kind=leave` 的覆盖
        记录，覆盖记录本身就是那条假；再写一次会把它的 `kind` 换成 `manual`，审批记录与
        排班事实就对不上了。判据与批量端点逐字相同 —— ``set_overrides_batch`` 的
        ``leave_approved`` 用的也是 ``_leave_ids([employee_id], day)``：命中就抛
        ``SchedulingError("leave_approved", ...)``，一个字节都不写。先撤掉那条假，再改班。

        **今天与窗口末日只在这里读一次**：两次各读一次钟，请求正好跨过 06:00 的切日点时
        「超出可补记窗口」和「还没排到」会按两个不同的营业日判（`beyond_window` 那句文案
        里的日期也会跟刚判过的那个不一样）。
        """
        employee_id, day, shift, zone = await self._resolve_override(
            employee_id, business_date, is_rest, shift_id, zone_id
        )
        leave_ids = await self._leave_ids([employee_id], day)
        if employee_id in leave_ids:
            raise SchedulingError("leave_approved", "leave_approved")
        await self._apply_override(employee_id, day, shift, zone)
        return {
            "employee_id": employee_id,
            "business_date": day,
            "shift_id": shift,
            "zone_id": zone,
            "is_rest": shift is REST,
        }

    async def _resolve_override(
        self,
        employee_id: int,
        business_date: str,
        is_rest: bool,
        shift_id: Optional[int],
        zone_id: Optional[int],
    ) -> tuple[int, str, Optional[int], Optional[int]]:
        """`set_override` 的**校验与定值**那一半：这一天该写成什么（班次 id / 工作区 id）。

        **一个字节都不写**（全是读）—— 晚一步的那道闸（`set_day_override` 要查的未决申请）
        走的就是「先把这一天算明白 → 再查闸 → 最后才落笔」这个次序，所以校验必须能单独
        走一遍。返回 `(员工号, 营业日, 班次 id, 工作区 id)`；班次 id 为空就是「那天休」。
        """
        employee_id = int(employee_id)
        day = _require_business_date(business_date)
        today = self.today()
        first_day = self._backfill_floor(today)
        _, last_day = self._window_from(today)
        if day < first_day:
            # 超出可补记窗口：太旧的日子不再改写。第二参填进那句提示的 `{}`：报**真实的
            # 窗口第一天**，别把「30 天」这个数写死在文案里（那个数一改，店长看到的就是
            # 假话 —— 同 `beyond_window` 的理由）。
            raise SchedulingError("past_day", first_day)
        if day > last_day:
            # 窗口尽头之后是「还没铺到」。在那里写一行，月历上就会出现一个
            # 「规则还没算到、却已经有人上班」的格子 —— 那是另一件事（先配规则）。
            # 第二参填进那句提示的 `{}`：报**真实的窗口末日**，别把 `EXPANSION_DAYS`
            # 写死在文案里（那个数一改，店长看到的就是假话）。
            raise SchedulingError("beyond_window", last_day)
        roster = await EmployeeAccounts(self._write_lock_owner).list_roster()
        person = next((row for row in roster if row["id"] == employee_id), None)
        if person is None:
            raise SchedulingError("unknown_employee", "unknown_employee")
        # 离职的人不能再排班（2026-10-29）：离职日**之后**的日子一律拒 —— 那些行在登记
        # 离职时已经删了，这里挡住的是「后来手滑又给他补一天」。空串（已离职、日期待补）
        # 当场算离职：任何日子都拒。批量端点逐格走这条，所以也一起被拦。
        resigned_at = person.get("resigned_at")
        if resigned_at is not None:
            left = str(resigned_at)[:10]
            if left == "" or day > left:
                raise SchedulingError("resigned_employee", left)

        shift: Optional[int] = None
        zone: Optional[int] = None
        if is_rest:
            if shift_id not in (None, "") or zone_id not in (None, ""):
                raise SchedulingError("rest_with_details", "rest_with_details")
        else:
            if shift_id in (None, ""):
                raise SchedulingError("missing_shift", "missing_shift")
            shift = int(shift_id)
            if shift not in {item["id"] for item in await self.list_shifts()}:
                raise SchedulingError("unknown_shift", "unknown_shift")
            zone = None if zone_id in (None, "") else int(zone_id)
            if zone is not None and zone not in {
                item["id"] for item in await self._zone_directory().list_zones()
            }:
                raise SchedulingError("unknown_zone", "unknown_zone")
            if zone is None:
                # 没点名要哪个区 → 跟这个班次的固定区（没配过就是没有区，不是错误）
                zone = (await self._zone_defaults_for(employee_id)).get(shift)

        return employee_id, day, shift, zone

    @_needs_migration
    async def set_day_override(
        self,
        employee_id: int,
        business_date: str,
        is_rest: bool = False,
        shift_id: Optional[int] = None,
        zone_id: Optional[int] = None,
    ) -> dict:
        """``set_override`` 再加一道闸：**那天还有未决的换班 / 请假申请就不写**（票 06）。

        考勤表页是店长逐日改格的第二个入口，而那张卡片上同时躺着别人刚提、还没批的
        申请。跟月历上的单日覆盖唯一的区别就在这里：月历上批量落笔**只提醒不阻断**
        （`set_overrides_batch` 的 `reminders`），这一页要的是**拒绝并说清**——店长在
        一张写着「9/25 请假待批」的卡片上点「改成休」，不该把人家那条请求悄悄冲掉。
        所以这里多回一个 `pending_request` 错误，由调用方翻成一句人话。

        除这道闸之外**没有第二套写排班的方式**：校验、写覆盖记录与结果行、只动那一天、
        可补记窗口，全部原样走 `set_override`（校验是它那一半 `_resolve_override`，
        落笔是 `_apply_override`）。`pending_request` 的 `args[0]` 是
        ``"leave"`` 或 ``"swap"``，API 层照它说「请假」还是「换班」。

        **判据与批准端同一条**：当事人是这个人（提的、或被问的）、那天落在那条申请的
        区间里（两端都算），而且**申请还没落定** —— `pending_manager`（等店长批）与
        `pending_peer`（换班还在等对方点头）都算：两种状态批下来写的都是那一天，
        而店长此刻手上正拿着那一天的格子。对方摇头、或申请人自己撤回之后
        （`rejected` / `cancelled`）就不再挡。

        **写前查**：先把这一天算明白（`_resolve_override`，全是读）、再读那道闸，
        两道都过了才落笔。被拒的那一次**一个字节都不碰** `staff_assignments` 与
        `scheduling_overrides` —— 原先那种「先写、再读、判出未决就按原样写回去」的补偿式
        写法终态虽然一样，却在「写完到读完」那一瞬把那一行重写过一遍：一次「没写成」的
        动作不该在库里留下任何写入。申请插入与「那天有没有未决的」读的是同一条连接，
        所以员工在店长点下去之前提上来的申请照样读得到。
        """
        employee_id, day, shift, zone = await self._resolve_override(
            employee_id, business_date, is_rest, shift_id, zone_id
        )
        pending = await self._pending_request_on(employee_id, day)
        if pending is not None:
            raise SchedulingError("pending_request", pending)
        await self._apply_override(employee_id, day, shift, zone)
        return {
            "employee_id": employee_id,
            "business_date": day,
            "shift_id": shift,
            "zone_id": zone,
            "is_rest": shift is REST,
        }

    async def _pending_request_on(self, employee_id: int, day: str) -> Optional[str]:
        """那天这个人有没有一条**还没落定**的申请：有就回 ``"leave"`` / ``"swap"``。

        两种「还在等」都算（见 `set_day_override`）：等店长批的、以及换班里等对方点头的。
        读不出来（0008 没应用）**不挡**：申请表不在时逐日改格照常能用，别让一次点格子
        因为读不到申请而整页 500（口径同 `_pending_request_days` 的尽力而为）。
        """
        try:
            rows = await self._request_rows()
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            logger.warning("待批申请读不了（0008 未应用？），这次逐日改格不挡: %s", exc)
            return None
        for row in rows:
            if row.get("status") not in (STATUS_PENDING_MANAGER, STATUS_PENDING_PEER):
                continue
            if not _request_touches(row, employee_id, day):
                continue
            return KIND_SWAP if row.get("kind") == KIND_SWAP else KIND_LEAVE
        return None

    @serialized_write
    async def _apply_override(
        self,
        employee_id: int,
        day: str,
        shift_id: Optional[int],
        zone_id: Optional[int],
        kind: str = KIND_MANUAL,
    ) -> None:
        stamp = self._now_iso()
        await self._write_override_row(
            employee_id, day, shift_id, zone_id, kind, stamp, voided=0
        )
        await self._write_day_row(employee_id, day, shift_id, zone_id, SOURCE_OVERRIDE, stamp)
        await self._conn.commit()

    async def _write_override_row(
        self,
        employee_id: int,
        day: str,
        shift_id: Optional[int],
        zone_id: Optional[int],
        kind: str,
        stamp: str,
        voided: int = 0,
    ) -> None:
        """把覆盖记录写成给定样子（有则改、无则插）。不自己上锁、不提交。

        再写一次就是**替换**这一天的样子（不是叠加）：`kind` 也跟着换成这次写的那个 ——
        请假批过的那天，店长又点着改了一次，那天现在就是店长定的（换回 `manual`）。
        `kind` 是参数而不是写死的 `manual`：批准请假写的就是同一条记录、另一个来源
        （票 08），两者进的是同一张表、同一行。

        `voided`（0040）是「这一天被清空过」的标志（BIGINT 0/1，见迁移头注释）：
        清空那天的覆盖行写 1 且班次 / 工作区都为空。**其余每个写路径都写 0** ——
        默认值就是 0，但 `set_override` / 批量的 `shift` / `rest` 都显式传，
        免得哪天有人给默认值换了个数，清空过的天再排班就被展开当成「还是空白」跳过。
        """
        cur = await self._conn.execute(
            """SELECT id FROM scheduling_overrides
               WHERE employee_id = ? AND business_date = ?""",
            (employee_id, day),
        )
        existing = await cur.fetchone()
        if existing is None:
            await self._conn.execute(
                """INSERT INTO scheduling_overrides
                       (employee_id, business_date, shift_id, zone_id, kind, voided,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (employee_id, day, shift_id, zone_id, kind, voided, stamp, stamp),
            )
        else:
            await self._conn.execute(
                """UPDATE scheduling_overrides
                   SET shift_id = ?, zone_id = ?, kind = ?, voided = ?, updated_at = ?
                   WHERE employee_id = ? AND business_date = ?""",
                (shift_id, zone_id, kind, voided, stamp, employee_id, day),
            )

    @_needs_migration
    async def clear_override(self, employee_id: int, business_date: str) -> dict:
        """撤掉某人某天的覆盖，那天回到规则铺出来的样子（票 07 的验收项）。

        今天与以后按**现在的规则**重算一遍；可补记窗口内（过去 30 天）的过去日子一样
        能撤，更早的仍是 `past_day`（2026-10-18 用户裁定放开 ADR 0108 决策 6）。

        **那天有已批准的假就整笔拒绝**（`leave_approved`）：覆盖记录 `kind=leave` 就是
        那条被批的假，删掉它等于把假抹掉，而申请表里那条仍然是 `approved`。判据与
        批量清除的 `skipped: leave_approved` 是同一处（`_leave_ids`）、同一个词 ——
        「清除班次」不许动已批的假，单格与批量不能两套。

        **那天什么都没有就是 no-op**：既没有覆盖记录、也没有结果行时 `_restore_day`
        不补写（见那里）—— 「清除」不该在从没铺过的过去日子里凭空造出一行上班。

        **今天只读一次**往下传：「这一天算不算过去」与「那天还在不在窗口里」两处各读
        一次钟的话，请求正好跨过 06:00 时会重算一个已经结束的营业日（或者漏算刚到的
        那一天）。
        """
        employee_id = int(employee_id)
        day = _require_business_date(business_date)
        today = self.today()
        # 2026-10-18 用户裁定：过去的日子也能「清除班次」（退回到规则铺出来的样子）。
        # 下界与「改班次」同一条：可补记窗口的第一天（`_backfill_floor`），更早的仍是
        # `past_day` —— 窗口是数据保留口径，不是这一次的交互取舍。
        if day < self._backfill_floor(today):
            raise SchedulingError("past_day", "past_day")
        await self._clear_override(employee_id, day, today)
        return {"employee_id": employee_id, "business_date": day}

    @serialized_write
    async def _clear_override(self, employee_id: int, day: str, today: str) -> None:
        """单格「撤销覆盖」：删覆盖记录 + 按现在的规则重算那天。

        **删之前先问一句「那天批过假没有」**（`_leave_ids`），命中就拒绝 —— 与批量清除
        的 `skipped: leave_approved` 同一处判据、同一个错误码。判据放在锁里而不是
        `clear_override` 那道锁外：判完到删之间正好有人批下那天的假，锁外会漏掉。

        **清空过的那天（`voided=1`）要按规则重新铺回来**：`void` 已经把结果行删了，
        「退回规则」若照 `_restore_day` 的「没有结果行就 no-op」走，店长点了「退回规则」
        看到的仍是空白（前端撤销「清空」走的也是这两条接口）。所以删之前先记下这条覆盖
        是不是 void 行，再让 `_restore_day` 强制按规则重算那一天 —— 判据是**这行证据**
        在，而不是「结果行还在」（那正是被 void 删掉的东西）。
        """
        if employee_id in await self._leave_ids([employee_id], day):
            raise SchedulingError("leave_approved", "leave_approved")
        voided = await self._override_voided(employee_id, day)
        await self._delete_override_row(employee_id, day)
        await self._restore_day(employee_id, day, today, force=voided)
        await self._conn.commit()

    async def _override_voided(self, employee_id: int, day: str) -> bool:
        """这条覆盖是不是「清空这一天」写下的（`voided = 1`，0040）。不自己上锁。

        没有覆盖行（或还没应用 0040）一律 False —— 调用方据此决定「退回规则」要不要
        在结果行已被 void 删掉的情况下照样按规则重铺。
        """
        cur = await self._conn.execute(
            """SELECT voided FROM scheduling_overrides
               WHERE employee_id = ? AND business_date = ?""",
            (employee_id, day),
        )
        row = await cur.fetchone()
        return row is not None and int(dict(row)["voided"] or 0) == 1

    async def _delete_override_row(self, employee_id: int, day: str) -> bool:
        """摘掉某人某天的覆盖记录（结果行不动）。不自己上锁、不提交。

        批量端点的「清除班次」也走它 —— 一条覆盖记录的删法只此一处。

        返回**真的删掉了一行没有**：清除链靠它区分「那天本来有覆盖」与「什么都没有」
        （后者不该让批量回执说「已清一格」，也不该广播 nudge）。
        """
        cur = await self._conn.execute(
            "DELETE FROM scheduling_overrides WHERE employee_id = ? AND business_date = ?",
            (employee_id, day),
        )
        return cur.rowcount > 0

    async def _restore_day(
        self, employee_id: int, day: str, today: str, force: bool = False
    ) -> bool:
        """把某一天按**现在的规则**重算回 `source='rule'`。不自己上锁、不提交。

        没有规则、或那天已经在窗口外：把这一行删掉，那天回到「还没铺到」——
        跟月历上窗口尽头的空格是同一个状态，不是「那天休」。

        **没有结果行就什么都不做并返回 False**（`force=False`，默认）：过去的日子只在它
        当年是「今天」时才被铺过，从没排过的那些天本来就没有行。清除 / 退回规则是「把
        覆盖撤掉、让规则重新说话」，不是「补一行上班」—— 在空日子上按现在的规则 INSERT
        会把考勤表从「待补」改成「上班」、把月历格子填上、把一个从没发生过的班次写进
        历史。要补记某天，走 `set_override` / 批量 `shift` 那条明示的路。

        `force=True` 只给「撤销清空过的那天」用（见 `_clear_override`）：`void` 删掉了
        结果行，而这行覆盖本身就是「这天曾被排过、被清空了」的证据；返回 `True` 表示
        这条证据已被处理（重铺了规则行、或那天本就在窗口外 / 没规则，删掉残留）。

        规则只读**他自己**那一条（`_rule_for`）而不是全店的：别人的规则写坏了，
        不该把这个人「撤掉覆盖」也一起挡住。`today` 由调用方给，见 `clear_override`。

        返回这一天有没有被真的处理过（写回规则行 / 删掉覆盖行）：批量回执的 `applied`
        只数这些格子。
        """
        if not force:
            cur = await self._conn.execute(
                """SELECT id FROM staff_assignments
                   WHERE employee_id = ? AND business_date = ?""",
                (employee_id, day),
            )
            if await cur.fetchone() is None:
                return False
        rule = await self._rule_for(employee_id)
        if rule is None or day > self._window_from(today)[1]:
            await self._conn.execute(
                """DELETE FROM staff_assignments
                   WHERE employee_id = ? AND business_date = ? AND source = ?""",
                (employee_id, day, SOURCE_OVERRIDE),
            )
            return True
        shift_id = _cycle_shift(rule, day)
        zone_id = None
        if shift_id is not REST:
            zone_id = (await self._zone_defaults_for(employee_id)).get(shift_id)
        await self._write_day_row(
            employee_id,
            day,
            shift_id,
            zone_id,
            SOURCE_RULE,
            self._now_iso(),
            reset_half_rest=True,
        )
        return True

    async def _write_day_row(
        self,
        employee_id: int,
        day: str,
        shift_id: Optional[int],
        zone_id: Optional[int],
        source: str,
        stamp: str,
        reset_half_rest: bool = False,
    ) -> None:
        """把某人某天的结果行写成给定样子（有则改、无则插）。不自己上锁、不提交。

        **写成「休」（`shift_id` 为空）时把 `half_rest` 一起清成 0**（票 07）：`休 + 休半天`
        是自相矛盾的一行，而且标志留着的话，哪天这一格又被改回某个班次，它会**自己**变成
        「休半天」—— 没人点过它。反过来，写成某个班次时**一个字都不动它**：店长改的是
        「上哪个班」，不是「上不上这半天」（半天休只在考勤表页与周表「休半天」笔那两扇
        门设/清），而且 `set_day_override` 判出未决申请要把这一天**原样写回去**时，也不该
        顺手抹掉一个跟这次请求无关的决定。

        `reset_half_rest=True` 是唯一一个「写成班次也清标志」的例外：`_restore_day` 把
        覆盖撤掉、按规则重铺那天时用 —— 回到规则 = 规则里没有半天休，残留的半天休标记
        会让那天同时是「规则班次」和「休半天」，而这是上一次手改留下的。

        清标志那条 UPDATE 才引用 `half_rest`（加列 0028）：没应用迁移的库上，写「休」会
        报缺列并由 `_needs_migration` 翻成 503，**其余写路径（改班次、改区、铺规则）
        照旧一个字都不碰它**。
        """
        cur = await self._conn.execute(
            """SELECT id FROM staff_assignments
               WHERE employee_id = ? AND business_date = ?""",
            (employee_id, day),
        )
        existing = await cur.fetchone()
        if existing is None:
            await self._conn.execute(
                """INSERT INTO staff_assignments
                       (employee_id, business_date, shift_id, zone_id, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (employee_id, day, shift_id, zone_id, source, stamp, stamp),
            )
        elif shift_id is None or reset_half_rest:
            await self._conn.execute(
                """UPDATE staff_assignments
                   SET shift_id = ?, zone_id = ?, source = ?, updated_at = ?, half_rest = 0
                   WHERE employee_id = ? AND business_date = ?""",
                (shift_id, zone_id, source, stamp, employee_id, day),
            )
        else:
            await self._conn.execute(
                """UPDATE staff_assignments
                   SET shift_id = ?, zone_id = ?, source = ?, updated_at = ?
                   WHERE employee_id = ? AND business_date = ?""",
                (shift_id, zone_id, source, stamp, employee_id, day),
            )

    # ── 批量落笔（票 14） ───────────────────────────────────────────────

    @_needs_migration
    async def set_overrides_batch(
        self,
        action: Any,
        shift_id: Any = None,
        zone_id: Any = None,
        items: Any = None,
        half_rest: Any = None,
    ) -> dict:
        """一次刷一片：能写的写、不能写的逐格点名（票 14；ADR 0107 决策 5/6）。

        `action` 整批同一个（词表见 `BATCH_ACTIONS`）：
        `shift` 改成某个班（`shift_id` 必给）、`rest` 改成休、`restore` 退回轮转规则
        （`clear` 是它的兼容别名）、`void` 清空这一天、`half_rest` 设/清那天的
        「休半天」标志（`half_rest` 必给）。`zone_id` 只对 `shift` 有意义 —— 不给（或给
        `null`）表示每格跟**这个人这个班次**的固定区，跟 `set_override` 同一条口径；
        其它动作带着 `shift_id` / `zone_id` / `half_rest` 也不报错（客户端把当前选中的
        班次一起发过来是常态，那些值在别的动作里没有意义）。

        **部分成功是预期语义**：`skipped` 逐格说清谁、哪天、为什么（词表定死在
        `past_day` / `past_clear` / `beyond_window` / `leave_approved` / `out_of_roster` /
        `half_rest_needs_shift`），不接受「一格里有人请假、整片白刷」。整批拒绝的只有
        三类：格数超上限（`MAX_BATCH_CELLS`）、动作不认识、输入本身写错（日期 / 人员
        编号 / 班次 / 工作区 / `half_rest` 的布尔值）—— 那些不是「某一格刷不了」，
        是这次请求不成立。

        `reminders` 是「这一格的人那天有申请等着批」：**照刷，只提醒**（批下来会把那天
        换成请假 / 对调），选择权留给店长。

        校验与筛选全在**锁外**（都是读），锁里只写、循环外一次提交 —— 与批请假
        （`_approve`）同构。今天、可补记窗口的第一天、展开窗口的末日只在这里读一次：
        跨 06:00 的一次批量不再出现「前半批按今天算、后半批按明天算」。

        `employee_ids` 是**真写下去的那些人**，只给路由层广播用（响应里不带出去）。

        `applied` 同样是**真写下去/真清掉的那些格**：`clear` / `restore` 在「既没有
        覆盖记录、也没有结果行」的日子上无事可做（`_restore_day` 的 no-op），那些格
        既不算 applied 也不进 `skipped` —— 它不是失败，只是没有理由可写进那张定死的
        词表（ADR 0107 决策 6）。
        """
        if action not in BATCH_ACTIONS:
            raise SchedulingError("invalid_batch_action", "invalid_batch_action")
        if not isinstance(items, (list, tuple)):
            raise SchedulingError("invalid_batch_items", "invalid_batch_items")
        if len(items) > MAX_BATCH_CELLS:
            raise SchedulingError("batch_too_large", str(MAX_BATCH_CELLS))

        today = self.today()
        first_day = self._backfill_floor(today)
        last_day = self._window_from(today)[1]
        cells = [self._batch_cell(item) for item in items]

        # 「不在册」= 花名册里没有这个人，或他停用 / 还没批准 —— 判据与周表、月历、
        # 当日名单共用一份（`_on_roster_ids`），别在这里再拼一遍。
        known = await self._on_roster_ids()

        shift: Optional[int] = None
        zone: Optional[int] = None
        defaults: dict[int, dict[int, Optional[int]]] = {}
        flag: Optional[bool] = None
        if action == BATCH_SHIFT:
            shift = await self._batch_shift(shift_id)
            zone, defaults = await self._batch_zone(zone_id)
        elif action == BATCH_HALF_REST:
            flag = self._batch_half_rest(half_rest)

        # 一格一格判「能不能刷」，判完还留在场上的才去查请假 —— 那天有已批的假的一格
        # 都不许动（连「清除」也不行：覆盖记录就是那条假，删了等于把批过的假抹掉）。
        reasons: list[Optional[str]] = [None] * len(cells)
        for index, (employee_id, day) in enumerate(cells):
            if employee_id not in known:
                reasons[index] = "out_of_roster"
            elif day > last_day:
                reasons[index] = "beyond_window"
            elif day < first_day:
                # 2026-10-18 用户裁定：`clear` 在过去的日子上**也生效**（放开 ADR 0108
                # 决策 6 的「过去只能改成某个班」）；这里只剩可补记窗口这一条下界。
                reasons[index] = "past_day"

        leave_days: dict[str, set[int]] = {}
        for index, (_, day) in enumerate(cells):
            if reasons[index] is not None or day in leave_days:
                continue
            leave_days[day] = await self._leave_ids(
                [
                    employee_id
                    for other, (employee_id, item_day) in enumerate(cells)
                    if item_day == day and reasons[other] is None
                ],
                day,
            )
        for index, (employee_id, day) in enumerate(cells):
            if reasons[index] is None and employee_id in leave_days[day]:
                reasons[index] = "leave_approved"

        if action == BATCH_HALF_REST:
            # 「休半天」只认**那一天真有班次**的结果行（休 / 空白都标不上）：与考勤表页
            # 同一道闸（`AttendanceStore.set_half_rest` 的 `half_rest_needs_shift`），
            # 只是这里逐格跳过而不是整笔拒绝。
            reasons = await self._half_rest_reasons(cells, reasons)

        written = [cell for cell, reason in zip(cells, reasons) if reason is None]
        if action in (BATCH_CLEAR, BATCH_RESTORE):
            # 「退回规则」可能对某几格无事可做（既没覆盖、也没结果行，见 `_restore_day`）：
            # 只有真被处理过的那些格才算 `applied`、才广播。
            written = await self._clear_batch_rows(written, today)
        elif action == BATCH_VOID:
            written = await self._void_batch_rows(written, self._now_iso())
        elif action == BATCH_HALF_REST:
            await self._half_rest_batch_rows(written, bool(flag), self._now_iso())
        else:
            rows = []
            for employee_id, day in written:
                # 没点名要哪个区 → 跟这个人在这个班次上的固定区（没配过就是没有区）。
                cell_zone = zone if zone is not None else defaults.get(employee_id, {}).get(shift)
                rows.append((employee_id, day, shift, cell_zone))
            await self._write_batch_rows(rows, self._now_iso())

        pending = await self._pending_request_days({item[0] for item in written})
        return {
            "applied": len(written),
            "skipped": [
                {"employee_id": employee_id, "business_date": day, "reason": reason}
                for (employee_id, day), reason in zip(cells, reasons)
                if reason is not None
            ],
            "reminders": [
                {
                    "employee_id": employee_id,
                    "business_date": day,
                    "kind": REMINDER_PENDING_REQUEST,
                }
                for employee_id, day in written
                if self._covers(pending.get(employee_id), day)
            ],
            "employee_ids": sorted({employee_id for employee_id, _ in written}),
        }

    @staticmethod
    def _batch_half_rest(value: Any) -> bool:
        """批量 `action=half_rest` 的那个标志：必给，且得真是布尔。

        `None` / 空串 = 客户端没说清要**标上**还是**取消** —— 这个动作不能猜（猜成
        「标上」会把取消点成标上），整批拒绝。字符串 `"true"` / `"false"` 也不收：
        请求体是 JSON，布尔就该是布尔，别的形状说明调用方写错了。
        """
        if value is None:
            raise SchedulingError("missing_half_rest", "missing_half_rest")
        if not isinstance(value, bool):
            raise SchedulingError("invalid_half_rest", "invalid_half_rest")
        return value

    async def _half_rest_reasons(
        self, cells: list[tuple[int, str]], reasons: list[Optional[str]]
    ) -> list[Optional[str]]:
        """把「那天没有班次」的格子记为 `half_rest_needs_shift`（其余照旧）。

        判据与考勤表页那条同口径：那天**必须有结果行、且 `shift_id` 非空**。清空过的
        天（void）没有结果行，休的那天班次为空 —— 两种都标不上半天休，逐格跳过。
        按天分组查一次（同 `leave_days` 的写法）：200 格上别逐格打一条 SQL。
        """
        ready: dict[str, set[int]] = {}
        for index, (_, day) in enumerate(cells):
            if reasons[index] is not None or day in ready:
                continue
            cur = await self._conn.execute(
                """SELECT employee_id FROM staff_assignments
                   WHERE business_date = ? AND shift_id IS NOT NULL""",
                (day,),
            )
            ready[day] = {int(dict(row)["employee_id"]) for row in await cur.fetchall()}
        gated = list(reasons)
        for index, (employee_id, day) in enumerate(cells):
            if gated[index] is not None:
                continue
            if employee_id not in ready.get(day, set()):
                gated[index] = "half_rest_needs_shift"
        return gated

    @staticmethod
    def _batch_cell(item: Any) -> tuple[int, str]:
        """一格批量输入 → ``(employee_id, business_date)``。

        人、日期都指不出「第几格」（整批一次发过来，序号对店长没有意义），所以报的是
        这一项本身不合法：`invalid_employee_id` / `invalid_business_date`，各一句中文。
        """
        mapping = item if isinstance(item, dict) else {}
        try:
            employee_id = int(mapping.get("employee_id"))
        except (TypeError, ValueError):
            raise SchedulingError("invalid_employee_id", "invalid_employee_id")
        return employee_id, _require_business_date(mapping.get("business_date"))

    async def _batch_shift(self, shift_id: Any) -> int:
        """批量 `action=shift` 的那个班次：必给、必须在用（跟 `set_override` 同一道闸）。"""
        if shift_id in (None, ""):
            raise SchedulingError("missing_shift", "missing_shift")
        try:
            shift = int(shift_id)
        except (TypeError, ValueError):
            raise SchedulingError("unknown_shift", "unknown_shift")
        if shift not in {item["id"] for item in await self.list_shifts()}:
            raise SchedulingError("unknown_shift", "unknown_shift")
        return shift

    async def _batch_zone(self, zone_id: Any) -> tuple[Optional[int], dict[int, dict]]:
        """批量 `action=shift` 的工作区：``(点名的那个区, 整张固定区表)``。

        `zone_id` 没给就是「每格跟这个人这个班次的固定区」—— 固定区是按人配的（票 03），
        所以一次读全表，别在 200 格上逐格查。
        """
        if zone_id in (None, ""):
            return None, await self.zone_defaults()
        try:
            zone = int(zone_id)
        except (TypeError, ValueError):
            raise SchedulingError("unknown_zone", "unknown_zone")
        if zone not in {item["id"] for item in await self._zone_directory().list_zones()}:
            raise SchedulingError("unknown_zone", "unknown_zone")
        return zone, {}

    @serialized_write
    async def _write_batch_rows(
        self, rows: list[tuple[int, str, Optional[int], Optional[int]]], stamp: str
    ) -> None:
        """把这一批覆盖记录与结果行一次写完，**循环外一次提交**。

        复用那两个不上锁、不提交的写入件（`_write_override_row` / `_write_day_row`）——
        批量不另造一套写库逻辑（与批请假 `_approve` 同构）。中途哪一格抛错都会被
        `serialized_write` 整批回滚：不会留下「写了一半」的排班。

        `voided=0` 是**显式**的：清空过的天再排班 / 再改休，那条覆盖行必须回到正常覆盖，
        否则展开会继续把它当 voided 跳过（见 0040 迁移的头注释）。
        """
        for employee_id, day, shift_id, zone_id in rows:
            await self._write_override_row(
                employee_id, day, shift_id, zone_id, KIND_MANUAL, stamp, voided=0
            )
            await self._write_day_row(
                employee_id, day, shift_id, zone_id, SOURCE_OVERRIDE, stamp
            )
        await self._conn.commit()

    @serialized_write
    async def _void_batch_rows(
        self, cells: list[tuple[int, str]], stamp: str
    ) -> list[tuple[int, str]]:
        """这一批「清空这一天」：写 `voided=1` 的覆盖行 + 删掉那天的结果行，最后一次提交。

        **为什么写一条覆盖行**：空白得能**持久**。只删结果行的话，下一次 `expand()`
        就会按规则把那天重新铺回来（「清」被改回原班次的旧缺陷）。`voided=1` 的覆盖行
        是「这一天不按规则排」的证据，`_expand_rows` 见到它就跳过（0040 迁移）。

        **为什么删结果行**：读取端（周表 / 月历 / 考勤表 / 员工端）把「没有行」天然读成
        空白 / 待补 / 还没排 —— 读取端一个字都不用改，这是「空白」本身该有的表达。
        删的是这个人那一天的那一行，不管它是规则铺的还是覆盖写的：店长点的是「清空这一天」。

        能走到这里的格子都已经过三道闸（在册 / 窗口 / 没有已批的假），更早的在筛选那一步
        就进 `skipped` 了。每个格子都真写下了覆盖行，所以整批都算 `applied`。
        """
        for employee_id, day in cells:
            await self._write_override_row(
                employee_id, day, None, None, KIND_MANUAL, stamp, voided=1
            )
            await self._conn.execute(
                """DELETE FROM staff_assignments
                   WHERE employee_id = ? AND business_date = ?""",
                (employee_id, day),
            )
        await self._conn.commit()
        return list(cells)

    @serialized_write
    async def _half_rest_batch_rows(
        self, cells: list[tuple[int, str]], half_rest: bool, stamp: str
    ) -> None:
        """这一批「设 / 清休半天」：只 UPDATE 那个标志与 `updated_at`，最后一次提交。

        班次 / 工作区 / 来源（`source`）与那一天是不是单日覆盖**一个字都不动** —— 与考勤表
        页 `AttendanceStore.set_half_rest` 同一口径：半天休不是第二个班次，也不是一次
        「跟规则不一样」的改写。能走到这里的格子都已确认「那天有结果行且班次非空」
        （见 `_half_rest_reasons`），所以 UPDATE 一定命中。
        """
        for employee_id, day in cells:
            await self._conn.execute(
                """UPDATE staff_assignments SET half_rest = ?, updated_at = ?
                   WHERE employee_id = ? AND business_date = ?""",
                (1 if half_rest else 0, stamp, employee_id, day),
            )
        await self._conn.commit()

    @serialized_write
    async def _clear_batch_rows(
        self, cells: list[tuple[int, str]], today: str
    ) -> list[tuple[int, str]]:
        """这一批「退回规则」：每格摘掉覆盖记录、按现在的规则重算，最后一次提交。

        与单格撤销（`_clear_override`）共用同一个删法 —— 批量退回与逐格撤销必须落到
        同一件事上。能走到这里的格子都在可补记窗口内（含过去 30 天；更早的在筛选那一步
        就记 `past_day`）；那天有已批的假的一组早在筛选那一步就记 `leave_approved`。

        **清空过的那天（voided）要重铺规则行**：`void` 删了结果行，照 `_restore_day` 的
        「没有结果行就 no-op」走，店长点「退回规则」看到的仍是空白。删之前先记下这条
        覆盖是不是 void 行，再让 `_restore_day` 强制重算（同 `_clear_override`）。

        返回**真被处理过的那些格**（`_delete_override_row` / `_restore_day` 两个返回值
        里有一个是真的）：既没有覆盖记录、也没有结果行的格子在退回时无事可做
        （`_restore_day` 的 no-op），不能算进回执的 `applied` —— 店长点「退回规则」之后
        被告知「已退 N 格」，而其中有些格子本来就什么都没有。
        """
        handled: list[tuple[int, str]] = []
        for employee_id, day in cells:
            voided = await self._override_voided(employee_id, day)
            removed = await self._delete_override_row(employee_id, day)
            restored = await self._restore_day(employee_id, day, today, force=voided)
            if removed or restored:
                handled.append((employee_id, day))
        await self._conn.commit()
        return handled

    async def _pending_request_days(
        self, employee_ids: set[int]
    ) -> dict[int, list[tuple[str, str]]]:
        """还在等店长批的申请：``employee_id → [(起, 止), …]``（请假与换班都在里面）。

        批量落笔只拿它做**提醒**（"王五 10/15 有一条待批的请假，批了会盖掉你刚刷的"），
        **不阻断写入**，所以这一整段是尽力而为：申请表不在（0008 没应用）时返回空表并
        记一条日志，别让一次刷一片因为读不到提醒而整批失败（同 `_leave_ids` 的口径）。
        """
        if not employee_ids:
            return {}
        try:
            rows = await self._request_rows(status=STATUS_PENDING_MANAGER)
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            logger.warning("待批申请读不了（0008 未应用？），这次批量不做提醒: %s", exc)
            return {}
        pending: dict[int, list[tuple[str, str]]] = {}
        for row in rows:
            employee_id = int(row["employee_id"])
            if employee_id in employee_ids:
                pending.setdefault(employee_id, []).append(
                    (row["start_date"], row["end_date"])
                )
        return pending

    @staticmethod
    def _covers(ranges: Optional[list[tuple[str, str]]], day: str) -> bool:
        """这一天在不在那几条申请区间里（两端都算）——ISO 文本按字典序比就是按日期比。"""
        return any(start <= day <= end for start, end in ranges or ())
    # ── 展开 ────────────────────────────────────────────────────────────

    @_needs_migration
    async def expand(self, employee_id: Optional[int] = None) -> int:
        """把规则铺成结果行，返回这次新写了多少行。

        一个人一次上锁、一次提交：第一次铺 90 天就是 90 行，不为此握着全局写锁
        （月历哪天有人第一次打开都会走到这里）。

        **这里读到的规则只当成一份「该看哪些人」的名单**用：真正要铺的那条规则在锁里
        重取一次，见 `_expand_one` —— 读到这份名单与拿到锁之间，别人可能已经把规则清了。

        **一个人一条脏规则不该拖累全店**：名单走 `_rule_rows()`（原样行、不解码），
        解码留给下面逐人做。`scheduling_rules` 是人工 SQL 也会碰的表，谁把某一行的
        `cycle` 或 `anchor_date` 写坏了，以前会让整条 `expand()` 抛错 —— `/calendar`、
        `/day`、`/inbox` 一起 400，而名单面板（唯一能重配周期的入口）读的是同一批数据，
        等于店长自锁。现在坏的那一个记一条 warning 日志后**跳过**，别人照常铺；
        返回值仍旧是这次一共写了多少行。
        """
        rules = await self._rule_rows()
        if employee_id is not None:
            key = int(employee_id)
            rules = {key: rules[key]} if key in rules else {}
        written = 0
        for emp_id in rules:
            try:
                written += await self._expand_one(emp_id)
            except SchedulingError as exc:
                # `serialized_write` 已经把这个人的半截事务回滚掉了（无写时是 no-op），
                # 所以下一个人照常能写。日志里点名是谁，运维照 employee_id 就能定位那行。
                logger.warning(
                    "排班规则读不出来，跳过这个人（别人照常铺）: employee_id=%s code=%s",
                    emp_id, exc.code,
                )
        return written

    async def _rule_for(self, employee_id: int) -> Optional[dict]:
        """一个人的规则（`list_rules()` 的单人版：展开时只要他自己那一条）。

        引用不存在 / 已停用的班次也在这里报 `unknown_shift_in_cycle`：铺 90 行指向幽灵
        班次的行比跳过这个人更糟（月历会多出一列查不到名字的班次，清理还得进库）。
        调用方 `expand()` 把它降级成「跳过这个人」。
        """
        cur = await self._conn.execute(
            "SELECT cycle, anchor_date FROM scheduling_rules WHERE employee_id = ?",
            (int(employee_id),),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        mapping = dict(row)
        cycle = self._decode_cycle(mapping["cycle"])
        await self._require_shifts_usable(cycle)
        return {
            "cycle": cycle,
            "anchor_date": mapping["anchor_date"],
        }

    @serialized_write
    async def _expand_one(self, employee_id: int) -> int:
        """锁里重取规则再铺 —— 锁外那份快照不能拿来写行。

        从 `expand()` 读名单到这儿拿到写锁之间，可能有人把规则清了（`_clear_rule` 删
        规则行、连今天以后的规则行一起删）。照旧快照展开就会把已经删掉的规则又铺回
        90 天，而且**不会自愈**（`expand()` 只补缺的天，铺回来的行再没别的路径去删）：
        名单面板写着「还没配规则」，月历与卫生读到的却是他有班。取不到规则就什么都不写。
        """
        rule = await self._rule_for(employee_id)
        if rule is None:
            return 0
        written = await self._expand_rows(employee_id, rule, self.today())
        if written:
            await self._conn.commit()
        return written

    async def _expand_rows(self, employee_id: int, rule: dict, today: str) -> int:
        """铺窗口内还缺的那些天，返回写了几行。不自己上锁、不提交（调用方管）。

        只管「还缺的天」：已经存在的行（含单日覆盖写下的）一律不碰。

        `today` 由调用方给（`set_rule` 的写路径里要和删除那一步用同一个营业日，
        `expand()` 里就是这个人那一刻的今天）：窗口只按它算一次。
        """
        first, last = self._window_from(today)
        # 先把起点解析出来，哪怕这一天都不缺：脏数据该在第一次展开时就报出来，
        # 而不是等到某个「正好缺一天」的时刻。
        anchor = _rule_anchor(rule)

        cur = await self._conn.execute(
            """SELECT business_date FROM staff_assignments
               WHERE employee_id = ? AND business_date >= ? AND business_date <= ?""",
            (employee_id, first, last),
        )
        already = {dict(row)["business_date"] for row in await cur.fetchall()}
        # 单日覆盖（票 07）：被覆盖过的天，样子以 `scheduling_overrides` 为准。
        # 结果行还在时下面 `already` 就挡住了（覆盖行是 `source='override'`，
        # 规则改动删不到它）；万一结果行丢了（换了库、手工清过），照记录补回来的
        # 是「那天被改成什么」，而不是照规则铺一个跟记录不符的班次。
        cur = await self._conn.execute(
            """SELECT business_date, shift_id, zone_id, voided FROM scheduling_overrides
               WHERE employee_id = ? AND business_date >= ? AND business_date <= ?""",
            (employee_id, first, last),
        )
        overrides = {dict(row)["business_date"]: dict(row) for row in await cur.fetchall()}
        # 每人每班次的固定区：铺行的时候一并写进结果，下游（卫生的当日分工）读这一列，
        # 不用自己去推「谁今天在哪」。
        zone_by_shift = await self._zone_defaults_for(employee_id)

        stamp = self._now_iso()
        written = 0
        for offset in range(EXPANSION_DAYS):
            business_date = shift_business_date(first, offset)
            if business_date > last:  # pragma: no cover - 窗口与循环同步，兜底而已
                break
            if business_date in already:
                continue
            override = overrides.get(business_date)
            if override is not None and int(override["voided"] or 0) == 1:
                # 「清空这一天」（2026-10-29）：那天就是不排。它不是「休」—— 以前照
                # 覆盖写成一行 `shift_id=NULL` 的休，等于把「清空」改回了「排休」。
                # 空白本身由「没有结果行」表达，读取端天然读成空白 / 待补。
                continue
            if override is not None:
                shift_id = override["shift_id"]
                zone_id = override["zone_id"]
                source = SOURCE_OVERRIDE
            else:
                shift_id = _cycle_shift(rule, business_date, anchor)
                zone_id = None if shift_id is REST else zone_by_shift.get(shift_id)
                source = SOURCE_RULE
            await self._conn.execute(
                """INSERT INTO staff_assignments
                       (employee_id, business_date, shift_id, zone_id, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (employee_id, business_date, shift_id, zone_id, source, stamp, stamp),
            )
            written += 1
        return written

    # ── 读 ──────────────────────────────────────────────────────────────

    async def _rows_and_shifts(self, cur) -> tuple[dict[str, dict], dict[int, dict]]:
        """把排班行按营业日索引，并取出这些行用到的班次（员工端两条门共用）。

        班次索引是在 `_shifts_for_display` 之后建的：`staff_assignments.shift_id` 没有外键，
        班次行被硬删掉时这里查不到，调用方 `.get()` 得 None 而不是 KeyError
        （员工页不该为一个别人手工删掉的名字 500；票 11 管班次增删）。
        """
        rows = {dict(row)["business_date"]: dict(row) for row in await cur.fetchall()}
        used = {int(row["shift_id"]) for row in rows.values() if row["shift_id"] is not None}
        shifts = {int(shift["id"]): shift for shift in await self._shifts_for_display(used)}
        return rows, shifts

    def _month_frame(self, start: date, today: str, days: list) -> dict:
        """两版月历共用的外壳：同一个 `lead` 算法与同一个展开窗口末日。

        `lead` = 第一格前面空几格（表头周一开头，周一=0，跟周表 `week_grid().days[].weekday`
        是同一套）。`window_end` 之后的日子注定是空的 ——
        前端得说出来这是「还没铺到」，不是「那天没人上班」/「那天休」。
        """
        return {
            "month": start.isoformat()[:7],
            "first_date": start.isoformat(),
            "today": today,
            "lead": start.weekday(),
            "window_end": self._window()[1],
            "days": days,
            # 「次日」是不是已经过了 22:00 的截止、2 天以上的假要提前几天 —— 员工端拿这两项
            # 把「请假 / 换班」在控件上先挡掉（2026-10-29 用户裁定；服务层仍是权威）。
            "next_day_locked": self.next_day_locked(today),
            "leave_lead_days": MULTI_DAY_LEAVE_LEAD_DAYS,
        }

    async def _on_roster_ids(self) -> set[int]:
        """在册的人：花名册里**已批准且未停用**的那些。

        `staff_assignments` 是物化的结果：员工停用（离职）只改花名册，90 天窗口里已经
        铺下的行照旧躺着，`expand()` 也仍会给有规则的人补行 —— 所以「谁在上班」不能只数
        结果行。`week_grid` 一直是这么筛的（并在 `excluded` 里报「另有 N 人已停用」），
        批量落笔的「不在册」也用它；月历 / 当日名单 / 员工看到的全店上班人数以前直接数行，
        于是同一天给出两个人数（停用的人只在一处出现）。现在这几处读口都从这里取判据。

        **不清行、不落任何写入**：停用只是「他不在名单里」，重新启用（`enable`）时结果行
        还在，人立刻回到名单与人数里 —— 这也正是「停用不删历史」的意思。
        """
        roster = await EmployeeAccounts(self._write_lock_owner).list_roster()
        return {
            employee["id"]
            for employee in roster
            if employee["approved"] and not employee["resigned"]
        }

    @_needs_migration
    async def month_calendar(self, month: str) -> dict:
        """一个月的月历：每天各班的**人数**（票 02 的验收项）。

        只数 `shift_id IS NOT NULL` 的行：休不是「上了某个班」，人数不该把它算进去。
        `overridden` 是那天有几行是**单日覆盖**写的（票 07）：月历靠它在格子上打个点，
        点开看「谁被改了、改了成什么」—— 它和人数走的是两条查询，「改成休」那种改动
        在人数里看不出来，在这个计数里看得出来。

        **只数在册的人**（`_on_roster_ids`）：停用 / 待批准的人名下可能还躺着结果行
        （停用不删行），他们既不算「上班人数」，也不算「那天被改过」的标记 —— 与
        `week_grid`、当日名单、员工月历的 `staff_count` 是同一份名单判据。
        """
        start, next_month = _month_bounds(month)
        first = start.isoformat()

        # 名单判据拼成一段 SQL 条件：空名单走 `AND FALSE`（同一条语句、结果自然是 0），
        # 不为「一个在册的人都没有」再写一套空实现。
        on_roster = sorted(await self._on_roster_ids())
        marks = ", ".join("?" for _ in on_roster)
        on_roster_clause = f"AND employee_id IN ({marks})" if on_roster else "AND FALSE"

        cur = await self._conn.execute(
            f"""SELECT business_date, shift_id, COUNT(*) AS n FROM staff_assignments
               WHERE business_date >= ? AND business_date < ? AND shift_id IS NOT NULL
                 {on_roster_clause}
               GROUP BY business_date, shift_id""",
            (first, next_month.isoformat(), *on_roster),
        )
        counted: dict[str, dict[int, int]] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            counted.setdefault(mapping["business_date"], {})[int(mapping["shift_id"])] = int(
                mapping["n"]
            )
        used = {shift_id for per_shift in counted.values() for shift_id in per_shift}
        shifts = await self._shifts_for_display(used)

        # 那天有几行是被单日覆盖写下的（票 07 的标记）：>0 就是「这一天跟规则不一样」。
        # 休也算 —— 「被改成休」正是店长最需要一眼看到的那种改动，而它不在人数里。
        cur = await self._conn.execute(
            f"""SELECT business_date, COUNT(*) AS n FROM staff_assignments
               WHERE business_date >= ? AND business_date < ? AND source = ?
                 {on_roster_clause}
               GROUP BY business_date""",
            (first, next_month.isoformat(), SOURCE_OVERRIDE, *on_roster),
        )
        overridden: dict[str, int] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            overridden[mapping["business_date"]] = int(mapping["n"])

        # 那天有几个人**休半天**（票 07）：他们仍然算进上面那个人数（那天确实上班），
        # 这一位只让月历在格子上多报一句「有几个人只上半天」—— 半天休是结果行上的一个
        # 标志，不改人数口径，所以单开一条查询、不往 `counts` 里掺。
        cur = await self._conn.execute(
            f"""SELECT business_date, COUNT(*) AS n FROM staff_assignments
               WHERE business_date >= ? AND business_date < ? AND half_rest = 1
                 AND shift_id IS NOT NULL
                 {on_roster_clause}
               GROUP BY business_date""",
            (first, next_month.isoformat(), *on_roster),
        )
        half_rest: dict[str, int] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            half_rest[mapping["business_date"]] = int(mapping["n"])

        today = self.today()
        days = []
        for day in _each_day(start, next_month):
            key = day.isoformat()
            per_shift = counted.get(key, {})
            days.append({
                "business_date": key,
                "day": day.day,
                "is_today": key == today,
                "counts": {str(s["id"]): per_shift.get(s["id"], 0) for s in shifts},
                "total": sum(per_shift.get(s["id"], 0) for s in shifts),
                "overridden": overridden.get(key, 0),
                # 那天几个人「休半天」（票 07）；0 = 没有（月历不画这个标记）。
                "half_rest": half_rest.get(key, 0),
            })

        return {**self._month_frame(start, today, days), "shifts": shifts}

    @_needs_migration
    async def day_detail(self, business_date: str) -> dict:
        """某一天：各班**是谁、在哪个区**（月历点开那天看的就是这个）。

        键叫 `groups` 而不是 `shifts`：`/calendar` 的 `shifts` 是班次列表，这里每一组
        还带人数和名字，同名会让调用方以为形状一样。

        工作区读的是**结果行上的 `zone_id`**，不是现在的固定区配置：那天写下来是什么
        就是什么 —— 事后改固定区只管今天与以后，不回头改写已经过去的日子。

        每个人带一个 `overridden`：这一行的 `source` 是 `override`（票 07 的单日覆盖），
        也就是「这天他跟规则不一样」。面板靠它标出来、并给出「撤销覆盖」这个动作。

        休的人不只给一个数：`off_people` 是跟 `groups` 同形的一份名单。他们也要能被点开
        —— 店长把某人改成休之后，得从那格再把他改回上班（或者撤掉那次改动）。

        同样是「这天没有班」，每人还带一个 `leave`：`True` 是**批了请假**，`False` 是本来
        就休。结果行里两者都是 `shift_id` 空，区别只在覆盖记录的 `kind`（票 08）——
        这个字段就是为那句「和本来就休看得出区别」给的判据，前端不自己猜。

        `half_rest`（票 07 / ADR 0114）是「这个人这天只上半天」：他仍然在自己的班次那一
        组里（那天确实上班），面板靠这个字段在他名字旁边写一句「休半天」。设置它的门有
        考勤表页与周表「休半天」笔两扇（ADR 0122）。

        **只出在册的人**（`_on_roster_ids`）：停用（离职）/ 还没批准的人名下可能还留着
        结果行（停用不删行），他们不该出现在「这天谁上班」的名单里，也不该把人数字段
        撑大 —— 与 `week_grid` 的名单判据、月历的人数口径是同一份（`my_day` 直读这里，
        员工端因此也不会看到「已离职的同事明天上班」）。
        """
        day = _require_business_date(business_date)
        names = await self._name_index()
        zone_names = await self._zone_name_index()
        on_roster = await self._on_roster_ids()

        cur = await self._conn.execute(
            """SELECT employee_id, shift_id, zone_id, source, half_rest
               FROM staff_assignments
               WHERE business_date = ?""",
            (day,),
        )
        by_shift: dict[Any, list[tuple[int, Optional[int], bool, bool]]] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            employee_id = int(mapping["employee_id"])
            if employee_id not in on_roster:
                # 不在册：他那天的行不参与这一天的名单（连「休」那一组也不算）。
                continue
            key = REST if mapping["shift_id"] is None else int(mapping["shift_id"])
            zone = None if mapping["zone_id"] is None else int(mapping["zone_id"])
            overridden = mapping["source"] == SOURCE_OVERRIDE
            half_rest = _is_half_rest(mapping["shift_id"], mapping["half_rest"])
            by_shift.setdefault(key, []).append(
                (employee_id, zone, overridden, half_rest)
            )

        def person(
            employee_id: int,
            zone: Optional[int],
            overridden: bool,
            half_rest: bool = False,
            leave: bool = False,
        ) -> dict:
            return {
                "id": employee_id,
                "name": names.get(employee_id, ""),
                # 没配区就是 None，前端渲染成「（未配区）」——
                # 「这个人这天在哪」还没定，不是错误。
                "zone": zone_names.get(zone) if zone is not None else None,
                # 区也按 **id** 给一份：编辑器要预填下拉，按名字反查不可靠
                # （`hygiene_zones` 的名字没有唯一约束，重名会挑错那个）。
                "zone_id": zone,
                "overridden": overridden,
                # 半天休（票 07）：那天仍然在这个班次这一组里，只是上半天。
                "half_rest": half_rest,
                # 只有不上班的人可能是请假；上班的人这里恒为 False（票 08）。
                "leave": leave,
            }

        shifts = await self._shifts_for_display({key for key in by_shift if key is not REST})
        groups = []
        for shift in shifts:
            people = sorted(
                by_shift.get(shift["id"], []),
                key=lambda item: names.get(item[0], ""),
            )
            groups.append({
                "shift": shift,
                "count": len(people),
                "people": [person(*item) for item in people],
            })
        rest_ids = sorted(by_shift.get(REST, []), key=lambda item: names.get(item[0], ""))
        # 「休」这一组里谁是请假：只问这些人（名单不长），而且查询按 employee_id 领头 ——
        # 跟 0007 那条索引的前导列对齐（复核 F7 的结论）。
        leave_ids = await self._leave_ids([item[0] for item in rest_ids], day)
        return {
            "business_date": day,
            "groups": groups,
            "total": sum(group["count"] for group in groups),
            "off_count": len(rest_ids),
            "off_people": [
                person(employee_id, zone, overridden, leave=employee_id in leave_ids)
                for employee_id, zone, overridden, _half in rest_ids
            ],
            # 过去的日子也能撤（2026-10-18 用户裁定放开）：前端据此显示「撤销覆盖」按钮。
            # 下界与「清除班次」同一条（可补记窗口），超窗口才收起来。
            "undoable": day >= self._backfill_floor(self.today()),
        }

    @_needs_migration
    async def week_grid(self, start: Optional[str] = None) -> dict:
        """「员工 × 周」矩阵：这一周每个人每天上什么班、在哪个区（票 A）。

        与 `day_detail` 用的是同一套判据：人名与名单都来自公共层的 `list_roster()`
        （`_name_index` 也是从它建的，所以不另起一套）、区名走 `_zone_name_index`、
        班次走 `_shifts_for_display`、请假走 `_leave_ids` ——
        两个视角在同一个格子上说的必须是同一件事。

        `start` 归一化到那一周的**周一**（`d - timedelta(days=d.weekday())`），跟月历的
        `lead = start.weekday()` 是同一条周起点；不给就是今天那一周。跨月周照常返回
        这 7 天，不拆表（`spans_months` 只是给前端一个提示位）。

        每人**七个日期键都在**：没有那一行时 `scheduled=False`，跟 `my_days`/`my_month`
        一样由服务层说清「还没有你的班」，前端不靠缺键去猜。两种 `False` 也分得开：
        `in_window=False` 是「还没铺到」（窗口末日之后），其余是这行还没写。

        `days[].row_count` 是那天**全体**的行数（含休）：0 表示那天一行都没有
        （系统启用前、或谁都没配规则），不能读成「大家都在休」。

        **行序在服务端算完再给出去**（票 14；ADR 0107 决策 10）：有规则的人按**规则起点
        班次**分片（`cycle` 里第一个不是「休」的班次；整轮全是休、或规则读不出来的当
        没规则），片的先后用班次表的 `sort_order`（同序按 id）；片内管理层
        （`admin_caps` 非空）置顶，其余按注册先后（`hygiene_employees.id` 升序）；
        没规则的人整块沉底，内部同样按注册先后。前端不重排。

        窗口外照样返回 7 天、不报错：店长会往两头翻，翻到的那一周得是空的而不是 400。

        `cells[].half_rest`（票 07 / ADR 0114）：这一格是**半天休** —— 那天仍然挂着
        `shift_id` 那个班次（格子里照旧写班次缩写），只是旁边多一个「半」标记。周表读
        它做展示、也能用笔栏那颗「休半天」写它（ADR 0122）。
        """
        today = self.today()
        if start in (None, ""):
            anchor = date.fromisoformat(today)
        else:
            # 坏格式走 `_require_business_date` 那句 `invalid_business_date`（400），
            # 不在这里自己拼一句新的。
            anchor = date.fromisoformat(_require_business_date(start))
        first = anchor - timedelta(days=anchor.weekday())
        last = first + timedelta(days=6)
        window_end = self._window()[1]

        # 7 天一条 SQL：区间右开（`last + 1 天`），`business_date` 是 TEXT 列。
        cur = await self._conn.execute(
            """SELECT employee_id, business_date, shift_id, zone_id, source, half_rest
               FROM staff_assignments
               WHERE business_date >= ? AND business_date < ?""",
            (first.isoformat(), shift_business_date(last.isoformat(), 1)),
        )
        by_employee: dict[int, dict[str, dict]] = {}
        row_count: dict[str, int] = {}
        used: set[int] = set()
        for row in await cur.fetchall():
            mapping = dict(row)
            key = mapping["business_date"]
            shift_id = None if mapping["shift_id"] is None else int(mapping["shift_id"])
            row_count[key] = row_count.get(key, 0) + 1
            if shift_id is not None:
                used.add(shift_id)
            by_employee.setdefault(int(mapping["employee_id"]), {})[key] = {
                "shift_id": shift_id,
                "zone_id": None if mapping["zone_id"] is None else int(mapping["zone_id"]),
                # 跟 `day_detail` 同一个判据：这一行是单日覆盖写的。
                "overridden": mapping["source"] == SOURCE_OVERRIDE,
                # 半天休（票 07）：只读的一位，周表照旧写班次缩写 + 一个「半」标记。
                "half_rest": _is_half_rest(shift_id, mapping["half_rest"]),
            }

        days = []
        for offset in range(7):
            day = first + timedelta(days=offset)
            key = day.isoformat()
            days.append({
                "business_date": key,
                "day": day.day,
                "month": day.month,
                # 0=周一 … 6=周日，与 `_month_frame` 的 `lead`（`date.weekday()`）同一套。
                "weekday": day.weekday(),
                "is_today": key == today,
                "is_weekend": day.weekday() >= 5,
                # 窗口末日之后注定没有行 —— 但那是「还没铺到」，不是「那天休」。
                "in_window": key <= window_end,
                "row_count": row_count.get(key, 0),
            })

        # 一次读名单：既建区名索引（格子里写区名），也原样给回应（票 01：算「要顶的档」
        # 要知道每个区跑不跑白班 / 夜班，前端不靠 `/roster` 也拿得到这份名单）。
        zones = await self._zone_directory().list_zones()
        zone_names = {zone["id"]: zone["name"] for zone in zones}
        # 每人每班次的固定区（票 01）：周表自己的回应里带上，前端算「要顶的档」时
        # 不用再打 `/roster`（周表的护栏是只用排班自己的接口）。没配过的人不在里面。
        defaults = await self.zone_defaults()
        # 启用的全出 + 停用但这一周真有行的才出（`_shifts_for_display` 的老口径）：
        # 抽屉要拿它当班次选择列，所以启用的一个不少。
        shifts = await self._shifts_for_display(used)
        shift_names = {int(shift["id"]): shift["name"] for shift in shifts}
        rules = await self._rule_rows()
        # 行序（票 14）：有规则的人按**规则起点班次**分片 —— `cycle` 里第一个不是「休」的
        # 班次（整轮全是休 / 规则读不出来都当没规则，见 `_cycle_lead_shift`）。
        leads = {
            employee_id: self._cycle_lead_shift(raw) for employee_id, raw in rules.items()
        }
        # 片的先后 = 班次表的显示顺序（`sort_order`，同序按 id）：票 11 让店长自己排的那个。
        # 用**全表**（含停用）建索引 —— 停用的班次仍可能留在某人的轮转里（人工 SQL 写下的
        # 历史数据），它照样该有自己的位置；表里已经没有的那个班次排在所有片之后。
        shard_order = {
            int(shift["id"]): index
            for index, shift in enumerate(await self.list_shifts(include_inactive=True))
        }
        last_shard = len(shard_order)

        def row_order(employee: dict) -> tuple:
            """行序的排序键：``(有没有分片, 片序, 管理层, 注册先后)``。

            管理层 = `admin_caps` 非空 —— 与花名册那个「管理员」派生标签同一判据
            （`_roster_row` 已经用 `parse_caps` 解析过，这里不再解一次 JSON）。
            """
            lead = leads.get(employee["id"])
            if lead is None:
                # 没规则（含整轮全休、规则读不出来）：整块沉底，内部按注册先后。
                return (1, last_shard, 0, employee["id"])
            return (
                0,
                shard_order.get(lead, last_shard),
                0 if employee["admin_caps"] else 1,
                employee["id"],
            )

        # 每天的请假判据：只问那天不上班的人（`day_detail` 就是这么调的 —— 查询按
        # employee_id 领头，对齐 0007 那条索引）。
        leave_ids: dict[str, set[int]] = {}
        for day in days:
            key = day["business_date"]
            off = [
                employee_id
                for employee_id, by_day in by_employee.items()
                if by_day.get(key, {}).get("shift_id") is None
            ]
            leave_ids[key] = await self._leave_ids(off, key)

        employees = []
        # 被过滤掉的人也要说一句（§3.7）：静默丢人正是「以为漏排」的来源。
        excluded = {"resigned": 0, "pending": 0}
        roster = await EmployeeAccounts(self._write_lock_owner).list_roster()
        for employee in sorted(roster, key=row_order):
            if employee["resigned"]:
                excluded["resigned"] += 1
                continue
            if not employee["approved"]:
                excluded["pending"] += 1
                continue
            rows = by_employee.get(employee["id"], {})
            cells = {}
            for day in days:
                key = day["business_date"]
                row = rows.get(key)
                shift_id = None if row is None else row["shift_id"]
                zone_id = None if row is None else row["zone_id"]
                cells[key] = {
                    "scheduled": row is not None,
                    "shift_id": shift_id,
                    # 班次行被硬删掉时名字给 None（没有外键，同 `my_days`）。
                    "shift_name": None if shift_id is None else shift_names.get(shift_id),
                    "zone_id": zone_id,
                    "zone_name": None if zone_id is None else zone_names.get(zone_id),
                    "overridden": False if row is None else row["overridden"],
                    # 半天休（票 07）：这一格是「只上半天」——读侧照旧写班次缩写，
                    # 旁边多一个「半」标记（周表笔栏也能设它，ADR 0122）。
                    "half_rest": False if row is None else row["half_rest"],
                    # 只有不上班的人可能是请假（跟 `day_detail` 同一条口径）。
                    "leave": bool(
                        row is not None
                        and row["shift_id"] is None
                        and employee["id"] in leave_ids[key]
                    ),
                }
            employees.append({
                "id": employee["id"],
                "name": employee["name"],
                "job_title": employee["job_title"],
                # 配过规则就有这一行（`_rule_rows()` 只出配过的，`rule is None` 是没配过）。
                "has_rule": employee["id"] in rules,
                "zone_defaults": [
                    {
                        "shift_id": shift_id,
                        "zone_id": zone_id,
                        # 区被删了（`staff_assignments.zone_id` / 这里都没有外键）：
                        # 名字给 None，整行照留 —— 弹层要照列并标「工作区已删」。
                        "zone_name": None if zone_id is None else zone_names.get(zone_id),
                    }
                    # 顺序在这里钉死（`shift_id` 升序）：`zone_defaults()` 不带 ORDER BY，
                    # 靠 DB 的物理顺序迟早会漂。
                    for shift_id, zone_id in sorted(defaults.get(employee["id"], {}).items())
                ],
                "cells": cells,
            })

        return {
            "start": first.isoformat(),
            "end": last.isoformat(),
            "today": today,
            "window_end": window_end,
            # 只是给前端的提示位，不是判据：跨月周照常一张表。
            "spans_months": first.month != last.month,
            "days": days,
            "shifts": shifts,
            # 区名单连同两个班次开关一起给（票 01）；与 `/roster` 同一份口径、按 id。
            "zones": zones,
            "employees": employees,
            "excluded": excluded,
        }

    @_needs_migration
    async def my_days(self, employee_id: int, days: int = MY_WINDOW_DAYS) -> dict:
        """员工自己的今天和往后几天：一天一行，带那天写下的班次与工作区。

        「今天」是排班自己那个 06:00 切日的今天（`self.today()`），跟月历、当日分工
        读的是同一个日期 —— 员工凌晨一点看到的和店长看到的必须是同一天。

        每人先单独补齐（只这一人，不是全店）：店长刚配完规则、月历还没人打开过，
        员工这一眼也得是对的。窗口尽头的天数就是「还没排」，不报错。

        没铺过的日子**也占一行**：`scheduled=False` 是「还没有你的班」，`scheduled=True`
        而 `shift_id=None` 是「那天休」。两者在员工页上是两句话，不能让调用方从
        「缺行」里去猜 —— 判据在服务层，前端只翻译。

        `leave=True` 是第三种「没有班」：**批了的请假**（票 08）。员工自己提的那条申请
        批下来那天，他该看到的不是「休」而是「请假」。

        `half_rest`（票 07）是「那天上了半天班」：**仍然是上班那一态**（`scheduled=True`
        且 `shift_id` 非空），只是员工页要说「休半天」而不是「上班」—— 否则员工会以为
        要上整天（ADR 0114）。四态因此是：还没排 / 休 / 休半天 / 上班。
        """
        await self.expand(employee_id)
        first = self.today()
        count = max(int(days), 1)
        last = shift_business_date(first, count - 1)
        cur = await self._conn.execute(
            """SELECT business_date, shift_id, zone_id, half_rest FROM staff_assignments
               WHERE employee_id = ? AND business_date >= ? AND business_date <= ?
               ORDER BY business_date""",
            (employee_id, first, last),
        )
        rows, shifts = await self._rows_and_shifts(cur)
        zone_names = await self._zone_name_index()
        leave_days = await self._leave_days(employee_id, first, last)

        out = []
        for offset in range(count):
            key = shift_business_date(first, offset)
            row = rows.get(key)
            shift_id = None
            zone_id = None
            if row is not None:
                shift_id = None if row["shift_id"] is None else int(row["shift_id"])
                zone_id = None if row["zone_id"] is None else int(row["zone_id"])
            # 班次行被硬删掉（`staff_assignments.shift_id` 没有外键）时名字给 None：
            # 员工页不该为了一个别人手工删掉的名字 500（票 11 管班次增删）。
            shift = shifts.get(shift_id) if shift_id is not None else None
            out.append({
                "business_date": key,
                "is_today": offset == 0,
                "scheduled": row is not None,
                "shift_id": shift_id,
                "shift_name": None if shift is None else shift["name"],
                # 班次色键（0043）：员工端整月页按它给「上班」那格上色。跟店长那一面同
                # 一份班次表（`_shifts_for_display`），所以两边不会是两种颜色。
                "shift_color": None if shift is None else shift.get("color_key"),
                "zone_id": zone_id,
                # 区名来自结果行上的 zone_id：那天写在行上的是哪个区就是哪个区，
                # 事后改固定区不改写过去（跟 `day_detail` 同一条口径）。
                "zone_name": None if zone_id is None else zone_names.get(zone_id),
                # 批了的请假（票 08）：「那天休」与「那天请假」是两句话。
                "leave": key in leave_days,
                # 半天休（票 07）：那天仍然上班（`shift_id` 非空），只是上半天。
                # 员工页靠它把「上班」说成「休半天」（ADR 0114 的四态）。
                "half_rest": row is not None and _is_half_rest(row["shift_id"], row["half_rest"]),
            })
        return {"today": first, "days": out}

    @_needs_migration
    async def my_month(self, employee_id: int, month: Optional[str] = None) -> dict:
        """员工自己的一个月：一天一格，格子里是那天的班别（票 06，「今天」页点进整月）。

        跟 `month_calendar`（店长那一版数的是各班**人数**）不是一回事：这条只回答
        「我那天上什么班」。`scheduled` / `shift_id` / `shift_name` 跟 `my_days` 是
        同一套口径 —— 休（行在、`shift_id` 空）、还没铺到（没有行）、班次被删（行在、
        名字 None）三种状态在服务层分好，前端只翻成人话。

        格子只写班别、不带工作区：手机一行七格放不下「白班 · 案板」，工作区在「今天」
        页那张卡上（`spec.md` 留给第 6 步的那个待定项按这个口径定）。`zone_name` 仍然
        照给（跟 `my_days` 同一套）：格子不写不等于数据没有 —— 点开某天的名单里要写
        「我这天在哪」，那一处正是它（票 13）。

        `staff_count` 是那天**全店**上班的人数（`shift_id` 非空的行数，休不算）：票 13
        起员工月历在每个格子上写出这个数，好知道哪天店里人多。它跟「我的班」是两件事，
        所以是两个字段，不揉进 `scheduled`。

        翻到过去的月份只显示已经铺过的日子（展开只往今天以后补，不回头），`window_end`
        之后的格子注定是「还没排」—— 跟店长月历同一个说法。

        `half_rest`（票 07 / ADR 0114）：那一天是**半天休** —— 仍然是上班那一态
        （`scheduled=True`、有班次），只是格子里写「休半天」而不是班别。
        """
        await self.expand(employee_id)
        start, next_month = _month_bounds(self.today()[:7] if month is None else month)
        first = start.isoformat()

        cur = await self._conn.execute(
            """SELECT business_date, shift_id, zone_id, half_rest FROM staff_assignments
               WHERE employee_id = ? AND business_date >= ? AND business_date < ?
               ORDER BY business_date""",
            (employee_id, first, next_month.isoformat()),
        )
        rows, shifts = await self._rows_and_shifts(cur)
        zone_names = await self._zone_name_index()
        # 整月里批了请假的那几天（票 08）：`_leave_days` 的两端都含，所以末界是下月一号
        # 的前一天 —— 用 `shift_business_date` 退一天，不自己算月份长度。
        leave_days = await self._leave_days(
            employee_id, first, shift_business_date(next_month.isoformat(), -1)
        )
        # 每天全店上班几个人（票 13）。**只数全店**，不看是谁：这条查询跟我的行无关，
        # 所以它一次就把整月数完，不按天循环（31 天 31 条 SQL 是没必要的）。
        # 与 `month_calendar` 的人数同一条口径：`shift_id IS NOT NULL` 才算上班，休不算。
        staff_count = await self._staff_counts(first, next_month.isoformat())

        today = self.today()
        days = []
        for day in _each_day(start, next_month):
            key = day.isoformat()
            row = rows.get(key)
            shift_id = None if row is None or row["shift_id"] is None else int(row["shift_id"])
            zone_id = None if row is None or row["zone_id"] is None else int(row["zone_id"])
            shift = shifts.get(shift_id) if shift_id is not None else None
            days.append({
                "business_date": key,
                "day": day.day,
                "is_today": key == today,
                "scheduled": row is not None,
                "shift_id": shift_id,
                "shift_name": None if shift is None else shift["name"],
                # 班次色键（0043）：整月格子里「上班」那格的颜色，跟店长那一面同一份班次表。
                "shift_color": None if shift is None else shift.get("color_key"),
                # 我那天在哪个区（格子里不写，点开那天的名单里写）：跟 `my_days` 同一条
                # 口径 —— 区名来自那天结果行上的 `zone_id`，事后改固定区不改写过去。
                "zone_name": None if zone_id is None else zone_names.get(zone_id),
                # 跟 `my_days` 同一个字段、同一条口径：格子里的「请假」不是「休」。
                "leave": key in leave_days,
                # 半天休（票 07）：那一天仍然是上班（有班次），格子里写「休半天」。
                "half_rest": row is not None and _is_half_rest(row["shift_id"], row["half_rest"]),
                "staff_count": staff_count.get(key, 0),
            })

        return self._month_frame(start, today, days)

    async def _staff_counts(self, first: str, last: str) -> dict[str, int]:
        """区间内每天全店有几个人上班（`shift_id` 非空的行数，休不算）。

        `last` 是**右开**的（跟 `month_calendar` 那条一样是下月一号），别按两端都含读。

        **只数在册的人**：与 `month_calendar` 的 `total` 是同一个数（员工月历格子上的
        `staff_count` 就是它），停用的人两边都不算 —— 否则同一天店长看到 2 人、员工看到
        3 人。名单判据见 `_on_roster_ids`（空名单用 `AND FALSE` 走同一条语句）。
        """
        on_roster = sorted(await self._on_roster_ids())
        marks = ", ".join("?" for _ in on_roster)
        on_roster_clause = f"AND employee_id IN ({marks})" if on_roster else "AND FALSE"
        cur = await self._conn.execute(
            f"""SELECT business_date, COUNT(*) AS n FROM staff_assignments
               WHERE business_date >= ? AND business_date < ? AND shift_id IS NOT NULL
                 {on_roster_clause}
               GROUP BY business_date""",
            (first, last, *on_roster),
        )
        counts: dict[str, int] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            counts[mapping["business_date"]] = int(mapping["n"])
        return counts

    @_needs_migration
    async def my_day(self, business_date: str) -> dict:
        """员工点开某一天看到的全店名单（票 13）：各班是谁、在哪个区，以及那天谁不上班。

        形状照着店长那版 `day_detail`（人名、请假、工作区都是从那一条读的，判据只有
        一份），但**只留员工该看的那几项**：他的 `zone_id`、`overridden`（这天他跟规则
        不一样，是「谁被临时调走」的管理信息）与 `undoable`（能不能撤销覆盖，那是店长
        的动作）都不出去。员工面要的是「今天店里谁在、在哪」和「谁休 / 谁请假」。

        请假与休分开：两个人都是没有班次，`leave` 说的是「批过的假」而不是「排班给的休」
        （票 08 的口径，跟员工页其他几处一致）。

        空组（那天某个班一个人都没有）不出去：员工看的是名单，不是「这个班今天没人」。

        「谁在名单里」跟着 `day_detail` 走（`_on_roster_ids`）：停用 / 待批准的人不出现，
        于是员工手机上也不会看到「已离职的同事明天上班」。
        """
        detail = await self.day_detail(business_date)
        groups = [
            {
                "shift_id": group["shift"]["id"],
                "shift_name": group["shift"]["name"],
                "count": group["count"],
                "people": [
                    {"id": person["id"], "name": person["name"], "zone": person["zone"]}
                    for person in group["people"]
                ],
            }
            for group in detail["groups"]
            if group["count"]
        ]
        return {
            "business_date": detail["business_date"],
            "today": self.today(),
            "groups": groups,
            "total": detail["total"],
            "off_count": detail["off_count"],
            # 休假名单：`leave` 分开「批过的假」与「排班给的休」，前端照它说两句话。
            "off_people": [
                {"id": person["id"], "name": person["name"], "leave": person["leave"]}
                for person in detail["off_people"]
            ],
            # 22:00 之后「次日」的假 / 换班提不了（2026-10-29 用户裁定）：前端拿这两项把
            # 控件先挡上（服务层才是权威，这里只是别让人白填一次表）。
            "next_day_locked": self.next_day_locked(self.today()),
            "leave_lead_days": MULTI_DAY_LEAVE_LEAD_DAYS,
        }

    @_needs_migration
    async def roster_with_rules(self, *, include_phone: bool = False) -> dict:
        """名单：全体花名册 + 每人当前那条规则 + 每人每班次的固定区。

        花名册来自**公共层**（`EmployeeAccounts.list_roster`），工作区名单也来自公共层
        （`ZoneDirectory.list_zones`，读的是卫生建的那张 `hygiene_zones`）；排班不自己去
        join `hygiene_employees` —— 表名是公共层的实现细节（`spec.md` 的「分层」）。

        **读不出来的规则显示出来，不抛错**（`_rule_for_display`）：这一页是店长唯一能
        重配周期的入口，它跟着别人的脏数据 400，那个人就只能进库改。所以这一条走
        `_rule_rows()`（不解码）而不是 `list_rules()`。

        **手机号按调用方身份投影**（`include_phone`，默认不给）：这条读口在
        `_require_scheduler` 那扇门后面（2026-10-14 起员工会话 + `schedule` 能力也进得来），
        而手机号是同事的私人联系方式，不是排班需要的字段 —— 与 `/me/colleagues`
        「不给手机号」同一条口径。管理端会话才给（`api/scheduling.py` 的路由按身份传
        `include_phone`），管理端花名册页（`/api/hygiene/admin/roster`）本来就有自己那份
        带手机号的名单，不靠这一条。默认 `False` 是 fail-closed：以后新加的调用方
        不显式开口就要不到手机号。
        """
        employees = await EmployeeAccounts(self._write_lock_owner).list_roster()
        raw_rules = await self._rule_rows()
        shifts = await self.list_shifts()
        known_shift_ids = {shift["id"] for shift in shifts}
        defaults = await self.zone_defaults()

        rows = []
        for employee in employees:
            row = {
                "id": employee["id"],
                "name": employee["name"],
            }
            if include_phone:
                # 只给管理端：员工端的「我是谁」在 `/me` 那几条里（读的永远是自己）。
                row["phone"] = employee["phone"]
            row.update({
                "job_title": employee["job_title"],
                "approved": employee["approved"],
                # 离职（2026-10-29 取代「停用」）：到今天为止算不算已离职 + 原始日期
                # （`''` = 已离职、日期待补）。前端拿它决定要不要提醒、标不标「已离职」。
                "resigned": employee["resigned"],
                "resigned_at": employee["resigned_at"],
                # 没配过规则是 None；配过但读不出来是
                # {"cycle": None, "anchor_date": None, "invalid": True}。
                "rule": (
                    None
                    if employee["id"] not in raw_rules
                    else self._rule_for_display(raw_rules[employee["id"]], known_shift_ids)
                ),
                # {班次 id: 工作区 id}；没配过就是空对象
                "zone_defaults": defaults.get(employee["id"], {}),
            })
            rows.append(row)
        return {
            "shifts": shifts,
            "zones": await self._zone_directory().list_zones(),
            "employees": rows,
        }

    async def _name_index(self) -> dict[int, str]:
        employees = await EmployeeAccounts(self._write_lock_owner).list_roster()
        return {
            employee["id"]: (employee["name"] or employee["phone"])
            for employee in employees
        }

    # ── 请假申请（票 08） ───────────────────────────────────────────────
    #
    # 申请挂在 `scheduling_requests`（0008），**批准才写进排班**：批准那天写一条覆盖
    # 记录（`kind=leave`、`shift_id` 空）+ 一行结果（`source=override`）。结果表里
    # 「请假」和「本来就休」都是没有班次，区别只记在覆盖记录的 `kind` 上 —— 当日分工
    # 与员工页都从那里读 `leave`（见 `day_detail`、`my_days`）。驳回与撤回一个字都不写。

    @_needs_migration
    async def submit_leave(
        self,
        employee_id: int,
        start_date: str,
        end_date: Optional[str] = None,
        note: Optional[str] = None,
    ) -> dict:
        """员工给自己提一条请假：一天，或一段日期（**两端都算**）。

        落 `pending_manager` —— 请假不经过换班那一步「等对方确认」（`spec.md` 的状态机）。
        申请本身**不碰排班**：批了才写覆盖行，驳回与撤回一个字都不写。

        提前量限制还没定（`spec.md` 的待定项）：现在只要求不是过去的日期、末日还在展开
        窗口里 —— 窗口外那天本来就「还没排到」，批了也没有一行可以变成请假。

        **今天与窗口末日只读一次**（同 `set_override`）：申请落库那一刻的营业日，跟
        「不是过去」和「在窗口里」两处判断用的是同一个。
        """
        employee_id = int(employee_id)
        roster = await EmployeeAccounts(self._write_lock_owner).list_roster()
        if employee_id not in {employee["id"] for employee in roster}:
            raise SchedulingError("unknown_employee", "unknown_employee")
        if not str(start_date or "").strip():
            # 字段整个没给（前端提交按钮本来就是灰的，这是给手写的调用方兜一句中文）：
            # 跟「格式不对」分开说，不然员工读到的是「日期格式应该是 YYYY-MM-DD」，
            # 而他手里根本没有一个日期可以改。
            raise SchedulingError("missing_day", "missing_day")
        first = _require_business_date(start_date)
        last = first if end_date in (None, "") else _require_business_date(end_date)
        if last < first:
            raise SchedulingError("bad_range", "bad_range")
        now = self._now_dt()
        today = current_workbench_date(now)
        last_day = self._window_from(today)[1]
        if last < today:
            # 跟票 07 的 `past_day` 是同一条口径，但错的是「给过去提申请」而不是
            # 「改过去的排班」：文案分开，别让员工读到「排班写下的历史不重写」。
            raise SchedulingError("past_leave", "past_leave")
        if last > last_day:
            # 第二参填进那句提示的 `{}`：报**真实的窗口末日**（跟票 07 同一条口径，
            # 别把 `EXPANSION_DAYS` 写死在文案里）。
            raise SchedulingError("beyond_leave", last_day)
        # 22:00 之后不提次日、2 天以上提前 7 天（2026-10-29 用户裁定）。
        self._check_apply_window(
            first, last, today, now, days=len(_each_day_between(first, last)),
        )
        return await self._insert_request(
            employee_id, KIND_LEAVE, first, last, _clean_note(note), STATUS_PENDING_MANAGER
        )

    @_needs_migration
    async def my_requests(self, employee_id: int) -> dict:
        """我自己提过的申请 + **等我回应的换班**，新的在前（票 08 / 09 的验收项）。

        两个方向都在这里：`requests` 是我提的（每条现在到哪一步），`incoming` 是别人问我
        换班的（`pending_peer`、还没回应的），后者带两个人那天的班，好在手机上直接决定。

        `status` 是机器可读的那个（前端翻成人话），`decided_at` 只在批 / 驳 / 撤之后才有。
        换班那条多一个 `peer_name`：员工端要写「等王五同意」，而它手上没有花名册。
        """
        employee_id = int(employee_id)
        rows = await self._request_rows(employee_id=employee_id)
        names = await self._name_index()
        for row in rows:
            peer_id = row.get("peer_employee_id")
            row["peer_name"] = None if peer_id is None else names.get(int(peer_id), "")
        return {
            "today": self.today(),
            "requests": rows,
            "incoming": await self._incoming_swaps(employee_id),
        }

    @_needs_migration
    async def cancel_request(self, employee_id: int, request_id: int) -> dict:
        """申请人撤回自己**还没被批**的申请（票 08 / 09 的验收项）。

        撤回只动申请这一行：排班一个字不改 —— 还没批的申请本来就没写进排班。

        待决的两个状态都能撤：请假只有「等店长批」，换班还有「等对方同意」；对方已经
        同意、店长还没批的时候也能撤（票 09 的验收项）。

        别人的申请一律当作「不存在」：不告诉调用方「这条在，但不是你的」。
        """
        row = await self._request_by_id(request_id)
        if row is None or int(row["employee_id"]) != int(employee_id):
            raise SchedulingError("unknown_request", "unknown_request")
        if row["status"] not in (STATUS_PENDING_PEER, STATUS_PENDING_MANAGER):
            # 批完 / 驳完 / 已撤回都不再动：状态机只往前走（`spec.md` 的状态机）。
            raise SchedulingError("request_not_pending", "request_not_pending")
        return await self._decide(
            int(row["id"]),
            STATUS_CANCELLED,
            (STATUS_PENDING_PEER, STATUS_PENDING_MANAGER),
        )

    @_needs_migration
    async def inbox(self) -> dict:
        """店长待办（票 08 / 09）：等他批的申请 + 还没配规则的人。

        两种申请进的是同一个队列，卡的形状不一样：请假（`kind=leave`）带一份**预览**
        —— 批了之后那天每个班次还剩几个人（`days[*].after`）；换班（`kind=swap`）摊开
        **两个人那天的班**（`days[*].current_shift_*` 是申请人的，`peer_*` 是对方的），
        批了就是对调，没有人数可算。两种卡都带 `peer_employee_id` / `peer_name`（请假是空），
        前端少一处分支。

        人手够不够只把数字摊开 —— 服务层没有「最少几个人」这个配置，也就没有阈值可判，
        「只提示、不阻止」在实现上就是**根本没有那道闸**（票面的口径）。

        `days` 是申请区间的每一天（两端都算；换班就一天），`past=True` 的那几天批了也
        不会动（批准只写今天与以后 —— 跟店长手改排班那条 30 天回溯窗口不是同一条），
        先说清楚，别让店长以为批了就改了历史。

        **等对方点头的换班不在这里**（`pending_peer`）：那就是票 09 要的那条规矩 ——
        对方还没点，店长看不到。旧申请在前（先来先处理）：这是待办队列，不是「最新动态」。

        缺 0009 那一列时，读会降级成「没有换班」，可表里排上队的换班仍旧渲染不出来 ——
        那种情况报 `not_migrated`（让人去应用迁移）；而列在、这一行的对方却是空的
        （0009 被撤过又补回来的残骸）只是**跳过那一行**：它是修不回来的死数据，
        不该把整页待办连着别人的请假一起 503 掉。
        """
        await self.expand()
        today = self.today()
        rows = await self._request_rows(status=STATUS_PENDING_MANAGER, oldest_first=True)
        names = await self._name_index()
        shifts = await self.list_shifts()
        shift_names = {shift["id"]: shift["name"] for shift in shifts}
        # 「还没配规则的新人」只看**有没有那一行**（`_rule_rows()`），不看它读不读得出来：
        # 一条读不出来的规则也是配过的规则，把这个人算成新人只会让店长去配第二遍，
        # 而 `list_rules()` 那条解码路会让整页待办跟着 400（它的口径见那个 docstring）。
        rules = await self._rule_rows()
        roster = await EmployeeAccounts(self._write_lock_owner).list_roster()

        # 一天只查一次：同一批申请常指着同几天（一个人请的那几天，别人也可能请）。
        on_duty: dict[str, dict[int, Optional[int]]] = {}

        async def day_roster(day: str) -> dict[int, Optional[int]]:
            if day not in on_duty:
                cur = await self._conn.execute(
                    """SELECT employee_id, shift_id FROM staff_assignments
                       WHERE business_date = ?""",
                    (day,),
                )
                mapping: dict[int, Optional[int]] = {}
                for item in await cur.fetchall():
                    record = dict(item)
                    mapping[int(record["employee_id"])] = (
                        None if record["shift_id"] is None else int(record["shift_id"])
                    )
                on_duty[day] = mapping
            return on_duty[day]

        requests = []
        for row in rows:
            employee_id = int(row["employee_id"])
            if row["kind"] == KIND_SWAP:
                # 换班是另一种卡：两个人的班摊开、批了对调，没有「剩几个人」可算
                # （对调不改变任何班次的人数）。摆不出来的那一行给 `None`（跳过）。
                card = await self._swap_card(row, day_roster, names, shift_names, today)
                if card is not None:
                    requests.append(card)
                continue
            days = []
            for day in _each_day_between(row["start_date"], row["end_date"]):
                people = await day_roster(day)
                counts: dict[int, int] = {}
                for shift_id in people.values():
                    if shift_id is not None:
                        counts[shift_id] = counts.get(shift_id, 0) + 1
                mine = people.get(employee_id)
                after = []
                for shift in shifts:
                    if counts.get(shift["id"]) is None and mine != shift["id"]:
                        # 那天本来就没这个班的人：不往卡上堆「0 人」。
                        continue
                    left = counts.get(shift["id"], 0) - (1 if mine == shift["id"] else 0)
                    after.append({
                        "shift_id": shift["id"],
                        "shift_name": shift["name"],
                        "count": max(left, 0),
                    })
                days.append({
                    "business_date": day,
                    "past": day < today,
                    # 那天她原本在哪：`scheduled=False` 是「那天还没排到」，
                    # `scheduled=True` 而 `current_shift_id` 空是「本来就休」。
                    "scheduled": employee_id in people,
                    "current_shift_id": mine,
                    "current_shift_name": None if mine is None else shift_names.get(mine),
                    "after": after,
                })
            requests.append({
                "id": int(row["id"]),
                "employee_id": employee_id,
                "employee_name": names.get(employee_id, ""),
                "kind": row["kind"],
                "start_date": row["start_date"],
                "end_date": row["end_date"],
                "note": row["note"],
                "created_at": row["created_at"],
                # 请假没有「对方」，这两格照样给出来：两种卡形状一样，前端少一处分支。
                "peer_employee_id": None,
                "peer_name": "",
                "days": days,
            })

        return {
            "today": today,
            "requests": requests,
            # 「还没配规则的新人」：判据就是名单里没有规则的那几个（`rule is None`）。
            # `approved` / `resigned` 一起给出去，由前端决定要不要提醒 —— 已离职的人
            # 不该天天挂在待办上，但「谁离职了」是名单的事，服务层不替它下结论。
            "without_rule": [
                {
                    "id": employee["id"],
                    "name": employee["name"],
                    "phone": employee["phone"],
                    "job_title": employee["job_title"],
                    "approved": employee["approved"],
                    "resigned": employee["resigned"],
                    "resigned_at": employee["resigned_at"],
                }
                for employee in roster
                if employee["id"] not in rules
            ],
        }

    @_needs_migration
    async def approve_request(self, request_id: int) -> dict:
        """批一条申请：请假那几天变成请假，换班两个人那天对调（票 08 / 09 的验收项）。

        请假写的是**覆盖行**（`kind=leave`、`shift_id` 空）而不是改结果表的列：结果表里
        「请假」与「休」都是没有班次，区别只在覆盖记录的 `kind`。换班写两条覆盖行
        （`kind=swap`）+ 两条结果行：谁的班去了谁那里，见 `_approve_swap`。

        两种都只认 `pending_manager`：对方的点头（换班）是进这道门的条件，不是能跳过的一步。

        已经过去的日子**不补写**（批准只写今天与以后）：批得晚了就是晚了，那天当时
        怎么排的就留着。申请照样记成已批准，`applied_days` 说清写了哪几天 ——
        调用方把 `skipped_days` 明说出来，而不是假装那几天也改了。
        """
        row = await self._request_by_id(request_id)
        if row is None:
            raise SchedulingError("unknown_request", "unknown_request")
        if row["status"] != STATUS_PENDING_MANAGER:
            raise SchedulingError("request_not_pending", "request_not_pending")
        return await self._approve(row)

    @_needs_migration
    async def reject_request(self, request_id: int) -> dict:
        """驳回一条申请：**排班一个字不改**（票 08 的验收项），只把申请记为驳回。

        换班也一样：店长驳的是「这两个人对调」这件事，两人那天的班照旧。对方自己
        拒绝的那一步走的是 `answer_swap`（那条根本不进店长的队列）。
        """
        row = await self._request_by_id(request_id)
        if row is None:
            raise SchedulingError("unknown_request", "unknown_request")
        if row["status"] != STATUS_PENDING_MANAGER:
            raise SchedulingError("request_not_pending", "request_not_pending")
        result = await self._decide(int(row["id"]), STATUS_REJECTED, (STATUS_PENDING_MANAGER,))
        # 同上：路由层要照这个 id 广播（被驳回的是**他**那条申请）。
        result["employee_id"] = int(row["employee_id"])
        return result

    # ── 换班（票 09） ───────────────────────────────────────────────────
    #
    # 换班比请假多一道门：**先过对方**。员工指一个同事和一天（`kind=swap`，
    # `peer_employee_id` 是 0009 加的那一列），落 `pending_peer`；对方在手机上同意之后
    # 才变成 `pending_manager` 进店长待办 —— 在那之前店长看不到它。店长批了，两个人
    # 那天的班对调（`_approve_swap`），对方拒绝或申请人撤回则一个字不写。

    @_needs_migration
    async def submit_swap(
        self,
        employee_id: int,
        peer_employee_id: int,
        business_date: str,
        note: Optional[str] = None,
    ) -> dict:
        """请一个同事跟自己换某一天的班（票 09 的第一步）。

        落 `pending_peer`：对方同意才轮到店长。申请本身**不碰排班** —— 批下来才动，
        所以对方拒绝、或申请人自己撤回，其他人的排班一个字都不变（验收项）。

        换的是「那天两个人各自的样子」：对方那天本来就休（或还没排到，新人没配规则），
        批了就是他接走你的班、你那天空出来 —— 这也是一种换班。但**提的人那天得真有班次**：
        那天休 / 那天批了假 / 还没排到都只有一行（或没有行），手上没有班就没什么可换的
        （`nothing_to_swap`）。以前只判「有没有一行」，请假那天因此也能拿去跟人换。

        **今天与窗口末日只读一次**（同 `set_override`、`submit_leave`）：跨 06:00 的
        请求里，「那天过去了没有」与「那天还在不在窗口里」用同一个营业日判。
        """
        employee_id = int(employee_id)
        peer_id = int(peer_employee_id or 0)
        if peer_id <= 0:
            # 字段整个没给：跟「这个人不存在」分开说（跟 `missing_swap_day` 同一条口径）。
            raise SchedulingError("missing_peer", "missing_peer")
        if peer_id == employee_id:
            raise SchedulingError("swap_with_self", "swap_with_self")
        roster = await EmployeeAccounts(self._write_lock_owner).list_roster()
        people = {employee["id"]: employee for employee in roster}
        if employee_id not in people:
            raise SchedulingError("unknown_employee", "unknown_employee")
        peer = people.get(peer_id)
        if peer is None:
            raise SchedulingError("unknown_peer", "unknown_peer")
        if peer["resigned"] or not peer["approved"]:
            # 停用 / 还没批准的人不进排班名单，也不该被拉来换班：跟「找不到这个人」
            # 是两件事（一个是名字错了，一个是人现在不在），分两句说。
            raise SchedulingError("peer_unavailable", "peer_unavailable")
        if not str(business_date or "").strip():
            # 字段整个没给：跟「格式不对」分开说（跟请假 `missing_day` 同一条口径）。
            raise SchedulingError("missing_swap_day", "missing_swap_day")
        day = _require_business_date(business_date)
        now = self._now_dt()
        today = current_workbench_date(now)
        last_day = self._window_from(today)[1]
        if day < today:
            raise SchedulingError("past_swap", "past_swap")
        if day > last_day:
            # 第二参填进那句提示的 `{}`：报真实的窗口末日（跟票 07、请假同一条口径）。
            raise SchedulingError("beyond_swap", last_day)
        # 22:00 之后不能再提次日（2026-10-29 用户裁定）。换班只有一天，`days=1`
        # 就只走那条截止；提前量那一条是请假独有的（换班本来就是临时的事）。
        self._check_apply_window(day, day, today, now, days=1)
        # 先把自己那天铺出来再问「有没有班」：规则配了但还没展开时不能误判成「没班」。
        await self.expand(employee_id)
        # 换的是「班」，不是「那天有没有一行」：那天休 / 那天请假 / 还没排到都只有一行
        # （或没有行），没有可以拿去跟人对调的东西。只判「有没有行」会把请假那天也放进来
        # —— 批准之后对方从「请假」变成来上这个申请人的班，而他自己的假被 `kind=swap`
        # 盖掉。判据是 `shift_id` 非空（跟 `_leave_ids` 认「请假」的同一个形状）。
        scheduled, mine_shift = await self._day_shift(employee_id, day)
        if not scheduled or mine_shift is None:
            raise SchedulingError("nothing_to_swap", "nothing_to_swap")
        for row in await self._request_rows(employee_id=employee_id):
            if row["status"] not in (STATUS_PENDING_PEER, STATUS_PENDING_MANAGER):
                # 已经落定的（批了 / 拒了 / 撤了）不挡：同一对同一天可以过些日子再换一次。
                continue
            peer_of_row = row.get("peer_employee_id")
            if (
                peer_of_row is not None
                and int(peer_of_row) == peer_id
                and row["start_date"] == day
            ):
                # 同一件事问两遍：对方手机上会出现两条一样的请求。对方同意之后
                # （`pending_manager`）也算挂着的 —— 那条还在店长待办里，再提一条会让
                # 店长看到两张同一天的卡，两条都批等于又换了回去，而两次回执都写「对调了」。
                # 挡在这里，而不是靠前端按钮灰着（手写的调用方也该读到一句话）。
                raise SchedulingError("already_asked", "already_asked")
        # 2026-10-29 用户裁定：**对方那天已经跟别人挂着一条换班**（还没批）时，这次不成立。
        # 跟 `already_asked` 是同一条道理的另一种形状 —— 一个人一天只该被卷进一件换班的事：
        # A⇄B 还挂着，C 再来问 A（或 B），批下来 A 那天就要同时对上两边的班，店长那边也会
        # 收到两张交叉的卡。措辞带上「是谁跟谁、到哪一步了」，前端拿它弹一句说清状况。
        busy = await self._pending_swap_of(peer_id, day)
        if busy is not None:
            raise SchedulingError("peer_in_swap", _swap_couple_text(busy, people))
        try:
            return await self._insert_request(
                employee_id,
                KIND_SWAP,
                day,
                day,
                _clean_note(note),
                STATUS_PENDING_PEER,
                peer_id,
            )
        except asyncpg.UniqueViolationError:
            # 上面那次扫描在写锁外（`submit_swap` 只有 `@_needs_migration`），挡不住
            # 同一瞬间进来的第二条；0009 的局部唯一索引是最后一道闸，落库失败就说人话。
            raise SchedulingError("already_asked", "already_asked") from None

    @_needs_migration
    async def answer_swap(self, employee_id: int, request_id: int, agree: bool) -> dict:
        """对方回一句「同意」或「拒绝」（票 09）。

        同意 → 这条申请进店长待办（`pending_manager`）；拒绝 → 直接结束（`rejected`），
        **店长那边从头到尾看不到它**，排班也一个字不改。

        不是问我的（`peer_employee_id` 不是我）一律当作「不存在」：跟撤回同一条口径，
        不告诉调用方「这条在，但不是问你的」。
        """
        me = int(employee_id)
        row = await self._request_by_id(request_id)
        if row is None or row.get("peer_employee_id") is None or int(row["peer_employee_id"]) != me:
            raise SchedulingError("unknown_request", "unknown_request")
        if row["status"] != STATUS_PENDING_PEER:
            # 已经回过的、被撤回的、批完的都不再动：状态机只往前走。
            raise SchedulingError("request_not_pending", "request_not_pending")
        result = await self._decide(
            int(row["id"]),
            STATUS_PENDING_MANAGER if agree else STATUS_REJECTED,
            (STATUS_PENDING_PEER,),
        )
        # 多带一个「这条是谁提的」：路由层照它广播 —— 对方点头/摇头之后，**申请人**得知道
        #（他那边没有别的途径看到）。加字段不改既有形状，调用方按需取。
        result["employee_id"] = int(row["employee_id"])
        return result

    async def colleagues(self, employee_id: int) -> list[dict]:
        """员工能找谁换班：名单里**在上班**的其他人（停用 / 还没批准的不给选）。

        只给 `id` / `name` / `job_title`：员工端要的是「选一个人」，不是一份花名册 ——
        手机号、排班权限这些不该跟着这扇门出去。顺序就是名单顺序（前端要排自己排）。
        """
        me = int(employee_id)
        roster = await EmployeeAccounts(self._write_lock_owner).list_roster()
        return [
            {
                "id": employee["id"],
                "name": employee["name"] or employee["phone"],
                "job_title": employee["job_title"],
            }
            for employee in roster
            if employee["id"] != me and employee["approved"] and not employee["resigned"]
        ]

    async def _incoming_swaps(self, employee_id: int) -> list[dict]:
        """等我回应的换班（票 09）：`status='pending_peer'` 且那一列是我。

        每条带两个人那天的班（`their_*` 是申请人、`my_*` 是我），好让对方在手机上直接
        看清楚「他那天白班、我那天夜班，同意就是对调」。先来先回（旧的在前）。

        `scheduled=False` 是「那天还没排到」（新人没配规则），跟「本来就休」分开说 ——
        两者批下来都会真的对调，但说给员工听的话不一样。

        0009 没应用时返回空：换班那时根本提不出来（写那条路会明说去应用 0009），
        请假照常能用（票 08 那几条读路径不碰这一列）。
        """
        me = int(employee_id)
        try:
            rows = await self._request_rows(
                peer_id=me, status=STATUS_PENDING_PEER, oldest_first=True
            )
        except asyncpg.UndefinedColumnError as exc:
            await self._rollback_quietly()
            logger.warning("换班列读不了（0009 未应用？），「等我回应」按空处理: %s", exc)
            return []
        names = await self._name_index()
        shift_names = {shift["id"]: shift["name"] for shift in await self.list_shifts()}
        cards = []
        for row in rows:
            day = row["start_date"]
            their_scheduled, their_shift = await self._day_shift(int(row["employee_id"]), day)
            my_scheduled, my_shift = await self._day_shift(me, day)

            def label(shift_id: Optional[int]) -> Optional[str]:
                return None if shift_id is None else shift_names.get(shift_id)

            cards.append({
                "id": int(row["id"]),
                "employee_id": int(row["employee_id"]),
                "employee_name": names.get(int(row["employee_id"]), ""),
                "kind": row["kind"],
                "business_date": day,
                "note": row["note"],
                "created_at": row["created_at"],
                "their_scheduled": their_scheduled,
                "their_shift_id": their_shift,
                "their_shift_name": label(their_shift),
                "my_scheduled": my_scheduled,
                "my_shift_id": my_shift,
                "my_shift_name": label(my_shift),
            })
        return cards

    @serialized_write
    async def _approve(self, row: dict) -> dict:
        """批准一条申请：写覆盖行 + 结果行，最后把申请置成已批准。一次提交。

        两种申请在这一层分岔：请假在区间里每一天写成「没有班次」（`kind=leave`），
        换班只动那一天、两个人的班对调（`kind=swap`，见 `_approve_swap`）。

        一次提交是有意的：批一半（写了三天、申请还是待批）比不批更糟。

        **动手写排班之前先把这条申请占住**（`_claim_request` 的状态谓词在锁里）：店长
        那次「读出来是待批」的判断在锁外，双击 / 两个标签页 / 网关重试会双双通过它 ——
        到这儿只有第一个改得动状态，第二个连一天的排班都不写。换班那条尤其要看住：
        `_approve_swap` 是拿**当下**两个人的班去对调，执行两次就是把班换回去，
        而两次都回「批了」。

        **今天与窗口末日在分岔之前读一次**（快照写法）：两类申请、以及换班那条里的
        「那天算不算过去 / 还在不在窗口里」，都用同一个营业日判 —— 请求正好跨过 06:00
        时不再出现「一次批准前半段算今天、后半段算明天」。
        """
        today = self.today()
        last_day = self._window_from(today)[1]
        if row["kind"] == KIND_SWAP:
            return await self._approve_swap(row, today, last_day)
        employee_id = int(row["employee_id"])
        stamp = self._now_iso()
        await self._claim_request(int(row["id"]), STATUS_APPROVED, (STATUS_PENDING_MANAGER,), stamp)
        applied: list[str] = []
        skipped: list[str] = []
        for day in _each_day_between(row["start_date"], row["end_date"]):
            if day < today or day > last_day:
                skipped.append(day)
                continue
            await self._write_override_row(employee_id, day, None, None, KIND_LEAVE, stamp)
            await self._write_day_row(employee_id, day, None, None, SOURCE_OVERRIDE, stamp)
            applied.append(day)
        await self._conn.commit()
        return {
            "id": int(row["id"]),
            "employee_id": employee_id,
            "status": STATUS_APPROVED,
            "decided_at": stamp,
            "applied_days": applied,
            "skipped_days": skipped,
        }

    @staticmethod
    def _peer_of_swap(row: dict) -> int:
        """换班申请里的「对方」（票 09）。

        0009 没应用时这一列读不出来（读路径按「没有换班」处理，见 `_select_request_rows`），
        可**列被撤掉之前**已经排上队的换班还躺在表里：这时候卡片渲染不出来、也批不了。
        老实报一条点名迁移文件的 503 —— 别拿 None 去查库：那样待办上会印出一句
        「对方那天休」的假话，点批准还会在 `int(None)` 上抛成清不掉的 500。
        """
        peer_id = row.get("peer_employee_id")
        if peer_id is None:
            raise SchedulingError("not_migrated", 'column "peer_employee_id" does not exist')
        return int(peer_id)

    async def _approve_swap(self, row: dict, today: str, last_day: str) -> dict:
        """把两个人那天的班对调（票 09 的验收项）。调用方持写锁、负责提交。

        写的是**覆盖行**（`kind=swap`）+ 结果行（`source=override`），两个人都写 ——
        跟店长手改、批准请假走的是同一条路，于是那天之后规则再重铺也改不动它。

        工作区按各自**新班次**的固定区取（验收：区跟着班次走）；新班次是「休 / 没排到」
        时区也留空。当天已经过去 / 超出展开窗口的那一天只记「没动」：批得晚了就是晚了
        （跟请假那一支同一条：批准只写今天与以后）。

        `today` / `last_day` 是 `_approve` 在分岔前取的那一份快照（见那里）：两处各读
        一次钟的话，跨 06:00 的请求会按两个不同的营业日判这一天算不算过去。

        动手之前先 `_claim_request` 占住这条申请：对调是拿**当下**的班算的，执行两次
        就是把班换回去 —— 两条都回「批了」，员工那边看着却白换了。

        **任何一方那天有已批准的假就整笔拒绝**（`leave_approved`）：写下去会用
        `kind=swap` 把那条 `kind=leave` 的覆盖记录盖掉，对方从「请假」变成来上申请人的班，
        而申请表里那条假仍是 `approved` —— 审批记录与排班事实互相矛盾。判据与批量清除、
        单格撤销同一处（`_leave_ids`）、同一个词。拒绝时 `_claim_request` 那次 UPDATE 被
        `serialized_write` 回滚，申请仍是「等店长批」，店长可以驳回它。
        """
        employee_id = int(row["employee_id"])
        peer_id = self._peer_of_swap(row)
        day = row["start_date"]
        stamp = self._now_iso()
        await self._claim_request(int(row["id"]), STATUS_APPROVED, (STATUS_PENDING_MANAGER,), stamp)
        applied: list[str] = []
        skipped: list[str] = []
        if day < today or day > last_day:
            # 过去 / 窗口外那天本来就不写：不在这里拦请假（拦了也没东西可盖）。
            skipped.append(day)
        elif await self._leave_ids([employee_id, peer_id], day):
            raise SchedulingError("leave_approved", "leave_approved")
        else:
            _, mine_now = await self._day_shift(employee_id, day)
            _, theirs_now = await self._day_shift(peer_id, day)
            mine_zone = await self._zone_for(employee_id, theirs_now)
            theirs_zone = await self._zone_for(peer_id, mine_now)
            await self._write_override_row(
                employee_id, day, theirs_now, mine_zone, KIND_SWAP, stamp
            )
            await self._write_day_row(
                employee_id, day, theirs_now, mine_zone, SOURCE_OVERRIDE, stamp
            )
            await self._write_override_row(peer_id, day, mine_now, theirs_zone, KIND_SWAP, stamp)
            await self._write_day_row(peer_id, day, mine_now, theirs_zone, SOURCE_OVERRIDE, stamp)
            applied.append(day)
        await self._conn.commit()
        return {
            "id": int(row["id"]),
            "employee_id": employee_id,
            "peer_employee_id": peer_id,
            "status": STATUS_APPROVED,
            "decided_at": stamp,
            "applied_days": applied,
            "skipped_days": skipped,
        }

    async def _zone_for(self, employee_id: int, shift_id: Optional[int]) -> Optional[int]:
        """这个人在这个班次上的固定区（票 03 配的那张表）；班次是空的就没有区。"""
        if shift_id is None:
            return None
        return (await self._zone_defaults_for(int(employee_id))).get(int(shift_id))

    async def _day_shift(self, employee_id: int, day: str) -> tuple[bool, Optional[int]]:
        """某人某天现在的结果行：``(那天排没排到, 班次 id)``。

        没有那一行 = 那天还没排到（新人没配规则，或窗口还没铺到），跟
        「有行、班次为空」= 那天休 是两件事 —— 换班卡片上要说给员工听的不一样。
        """
        cur = await self._conn.execute(
            """SELECT shift_id FROM staff_assignments
               WHERE employee_id = ? AND business_date = ?""",
            (int(employee_id), day),
        )
        found = await cur.fetchone()
        if found is None:
            return False, None
        shift_id = dict(found)["shift_id"]
        return True, (None if shift_id is None else int(shift_id))

    async def _swap_card(
        self, row: dict, day_roster, names: dict, shift_names: dict, today: str
    ) -> Optional[dict]:
        """店长待办里换班那张卡：两个人那天的班摊开，批了就是对调。

        `days` 跟请假卡同形（就一天）：`scheduled` / `current_shift_*` 是**申请人**的，
        `peer_*` 是**对方**的。`scheduled=False` 是那天还没排到（新人没配规则）——
        批下来照样对调：他接走申请人的班。没有「剩几个人」这一项：对调走两个人、
        来两个人，哪个班次的人数都不变。

        摆不了桌的那一行返回 `None`（见下面那段）：一张卡摆不出来，不该让整页待办
        跟着倒下 —— 同一队列里还压着别人的请假。
        """
        employee_id = int(row["employee_id"])
        if self._requests_have_peer and row.get("peer_employee_id") is None:
            # 列读得出来，这一行的对方却是空的：0009 被撤掉又加回来留下的死数据
            # （`DROP COLUMN` 连值一起丢），对方是谁已经无从考证，这行永远批不了。
            # 跳过它、留一条日志，而不是把整页待办 503 掉 —— 它已经不是「等着批的活」，
            # 是修不回来的残骸。（列真的不在时 `_requests_have_peer` 是 False，
            # 那种情况仍旧报 `not_migrated`：照做能修好，别混为一谈。）
            logger.warning(
                "换班 #%s 没有对方（0009 被撤过？），这张卡跳过不摆", row.get("id")
            )
            return None
        peer_id = self._peer_of_swap(row)
        day = row["start_date"]
        people = await day_roster(day)
        mine = people.get(employee_id)
        theirs = people.get(peer_id)

        def label(shift_id: Optional[int]) -> Optional[str]:
            return None if shift_id is None else shift_names.get(shift_id)

        return {
            "id": int(row["id"]),
            "employee_id": employee_id,
            "employee_name": names.get(employee_id, ""),
            "kind": row["kind"],
            "peer_employee_id": peer_id,
            "peer_name": names.get(peer_id, ""),
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "note": row["note"],
            "created_at": row["created_at"],
            "days": [{
                "business_date": day,
                "past": day < today,
                "scheduled": employee_id in people,
                "current_shift_id": mine,
                "current_shift_name": label(mine),
                "peer_scheduled": peer_id in people,
                "peer_shift_id": theirs,
                "peer_shift_name": label(theirs),
            }],
        }

    async def _claim_request(
        self, request_id: int, status: str, expect: tuple[str, ...], stamp: str
    ) -> None:
        """把申请从 `expect` 里的某个状态改成 `status`；没抢到就报「已经处理过了」。

        **状态谓词落在写锁里**：调用方那次「读出来是待批 / 是等我回应」的判断在锁外，
        双击、两个标签页、网关重试会双双通过那道判断。到这儿再比一次状态，只有第一个
        改得动这一行 —— 后面那个连它要写的排班都不写。

        不提交：调用方把这条 UPDATE 与排班写入放在同一个事务里，要么都成要么都不成。

        判「抢没抢到」用 `rowcount` 而不是 `... RETURNING id`：方言层对 UPDATE 只解析
        asyncpg 的 command tag（见 `db_core/backend/pg.py` 的 `execute`），RETURNING
        的行取不出来 —— 写 `RETURNING` 会让这里永远读到「没抢到」。
        """
        marks = ", ".join("?" for _ in expect)
        cur = await self._conn.execute(
            f"""UPDATE scheduling_requests
                SET status = ?, decided_at = ?, updated_at = ?
                WHERE id = ? AND status IN ({marks})""",
            (status, stamp, stamp, int(request_id), *expect),
        )
        if cur.rowcount != 1:
            # 0 = 状态已经被人改了；-1 = command tag 没解析出来（`rowcount_from_status`），
            # 两种都按「没抢到」办 —— 宁可让店长刷新一次，也不能重复写一遍排班。
            raise SchedulingError("request_not_pending", "request_not_pending")

    @serialized_write
    async def _decide(self, request_id: int, status: str, expect: tuple[str, ...]) -> dict:
        """把申请从 `expect` 置成 `status` 并记下时间（驳回 / 撤回 / 对方回应共用）。

        排班一个字不改；状态谓词在锁里，见 `_claim_request`。
        """
        stamp = self._now_iso()
        await self._claim_request(request_id, status, expect, stamp)
        await self._conn.commit()
        return {"id": int(request_id), "status": status, "decided_at": stamp}

    async def _select_request_rows(
        self, clause: str, params: tuple, order: str = ""
    ) -> list[dict]:
        """读申请行（语句由调用方拼：筛选与排序各不一样，列名只此一处）。

        0009 没应用时**退一步**：按没有换班那一列读，请假那几条照常能用 —— 换班那时
        根本提不出来（写的那条路会明说去应用 0009），「等我回应的换班」当然是空的。
        降级只发生一次（`_requests_have_peer` 记住）；失败的事务先回滚，否则这条连接
        后面的查询会全跟着报 `current transaction is aborted`（同 `_needs_migration`）。
        """
        tail = f" ORDER BY {order}" if order else ""
        columns = _REQUEST_COLUMNS if self._requests_have_peer else _REQUEST_COLUMNS_NO_PEER
        try:
            cur = await self._conn.execute(
                f"SELECT {columns} FROM scheduling_requests{clause}{tail}", params
            )
        except asyncpg.UndefinedColumnError as exc:
            await self._rollback_quietly()
            if not self._requests_have_peer:
                raise
            self._requests_have_peer = False
            logger.warning("换班列读不了（0009 未应用？），申请按没有换班处理: %s", exc)
            cur = await self._conn.execute(
                f"SELECT {_REQUEST_COLUMNS_NO_PEER} FROM scheduling_requests{clause}{tail}",
                params,
            )
        rows = [dict(row) for row in await cur.fetchall()]
        for row in rows:
            row.setdefault("peer_employee_id", None)
        return rows

    async def _request_rows(
        self,
        employee_id: Optional[int] = None,
        status: Optional[str] = None,
        oldest_first: bool = False,
        peer_id: Optional[int] = None,
    ) -> list[dict]:
        """读申请（可按人、按状态、按「对方是谁」筛）。排序由调用方选。"""
        where: list[str] = []
        params: list[Any] = []
        if employee_id is not None:
            where.append("employee_id = ?")
            params.append(int(employee_id))
        if peer_id is not None:
            # 「等我回应的换班」：这一列（0009）按 `peer_employee_id` 领头读，
            # 索引 `idx_scheduling_requests_peer` 就是给这条路径建的。
            where.append("peer_employee_id = ?")
            params.append(int(peer_id))
        if status is not None:
            where.append("status = ?")
            params.append(status)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        order = "created_at ASC, id ASC" if oldest_first else "created_at DESC, id DESC"
        return await self._select_request_rows(clause, tuple(params), order)

    async def _pending_swap_of(self, employee_id: int, day: str) -> Optional[dict]:
        """这天这个**人**挂着的那条换班（还没落定）：他提的、或别人问他的，都算。

        两扇门各读一次：`employee_id` 那一列是他提的，`peer_employee_id` 那一列是问他的
        —— 两条路各有自己的索引（0009 的 `idx_scheduling_requests_peer` 就是给后一条建的），
        拼成一次 OR 查询会同时用不上。返回第一条（新的在前，就取最新的那条说给员工听）。
        """
        target = int(employee_id)
        for rows in (
            await self._request_rows(employee_id=target),
            await self._request_rows(peer_id=target),
        ):
            for row in rows:
                if row["kind"] != KIND_SWAP:
                    continue
                if row["status"] not in (STATUS_PENDING_PEER, STATUS_PENDING_MANAGER):
                    # 批了 / 拒了 / 撤了都算落定：那天的班要么已经换过去、要么没动过，
                    # 别人再来找这个人换是另一件事。
                    continue
                if row["start_date"] != day:
                    continue
                return row
        return None

    async def _request_by_id(self, request_id: int) -> Optional[dict]:
        rows = await self._select_request_rows(" WHERE id = ?", (int(request_id),))
        return rows[0] if rows else None

    @serialized_write
    async def _insert_request(
        self,
        employee_id: int,
        kind: str,
        first: str,
        last: str,
        note: Optional[str],
        status: str,
        peer_employee_id: Optional[int] = None,
    ) -> dict:
        stamp = self._now_iso()
        with_peer = (
            "(employee_id, kind, start_date, end_date, status, note, created_at, updated_at,"
            " peer_employee_id)"
        )
        without_peer = (
            "(employee_id, kind, start_date, end_date, status, note, created_at, updated_at)"
        )
        base = (employee_id, kind, first, last, status, note, stamp, stamp)
        cur = None
        if self._requests_have_peer:
            try:
                cur = await self._conn.execute(
                    f"INSERT INTO scheduling_requests {with_peer}"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                    (*base, peer_employee_id),
                )
            except asyncpg.UndefinedColumnError:
                # 0009 还没应用：请假不靠这一列，照常落库（退回没有它的写法，并记住）；
                # 换班非得记「跟谁换」，抛给调用方的 `_needs_migration` 变成 503，
                # 点名 `0009_scheduling_swap.sql`。
                if peer_employee_id is not None:
                    raise
                await self._rollback_quietly()
                self._requests_have_peer = False
                logger.warning("换班列写不了（0009 未应用？），请假按没有换班落库")
        if cur is None:
            if peer_employee_id is not None:
                raise SchedulingError("not_migrated", 'column "peer_employee_id" does not exist')
            cur = await self._conn.execute(
                f"INSERT INTO scheduling_requests {without_peer}"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                base,
            )
        created = await cur.fetchone()
        await self._conn.commit()
        return {
            "id": int(dict(created)["id"]),
            "employee_id": employee_id,
            "kind": kind,
            "start_date": first,
            "end_date": last,
            "status": status,
            "note": note,
            "peer_employee_id": peer_employee_id,
            "decided_at": None,
            "created_at": stamp,
        }

    async def _leave_ids(self, employee_ids: Any, day: str) -> set[int]:
        """这天这些人里，哪些是**请假**（覆盖记录的 `kind` 是 `leave`）而不是本来就休。

        按 `employee_id` 领头查 —— 0007 那条索引 `idx_scheduling_overrides_employee`
        的前导列就是它（票 07 复核 F7 的结论），别写成先按 `business_date` 筛的形态。

        `scheduling_overrides` 还没建（0007 没应用）时返回空集，不把整个当日分工变成
        503：那张表不在就**不可能**有请假标记，那天所有人本来就只是「休」（写请假那条
        路仍然会明说去应用迁移 —— 见 `migrations/pg/README.md`）。
        """
        ids = [int(item) for item in employee_ids]
        if not ids:
            return set()
        marks = ", ".join("?" for _ in ids)
        try:
            cur = await self._conn.execute(
                f"""SELECT employee_id FROM scheduling_overrides
                    WHERE employee_id IN ({marks}) AND business_date = ? AND kind = ?""",
                (*ids, day, KIND_LEAVE),
            )
            return {int(dict(row)["employee_id"]) for row in await cur.fetchall()}
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            logger.warning("请假标记读不了（0007 未应用？），这天按「休」显示: %s", exc)
            return set()

    async def _leave_days(self, employee_id: int, first: str, last: str) -> set[str]:
        """某人一段日期里批了请假的那几天（两端都算）。缺 0007 时同 `_leave_ids` 返回空集。"""
        try:
            cur = await self._conn.execute(
                """SELECT business_date FROM scheduling_overrides
                   WHERE employee_id = ? AND business_date >= ? AND business_date <= ?
                     AND kind = ?""",
                (int(employee_id), first, last, KIND_LEAVE),
            )
            return {dict(row)["business_date"] for row in await cur.fetchall()}
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            logger.warning("请假标记读不了（0007 未应用？），按「休」显示: %s", exc)
            return set()
