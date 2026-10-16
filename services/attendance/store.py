#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当月考勤表：一人一张卡，逐日三态 + 卡片上那几列（`GLOSSARY.md` 的「考勤表」）。

**这是投影，不是打卡系统**（`docs/adr/0112`）：不记上下班时刻、不判迟到早退、不算
实际工时。三个输入是排班结果（`staff_assignments`）、净时长（`overtime_entries` 里
**已批准**的那几笔，正负相抵）与花名册（公共层 `services.identity`）。这一层**只读**，
而且只算当月 —— 不提供历史月份的参数，选不到过去。

口径（票 01；假期那一套是票 03 的事）：

* 一天三态：排了班 = `work`、有排班行但班次为空 = `rest`、**压根没有排班行** =
  `todo`（轮转规则没铺到，「待补」）。今天之后的日期 `state` 是 `None` —— 那是「还没
  到」，不是「待补」。
* 名单 = 在职员工（**已批准且未停用**，同排班周表的口径）+ 当月有排班或加班记录的
  人（含当月离职的）。没有当月记录的**历史**离职人员不进表。
 * 卡片列：职位 / 底薪 / 年限（工龄奖）/ 是否新入职 / 当月净时长（**只算已批准的单**）。
   待审批的那几笔另给 `pending_hours` / `pending_count` —— 只标色，不计数（故事 14）。
   入职日期为空的人 `new_hire` 是 `None`（留空），不是 `False`（「算不出来」与「不是
   新来的」是两件事）。
 * 票 06 起格子上多一个 `shift_id`、卡片上多一份 `shifts`（**启用中的**班次）：逐日改格
   要能标出「现在是哪个班」并挑另一个。
 * 票 07 起格子再多一个 `half_rest`（**半天休**，ADR 0114）：那天仍然只挂本来那个班次、
   仍然只算「上班」，只是上半天。这一层是**唯一**设置它的地方（`set_half_rest`）——
   排班月历 / 周表 / 员工端只读地显示它。除此之外这一层仍**只读**：逐日改格的写路径
   在排班那一边（票 06）。

「今天」取**营业日**（06:00 切，同排班）：考勤表的格子就是
`staff_assignments.business_date`，两边的「哪一天」必须是同一个词。

假期台账（票 03，口径见 `GLOSSARY.md` 的「假期」那几节与 `docs/adr/0113`）：

* **实际出勤天数**：本月**已过**的营业日里「排了班、又没标休」的天数；**标了「休半天」
  的那天算 0.5 天**（票 07，ADR 0114）。今天之后排的班一天都不算 —— 那些日子还没发生。
* **月例休** = `min(4, floor(实际出勤 ÷ 6))`；**法定节假日补休** = 当月每个法定节假日
  那天排了班 +1（休不给，没标日历就没有 —— 日历是唯一依据）；**生日假** = 身份证号
  派生的生日月份落在当月 +1（没填身份证号算不出来，不给）；**年假**只在工龄奖调整
  **发生在当月**时计入（管理层 5 天 / 其他 3 天）。
* **上月存假**读上一月**已封账**那一行的结余；上一月还没封账（上线当月就是这一种）时
  退回读本月这一行的**期初余额** —— 那是人工录入一次的「上月末余额」。
* **可用天数池** = 实际出勤 + 月例休 + 补休 + 生日假 + 年假 + 上月存假；
  **本月出勤天数** = `min(池, 本月自然天数)`；**存假** = `池 − 本月自然天数`
  （可为负 = 欠班、可带 .5）。**加班与补钟不参与这一套**（净时长单独结算）。

当月的一切都是**暂算值**：这一层只算不落库，封账（写结余）是票 04 的事。

票 04 加的是**封账**（`GLOSSARY.md` 的「封账」、`docs/adr/0113`）：超级管理员在考勤表页
点一次，就把**上一个自然月**那一行的 `closing_balance`（存假结余）与 `sealed_at` 写进
月度考勤台账。写下去之后，当月卡片上的「上月存假」读的就是那一行的结余 —— 之后再改
上月的排班也不会让它漂（那正是这张表存在的理由）。**只有已经过完的月份能封**：当月与
未来一律拒；已经封过的月再封一次也拒，一个字都不改已写下的那一行。这是本服务**唯一
的写口**（逐日改格那扇门在排班那一边，只借道 `api/attendance.py`）。
"""

from __future__ import annotations

import asyncio
import functools
import logging
import math
import re
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

import asyncpg

from db_core.utils import CHINA_TZ
from services.business_day import current_business_date
from services.holiday_calendar import HolidayCalendar, HolidayError
from services.identity import EmployeeAccounts, serialized_write
from services.identity.profile import (
    birthday_in_year,
    seniority_due_month,
    seniority_should_be,
)
from services.overtime import STATUS_APPROVED, STATUS_PENDING

logger = logging.getLogger(__name__)

__all__ = [
    "ANNUAL_LEAVE_MANAGEMENT",
    "ANNUAL_LEAVE_STAFF",
    "MONTHLY_REST_MAX",
    "MONTHLY_REST_STEP",
    "STATE_REST",
    "STATE_TODO",
    "STATE_WORK",
    "AttendanceError",
    "AttendanceStore",
]

# 一天的三态。第四态是「空」—— 今天之后的日子 `state` 为 `None`。
STATE_WORK = "work"  # 那天排了班
STATE_REST = "rest"  # 那天有排班行、班次为空（休）
STATE_TODO = "todo"  # 那天压根没有排班行（轮转规则没铺到，「待补」）

# 假期台账那几个数（票 03）。每一条都在 `GLOSSARY.md` 的「考勤与假期」里定死：
# 月例休每出勤 6 天折 1 天、上限 4 天；年假只在工龄奖调整那个月给，管理层 5 天、
# 其他员工 3 天（判据是**管理能力开关非空**，与职位名无关）。
MONTHLY_REST_STEP = 6
MONTHLY_REST_MAX = 4
ANNUAL_LEAVE_MANAGEMENT = 5
ANNUAL_LEAVE_STAFF = 3


def _needs_migration(method):
    """要读的表 / 列还没建（0005 / 0021 / 0026 / 0027 / 0028 没应用）时，换成一个能照做的错。

    照排班 `store.py` 的同一个装饰器：整页不该是一个 500 traceback，店长要看到的是
    「去 Admin『系统更新 → 数据库迁移』应用哪个脚本」。顺带把失败的事务回滚掉，否则
    这条连接后面的查询会全跟着报 `current transaction is aborted`。
    """

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        try:
            return await method(self, *args, **kwargs)
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            raise AttendanceError("not_migrated", str(exc)) from exc

    return wrapper


class AttendanceError(ValueError):
    """考勤这层的错误。``code`` 是给 API 层查文案用的稳定标识，不是给店长看的文案。"""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


def _next_month(start: date) -> date:
    """下月一号 —— 区间一律**右开**（`>= 本月一号 AND < 下月一号`），不自己数月份长度。"""
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def _is_half_rest(value: Any) -> bool:
    """`half_rest` 是 BIGINT 0/1（仓库口径的布尔，同 `staff_shifts.is_active`）。

    读出来是 `int`，写进去是 `1` / `0` —— 这一对转换只在这里与 `set_half_rest` 两处，
    别让调用方各自 `bool()` 一遍（`"0"` 这种字符串在别处会变成 `True`）。
    """
    return bool(int(value)) if value is not None else False


def _require_business_date(value: Any) -> str:
    """营业日规整成 `YYYY-MM-DD`（同排班那句 `invalid_business_date`）。"""
    text = str(value or "")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise AttendanceError("invalid_business_date", "invalid_business_date")


_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _validated_month(month: Any) -> str:
    """把「封哪个月」收敛成 `YYYY-MM`，写错就报 `invalid_month`。

    只认格式还不够：`2026-13` 过得了正则，得真解析一次（顺带拦下 `2026-00`）。
    与 `HolidayCalendar` 的同名收敛同口径 —— 月份在这套系统里只有一种写法。
    """
    text = str(month or "").strip()
    if not _MONTH_RE.match(text):
        raise AttendanceError("invalid_month", f"invalid month: {month!r}")
    try:
        date.fromisoformat(f"{text}-01")
    except ValueError as exc:
        raise AttendanceError("invalid_month", str(exc)) from exc
    return text


def _previous_month(month: str) -> str:
    """上一个自然月（`YYYY-MM`）—— 封账封的就是它。"""
    return (date.fromisoformat(f"{month}-01") - timedelta(days=1)).isoformat()[:7]


class AttendanceStore:
    """当月考勤表的读口（`sheet`）+ 封账那一处写口（`seal`）。构造要一个已打开的库连接
    （同 `SchedulingStore` 的口径）。"""

    def __init__(self, conn_or_db, now: Optional[Callable[[], datetime]] = None):
        conn = getattr(conn_or_db, "_conn", conn_or_db)
        if conn is None:
            raise RuntimeError("AttendanceStore requires an open database connection")
        self._conn = conn
        self._now = now or (lambda: datetime.now(CHINA_TZ))
        self._write_lock_owner = conn_or_db
        self._local_write_lock = None

    @property
    def _write_lock(self):
        """写口那一个动作要用的锁：跟排班 / 节假日日历共用**同一个**全局写锁
        （同 `HolidayCalendar._write_lock` 的做法）。

        拿不到 owner 上那把锁（测试里塞的是裸连接）时退回一把本实例自己的 —— 那一档
        没有别的写方，串行化仍然成立。
        """
        shared = getattr(self._write_lock_owner, "_write_lock", None)
        if shared is not None:
            return shared
        if self._local_write_lock is None:
            import asyncio

            self._local_write_lock = asyncio.Lock()
        return self._local_write_lock

    @property
    def _write_lock(self):
        """写锁：跟着 holder（`DatabaseManager`）那一把全局锁走，别自己另开一把。

        同 `HolidayCalendar` / `SchedulingStore` 的口径 —— 两个服务各持一把私有锁的话，
        隐式事务会交错。拿不到共享锁时（直接传连接）才退化成一把本实例的锁。
        """
        shared = getattr(self._write_lock_owner, "_write_lock", None)
        if shared is not None:
            return shared
        lock = getattr(self, "_local_write_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._local_write_lock = lock
        return lock

    def _now_dt(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TZ)
        return value

    def today(self) -> str:
        """当前营业日（06:00 切，北京时）—— 与 `staff_assignments.business_date` 同一个词。"""
        return current_business_date(self._now_dt())

    def _now_iso(self) -> str:
        """写库的时间戳（ISO 字符串，带 +08:00）—— 与排班那边 `_now_iso()` 同一条口径。"""
        return self._now_dt().isoformat()

    async def _rollback_quietly(self) -> None:
        try:
            await self._conn.rollback()
        except Exception:  # pragma: no cover - 回滚失败不该盖住原始错误
            logger.debug("回滚失败（忽略）", exc_info=True)

    async def _assignment_rows(
        self, first: str, next_month: str
    ) -> tuple[dict[int, dict[str, Any]], dict[int, set[str]]]:
        """当月每天每人的排班行，两份索引：

        * ``{员工号: {营业日: 班次 id 或 None}}`` —— 三态（上班 / 休 / 待补）与班次号；
        * ``{员工号: {标了「休半天」的营业日}}`` —— 只装**那天真有班次**的那些（票 07）。

        **有行**就说明那天被铺过（`None` = 休）；没有这一格就是「待补」，所以这里不
        过滤班次为空的行 —— 过滤掉的话「休」会与「没铺到」塌成同一件事。

        「休半天」在 `shift_id IS NULL`（那天休）的行上没有意义：`休 + 休半天` 是自相
        矛盾的，写路径（排班 `set_override` 的「改成休」）也会把那种行上的标志清成 0。
        这里再挡一次，是为了不让手工 SQL / 历史数据里那种行在页面上变成「休半天」。
        """
        cur = await self._conn.execute(
            """SELECT employee_id, business_date, shift_id, half_rest FROM staff_assignments
               WHERE business_date >= ? AND business_date < ?""",
            (first, next_month),
        )
        rows: dict[int, dict[str, Any]] = {}
        half_rests: dict[int, set[str]] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            employee_id = int(mapping["employee_id"])
            rows.setdefault(employee_id, {})[mapping["business_date"]] = (
                None if mapping["shift_id"] is None else int(mapping["shift_id"])
            )
            if mapping["shift_id"] is not None and _is_half_rest(mapping["half_rest"]):
                half_rests.setdefault(employee_id, set()).add(mapping["business_date"])
        return rows, half_rests

    async def _assignment_row(self, employee_id: int, day: str) -> Optional[dict]:
        """某人某天那一行（没有则 ``None``）—— 逐日写口要判「那天到底有没有班次」。"""
        cur = await self._conn.execute(
            """SELECT shift_id, half_rest FROM staff_assignments
               WHERE employee_id = ? AND business_date = ?""",
            (int(employee_id), day),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)

    async def _month_entries(self, first: str, next_month: str) -> tuple[dict[int, int], set[int]]:
        """当月加班登记：``(已批准的半小时数合计, 这个月有登记的人)``。

        两个答案一次查出来，但**判据不一样**：

        * 净时长只算**已批准**的那几笔（票 01 验收 7）—— 待审批 / 已驳回 / 已撤回 /
          已作废的登记一分都不算（「有单子等着批」是待办队列里的事，不在这个数里）；
        * 「这个人当月有没有加班记录」算**全部状态**（票 01 验收 5）—— 一笔还没批的
          加班也是「他那个月上过班」的证据，离职的人靠它进表。

        归月按 `entry_date` 的自然月，与什么时候审批无关（8/31 的单 9 月才批，算 8 月）。
        """
        cur = await self._conn.execute(
            """SELECT employee_id, half_hours, status FROM overtime_entries
               WHERE entry_date >= ? AND entry_date < ?""",
            (first, next_month),
        )
        totals: dict[int, int] = {}
        recorded: set[int] = set()
        for row in await cur.fetchall():
            mapping = dict(row)
            employee_id = int(mapping["employee_id"])
            recorded.add(employee_id)
            if mapping["status"] == STATUS_APPROVED:
                totals[employee_id] = totals.get(employee_id, 0) + int(mapping["half_hours"])
        return totals, recorded

    async def _pending_entries(
        self, first: str, next_month: str
    ) -> dict[int, dict[str, int]]:
        """当月**还没批**的加班 / 补钟：``{员工号: {"half_hours": 合计, "count": 笔数}}``。

        「还没批」= `STATUS_PENDING` 那一种（`services/overtime/ledger.py` 的状态机）。
        它与上面那份净时长是两笔账：这一份只用来在卡片上**标色并说清还有几小时等着批**
        （故事 14），**一分都不进净时长、也不进存假**（票 01 留下的缺口）。
        归月与净时长同一条：按 `entry_date` 的自然月，与什么时候审批无关。
        """
        cur = await self._conn.execute(
            """SELECT employee_id, half_hours FROM overtime_entries
               WHERE entry_date >= ? AND entry_date < ? AND status = ?""",
            (first, next_month, STATUS_PENDING),
        )
        pending: dict[int, dict[str, int]] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            employee_id = int(mapping["employee_id"])
            bucket = pending.setdefault(employee_id, {"half_hours": 0, "count": 0})
            bucket["half_hours"] += int(mapping["half_hours"])
            bucket["count"] += 1
        return pending

    async def _statutory_holidays(self, month: str) -> list[str]:
        """当月法定节假日（升序的日期串）。**日历是唯一依据**，代码里没有年度表。

        日历缺表（0026 没应用）时与缺排班 / 加班表一样报 `not_migrated` —— 这一页要
        读的每一张表都点得到名，店长才知道去 Admin 应用哪几个脚本。
        """
        calendar = HolidayCalendar(self._write_lock_owner, now=self._now)
        try:
            return await calendar.days_in_month(month)
        except HolidayError as exc:
            if exc.code == "not_migrated":
                await self._rollback_quietly()
                raise AttendanceError("not_migrated", str(exc)) from exc
            raise

    async def _ledger_rows(self, months: tuple[str, str]) -> dict[tuple[int, str], dict]:
        """本月与上一月的台账行：``{(员工号, 月份): 那一行}``。

        **只读**（票 03 不写这张表）：写它的是封账（票 04）与手填列（票 05）。缺表
        （0027 没应用）时照旧走 `not_migrated` 那条出口。
        """
        cur = await self._conn.execute(
            """SELECT employee_id, "month", opening_balance, closing_balance, sealed_at
               FROM attendance_ledger WHERE "month" IN (?, ?)""",
            months,
        )
        rows: dict[tuple[int, str], dict] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            rows[(int(mapping["employee_id"]), mapping["month"])] = mapping
        return rows

    async def _shifts(self) -> list[dict[str, Any]]:
        """卡片上那个下拉里能挑的班次：**启用中的**，按显示顺序（票 06）。

        挑班次的下拉与排班月历读的是同一张表、同一条「只在用的班次」口径 ——
        停用的班次摆在这里等于给店长一个点下去就 400 的选项。名字之外还带 `id`：
        改格是按 id 写的（班次可以改名，名字不是键）。
        """
        cur = await self._conn.execute(
            """SELECT id, name FROM staff_shifts WHERE is_active = 1
               ORDER BY sort_order ASC, id ASC"""
        )
        return [
            {"id": int(dict(row)["id"]), "name": dict(row)["name"]}
            for row in await cur.fetchall()
        ]

    @_needs_migration
    async def sheet(self) -> dict:
        """当月那一张考勤表：月框 + 一人一张卡。**唯一的读口**，没有月份参数。"""
        today = self.today()
        month = today[:7]
        start = date.fromisoformat(f"{month}-01")
        next_month = _next_month(start)
        # 区间右开：`[本月一号, 下月一号)` —— 不自己数月份长度（2 月与 31 天的月各一次坑）。
        first, until = start.isoformat(), next_month.isoformat()

        rows, half_rests = await self._assignment_rows(first, until)
        totals, recorded = await self._month_entries(first, until)
        # 还没批的加班 / 补钟：只标色，不进净时长、也不进存假（故事 14）。
        pending = await self._pending_entries(first, until)
        # 当月的法定节假日（补休要用）。日历缺表时与缺排班表一样报 `not_migrated`。
        holidays = await self._statutory_holidays(month)
        # 本月与上一月的台账行：算「上月存假」（已封账的结余 / 人工期初）。
        previous_month = (start - timedelta(days=1)).isoformat()[:7]
        ledger = await self._ledger_rows((previous_month, month))
        shifts = await self._shifts()
        # 当月有排班或加班记录的人：离职的人靠它进表（票 01 验收 5）。
        on_record = set(rows) | recorded
        # 员工是谁只从公共层取（同排班的路子）：职位 / 底薪 / 工龄奖 / 入职日期都在
        # 那一份投影里。时钟跟着本服务的钟走，派生字段才与「今天」同一个。
        roster = await EmployeeAccounts(self._write_lock_owner, now=self._now).list_roster()

        # 月框：当月每一天一条（`day` 是日期号，前端拿它当表头）。`is_future` 是
        # 「还没到」的判据 —— 今天之后留空，不是「待补」（票 01 验收 4）。
        days = []
        for offset in range(monthrange(start.year, start.month)[1]):
            day = start + timedelta(days=offset)
            key = day.isoformat()
            days.append(
                {
                    "day": day.day,
                    "business_date": key,
                    "is_today": key == today,
                    "is_future": key > today,
                }
            )

        employees = []
        for employee in roster:
            employee_id = int(employee["id"])
            if not _is_on_the_sheet(employee, employee_id in on_record):
                continue
            theirs = rows.get(employee_id, {})
            # 这个人当月标了「休半天」的那几天（票 07）：格子上写出来、例休折算按 0.5 天。
            their_half_rests = half_rests.get(employee_id, set())
            hire_date = employee.get("hire_date") or None
            employees.append(
                {
                    "id": employee_id,
                    "name": employee.get("name") or "",
                    "job_title": employee.get("job_title") or "",
                    "base_salary": employee.get("base_salary"),
                    "seniority_bonus": employee.get("seniority_bonus"),
                    # 入职日期落在当月 = 新入职；没填入职日期 = 留空（`None`），不是「不是新来的」。
                    "new_hire": None if not hire_date else str(hire_date)[:7] == month,
                    "net_hours": totals.get(employee_id, 0) / 2,
                    # 这一页的逐日改格（票 06）能挑的班次：**启用中的**那一份。
                    # 每个员工带一份，前端不用再打第二个接口，也就不会有两份会漂的名单。
                    "shifts": shifts,
                    # 还没批的加班 / 补钟（故事 14）：页面拿 `pending_count` 标色、拿
                    # `pending_hours` 说清还有几小时等着批；它们**不进** `net_hours`。
                    "pending_hours": pending.get(employee_id, {}).get("half_hours", 0) / 2,
                    "pending_count": pending.get(employee_id, {}).get("count", 0),
                    # 假期台账（票 03）：这一块全是**暂算值**，当月只算不落库。
                    "leave": _leave_terms(
                        today=today,
                        theirs=theirs,
                        half_rest_days=their_half_rests,
                        holidays=holidays,
                        employee=employee,
                        month_days=len(days),
                        previous_balance=_previous_balance(
                            ledger.get((employee_id, previous_month)),
                            ledger.get((employee_id, month)),
                        ),
                    ),
                    "days": [
                        {
                            "business_date": day["business_date"],
                            "state": _day_state(today, day["business_date"], theirs),
                            # 那天挂的是哪个班（`None` = 休 / 没排到 / 还没到）。
                            # 状态之外再给班次号：卡片上的下拉要标出「现在是哪个」。
                            "shift_id": theirs.get(day["business_date"]),
                            # 半天休（票 07）：那天仍然只挂上面那个班次，只是上半天。
                            # 只有「上班」那一态才可能是 `True`（写路径与读口都挡了
                            # 「休 + 休半天」那种自相矛盾的行）。
                            "half_rest": day["business_date"] in their_half_rests,
                        }
                        for day in days
                    ],
                }
            )

        return {
            "month": month,
            "today": today,
            # 当月只算不落库（`docs/adr/0113`）：这个月的每一个数都还会随排班与加班变，
            # 页面照着这一位标「暂算值（未封账）」。封账封的是**已经过完的**月份
            # （票 04 写台账那一行），当月永远不封 —— 所以这一位眼下恒为 True。
            "provisional": True,
            # 上月封账（票 04）：`sealed_at` 是那一刻的定稿时刻，有它才说明卡片上的
            # 「上月存假」来自结转而不是人工期初。没有这一位（老载荷）时页面按「还没封」画。
            "previous_month": {
                "month": previous_month,
                "sealed_at": _sealed_at(ledger, previous_month),
            },
            "days": days,
            "employees": employees,
        }

    @_needs_migration
    @serialized_write
    async def set_half_rest(
        self, employee_id: int, business_date: str, half_rest: bool = True
    ) -> dict:
        """把某人某一天标成「休半天」/ 取消（票 07，ADR 0114）。**只有这一扇门能设它。**

        改的是 `staff_assignments` 那一行上的 `half_rest` 一个字段：班次、工作区、来源
        （`source`）与那一天是不是单日覆盖，**一个字都不动** —— 半天休不是第二个班次，
        也不是一次「跟规则不一样」的改写（那天仍然只挂本来那个班次、仍然只有一行）。

        三道闸，都在写之前：

        * 日期得是 `YYYY-MM-DD`（坏格式 = `invalid_business_date`）；
        * 得落在**当月**（这一页只算当月，写口也只认当月：上个月的格子它压根不显示）；
        * 那天得**真有班次**（`shift_id` 非空）。没有排班行 = 「待补」、行在但班次空 =
          「休」—— 两种都标不上「休半天」：那两天本来就没有半天班可言（`half_rest_needs_shift`）。

        写完 `commit` 一次。广播与鉴权在 `api/attendance.py`（这一层不碰 HTTP）。
        """
        today = self.today()
        day = _require_business_date(business_date)
        if day[:7] != today[:7]:
            raise AttendanceError("out_of_month", today)
        employee_id = int(employee_id)
        row = await self._assignment_row(employee_id, day)
        if row is None or row["shift_id"] is None:
            raise AttendanceError("half_rest_needs_shift", day)
        await self._conn.execute(
            """UPDATE staff_assignments SET half_rest = ?, updated_at = ?
               WHERE employee_id = ? AND business_date = ?""",
            (1 if half_rest else 0, self._now_iso(), employee_id, day),
        )
        await self._conn.commit()
        logger.info(
            "逐日改格：%s %s 的「休半天」=%s", employee_id, day, "是" if half_rest else "否"
        )
        return {
            "employee_id": employee_id,
            "business_date": day,
            "half_rest": bool(half_rest),
        }

    @_needs_migration
    @serialized_write
    async def seal(self, month: Optional[str] = None) -> dict:
        """把某个**已经过完的**自然月的存假结余写进台账并定稿（票 04 / ADR 0113）。

        `month` 不给就是**上一个自然月**（页面上那颗「封账上月」按钮打的就是这一条）。
        写下去的是那一行的 `closing_balance`（本月结余 = 池 − 本月自然天数）与
        `sealed_at`；**行里别的列一个字都不动** —— 期初（票 03 的人工录入）与手填列
        （票 05）是别人写下的真相，封账只加自己那一份。

        三道闸：

        * **只有已经过完的月能封**（`month_not_finished`）：当月与未来一律拒 —— 这个月的
          实际出勤、例休、补休都还会变，封了就是把暂算值当定稿。
        * **封过的月不再封**（`already_sealed`）：整月拒，已写下的那一行一个字都不改
          （重算一遍历史只会得到另一个数，见 ADR 0113）。
        * 缺表（0027 没应用）照旧走 `not_migrated`。

        名单与算式跟 `sheet()` **同一套**：这个人那个月在表上（当月有排班 / 加班记录，
        或现在在职）就在封账名单里，算的是那个月的实际出勤、当月法定节假日补休、生日假、
        年假与**再上一月**的存假。所以封下的数就是那个月卡片上那张账。

        写入用 `ON CONFLICT ... WHERE sealed_at IS NULL` 兜并发：两个请求同时走到这里时
        后一个不覆盖前一个（`sealed_employees` 是这次落在表上的人数）。
        """
        today = self.today()
        current_month = today[:7]
        target = _validated_month(month) if month else _previous_month(current_month)
        if target >= current_month:
            # 当月与未来都还没过完：封账是给「已经定下来的那一个月」用的。
            raise AttendanceError("month_not_finished", target)

        start = date.fromisoformat(f"{target}-01")
        month_days = monthrange(start.year, start.month)[1]
        first, until = start.isoformat(), _next_month(start).isoformat()
        # 这个月最后一天：整月都过完了，算「已过」的天数时它就是那个月的「今天」。
        as_of = (start + timedelta(days=month_days - 1)).isoformat()

        if await self._sealed_rows(target):
            raise AttendanceError("already_sealed", target)

        # 两份索引：三态（`rows`）与「哪天标了休半天」（`half_rests`）—— 后者进例休折算。
        rows, half_rests = await self._assignment_rows(first, until)
        _, recorded = await self._month_entries(first, until)
        holidays = await self._statutory_holidays(target)
        before = _previous_month(target)
        ledger = await self._ledger_rows((before, target))
        roster = await EmployeeAccounts(self._write_lock_owner, now=self._now).list_roster()
        on_record = set(rows) | recorded
        sealed_at = self._now_dt().isoformat()

        sealed = 0
        for employee in roster:
            employee_id = int(employee["id"])
            if not _is_on_the_sheet(employee, employee_id in on_record):
                continue
            leave = _leave_terms(
                today=as_of,
                theirs=rows.get(employee_id, {}),
                # 封账与当月卡片同一套算式：标了「休半天」的那天算 0.5 天出勤（票 07）。
                # 少了这一句，封下来的存假会比卡片上多算半天。
                half_rest_days=half_rests.get(employee_id, set()),
                holidays=holidays,
                employee=employee,
                month_days=month_days,
                previous_balance=_previous_balance(
                    ledger.get((employee_id, before)),
                    ledger.get((employee_id, target)),
                ),
                month=target,
            )
            await self._conn.execute(
                """INSERT INTO attendance_ledger
                   (tenant_id, employee_id, "month", closing_balance, sealed_at,
                    created_at, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (tenant_id, employee_id, "month") DO UPDATE SET
                     closing_balance = excluded.closing_balance,
                     sealed_at = excluded.sealed_at,
                     updated_at = excluded.updated_at
                   WHERE attendance_ledger.sealed_at IS NULL""",
                (employee_id, target, leave["balance"], sealed_at, sealed_at, sealed_at),
            )
            sealed += 1
        await self._conn.commit()
        logger.info("封账: month=%s rows=%s", target, sealed)
        return {"month": target, "sealed_at": sealed_at, "sealed_employees": sealed}

    async def _sealed_rows(self, month: str) -> list[dict]:
        """这个月**已经封过账**的行（空 = 还没封，可以封）。"""
        cur = await self._conn.execute(
            """SELECT employee_id FROM attendance_ledger
               WHERE "month" = ? AND sealed_at IS NOT NULL""",
            (month,),
        )
        return [dict(row) for row in await cur.fetchall()]


def _is_on_the_sheet(employee: dict, on_record: bool) -> bool:
    """这个人进不进当月的表。

    在职（已批准、未停用）的一律在；此外**当月有排班或加班记录**的也在 —— 当月离职的
    人那天还有班，漏掉他就漏结。反过来，一个早已停用、当月又一条记录都没有的人不在表上。
    """
    return on_record or (bool(employee.get("approved")) and not employee.get("disabled"))


def _day_state(today: str, business_date: str, theirs: dict[str, Any]) -> Optional[str]:
    """那天这个人是什么状态；今天之后是 `None`（留空，不是「待补」）。"""
    if business_date > today:
        return None
    if business_date not in theirs:
        return STATE_TODO
    return STATE_REST if theirs[business_date] is None else STATE_WORK


def _leave_terms(
    *,
    today: str,
    theirs: dict[str, Any],
    holidays: list[str],
    employee: dict,
    month_days: int,
    previous_balance: float,
    half_rest_days: Optional[set[str]] = None,
    month: Optional[str] = None,
) -> dict[str, Any]:
    """卡片上那一块假期算式（票 03，ADR 0113 的口径）。**全是派生值，不落库。**

    ``theirs`` 是这个人当月的排班行（``{营业日: 班次 id 或 None}``）；``half_rest_days``
    是其中**标了「休半天」**的那几天（票 07，只含班次非空的日子）；``holidays`` 是
    当月法定节假日（`HolidayCalendar.days_in_month`，日历是唯一依据）；``employee`` 是
    花名册那一行（生日从身份证号派生、工龄奖与入职日期都在里面）。

    * **实际出勤天数**只数**已过**的日子 —— 今天之后排了班也是「还没发生」。
      **标了「休半天」的那天算 0.5 天**（`GLOSSARY.md` 的月例休：「出勤半天算 0.5 天
      参与折算」）：月例休与存假都跟着这半个天走。
    * **法定节假日补休**按**当月每个**法定节假日算：那天排了班（有排班行、班次非空）
      就 +1，休的不给，没标日历的没有。这条不跟「已过」：日历与轮转都已经知道那天他
      上班，池子又是整月的量（票 03 验收 3）。**半天休照给整天**：那天他确实上了班
      （补休的判据是「那天排了班」，不是「上了几个半天」）。
    * **生日假**：身份证号派生的生日落在当月 +1。生日那一栏是**派生值**（`MM-DD`，
      没填身份证号 / 号码读不出日期就是 `None`）—— 算不出来的人没有这一项，不是 0 天。
      平年 2 月 29 日折到 28 日由 `birthday_in_year` 负责（与人事提醒同一处口径）。
    * **年假**只在**工龄奖调整发生的那一个月**计入：`seniority_due_month`（应为值是
      哪一次调整带来的）就在当月，而且「当前」与「应为」确实已经对上（还没落账的、
      调过头的都不算）—— GLOSSARY 的原话是「由应为值与当前值什么时候对上说了算」。
      管理层 5 天、其他员工 3 天，**管理层按管理能力开关非空判，与职位名无关**。
    * **可用天数池** = 上面五项 + 上月存假；**本月出勤天数** = `min(池, 本月自然天数)`；
      **存假** = `池 − 本月自然天数`（可为负 = 欠班、可带 .5）。

    **加班与补钟不参与这一套**（`GLOSSARY.md` 的存假：净时长单独结算）。

    `month` 是「算的是哪个月」—— `sheet()` 不给，就取 `today` 那个月（当月的卡片）；
    封账（票 04）算的是**上一个自然月**，于是它明说哪个月，并把 `today` 顶到那个月的
    最后一天（整月都过完了，那个月的每一天都算「已过」）。生日假与年假按 `month` 判，
    不跟 `today` 的月份 —— 否则封 8 月的账会拿 9 月的生日去给假。
    """
    month = month or today[:7]
    half_rest_days = half_rest_days or set()
    actual_days = 0.0
    for business_date, shift_id in theirs.items():
        if business_date <= today and shift_id is not None:
            actual_days += 0.5 if business_date in half_rest_days else 1.0
    holiday_makeup = sum(1 for day in holidays if theirs.get(day) is not None)
    birthday_leave = 0
    birthday = employee.get("birthday")
    if birthday:
        landed = birthday_in_year(birthday, int(month[:4]))
        if landed and landed[:2] == month[5:7]:
            birthday_leave = 1
    annual_leave = 0
    hire_date = employee.get("hire_date") or None
    should_be = seniority_should_be(hire_date, today)
    settled = employee.get("seniority_bonus")
    if (
        should_be > 0
        and settled is not None
        and int(settled) == should_be
        and seniority_due_month(hire_date, today) == month
    ):
        annual_leave = (
            ANNUAL_LEAVE_MANAGEMENT if employee.get("admin_caps") else ANNUAL_LEAVE_STAFF
        )
    monthly_rest = min(MONTHLY_REST_MAX, math.floor(actual_days / MONTHLY_REST_STEP))
    pool = (
        actual_days
        + monthly_rest
        + holiday_makeup
        + birthday_leave
        + annual_leave
        + previous_balance
    )
    return {
        "actual_days": actual_days,
        "monthly_rest": monthly_rest,
        "holiday_makeup": holiday_makeup,
        "birthday_leave": birthday_leave,
        "annual_leave": annual_leave,
        "previous_balance": previous_balance,
        "pool": pool,
        "month_days": month_days,
        # 池比自然天数还小的时候按池填（这个月剩下的假不够铺满），所以这里不能 clamp 到 0。
        "month_attendance_days": min(pool, float(month_days)),
        "balance": pool - month_days,
    }


def _sealed_at(ledger: dict[tuple[int, str], dict], month: str) -> Optional[str]:
    """这个月**已经封账**的时刻（一行都没有 / 都没封账时是 `None`）。

    取的是一行上的 `sealed_at` —— 同一次封账写下的行时刻相同，取最大值只为确定性
    （页面上那句话要一个稳定的字符串）。这一位就是「封账行看得出是什么时候封的」。
    """
    stamps = [
        row["sealed_at"]
        for (_, row_month), row in ledger.items()
        if row_month == month and row.get("sealed_at")
    ]
    return max(str(stamp) for stamp in stamps) if stamps else None


def _previous_balance(previous_row: Optional[dict], current_row: Optional[dict]) -> float:
    """上月存假（票 03 验收 7）。

    正源是**上一月已封账那一行的结余** —— 封账是定稿，过去的月份不随后来的排班改动漂
    （ADR 0113）。上一月还没有已封账的行时（上线当月 2026-10 就是这一种，之前根本没有
    台账数据）退回读**本月这一行的期初余额** —— 那是人工录入一次的「上月末余额」。
    两个都没有就是 0（新员工从零开始）。
    """
    if previous_row is not None and previous_row.get("sealed_at"):
        return float(previous_row.get("closing_balance") or 0.0)
    if current_row is not None and current_row.get("opening_balance") is not None:
        return float(current_row["opening_balance"])
    return 0.0
