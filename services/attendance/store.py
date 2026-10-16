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
   要能标出「现在是哪个班」并挑另一个。这一层仍然**只读** —— 写路径在排班那一边。

「今天」取**营业日**（06:00 切，同排班）：考勤表的格子就是
`staff_assignments.business_date`，两边的「哪一天」必须是同一个词。

假期台账（票 03，口径见 `GLOSSARY.md` 的「假期」那几节与 `docs/adr/0113`）：

* **实际出勤天数**：本月**已过**的营业日里「排了班、又没标休」的天数（半天休在票 07
  之后算 0.5 天）。今天之后排的班一天都不算 —— 那些日子还没发生。
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
"""

from __future__ import annotations

import functools
import logging
import math
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

import asyncpg

from db_core.utils import CHINA_TZ
from services.business_day import current_business_date
from services.holiday_calendar import HolidayCalendar, HolidayError
from services.identity import EmployeeAccounts
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
    """要读的表还没建（0005 / 0021 / 0026 / 0027 没应用）时，换成一个能照做的错。

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


class AttendanceStore:
    """当月考勤表的读口。构造要一个已打开的库连接（同 `SchedulingStore` 的口径）。"""

    def __init__(self, conn_or_db, now: Optional[Callable[[], datetime]] = None):
        conn = getattr(conn_or_db, "_conn", conn_or_db)
        if conn is None:
            raise RuntimeError("AttendanceStore requires an open database connection")
        self._conn = conn
        self._now = now or (lambda: datetime.now(CHINA_TZ))
        self._write_lock_owner = conn_or_db

    def _now_dt(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TZ)
        return value

    def _now_iso(self) -> str:
        return self._now_dt().isoformat()

    def today(self) -> str:
        """当前营业日（06:00 切，北京时）—— 与 `staff_assignments.business_date` 同一个词。"""
        return current_business_date(self._now_dt())

    async def _rollback_quietly(self) -> None:
        try:
            await self._conn.rollback()
        except Exception:  # pragma: no cover - 回滚失败不该盖住原始错误
            logger.debug("回滚失败（忽略）", exc_info=True)

    async def _assignment_rows(self, first: str, next_month: str) -> dict[int, dict[str, Any]]:
        """当月每天每人的排班行：``{员工号: {营业日: 班次 id 或 None}}``。

        **有行**就说明那天被铺过（`None` = 休）；没有这一格就是「待补」，所以这里不
        过滤班次为空的行 —— 过滤掉的话「休」会与「没铺到」塌成同一件事。
        """
        cur = await self._conn.execute(
            """SELECT employee_id, business_date, shift_id FROM staff_assignments
               WHERE business_date >= ? AND business_date < ?""",
            (first, next_month),
        )
        rows: dict[int, dict[str, Any]] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            rows.setdefault(int(mapping["employee_id"]), {})[mapping["business_date"]] = (
                None if mapping["shift_id"] is None else int(mapping["shift_id"])
            )
        return rows

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

    async def _ledger_rows(self, *months: str) -> dict[tuple[int, str], dict]:
        """本月与上一月的台账行：``{(员工号, 月份): 那一行}``。

        四个手填列（`shift_name` / `lodging` / `borrowed_store` / `resigned`，票 05）
        与假期那几列一起读出来 —— 读的是同一行，不再打第二个查询。写这一行的是封账
        （票 04，`closing_balance` / `sealed_at`）与手填列（票 05，那四列）。缺表
        （0027 没应用）时照旧走 `not_migrated` 那条出口。
        """
        placeholders = ", ".join(["?"] * len(months))
        cur = await self._conn.execute(
            f"""SELECT employee_id, "month", opening_balance, closing_balance, sealed_at,
                      shift_name, lodging, borrowed_store, resigned
               FROM attendance_ledger WHERE "month" IN ({placeholders})""",
            tuple(months),
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

    async def _shift_name_defaults(
        self, rows: dict[int, dict[str, Any]]
    ) -> dict[int, str]:
        """「本月班次」的默认值：这个人当月**出现次数最多**的班次名（票 05）。

        并列时取 `staff_shifts.sort_order` 靠前的那个（票面口径：并列取班次显示顺序）。
        **当月一条排班行都没有的人不进这份字典** —— 卡片上留空，不是报错、也不是编一个
        默认班次。只挑**排在上班**的行（班次非空）：休的那些行不投任何票。

        班次名从 `staff_shifts` 取、**不看 `is_active`**：这是已经在员工身上的历史班次，
        后来把这个班次停用不会让他这个月没上过班（同 `list_shifts(include_inactive=True)`
        的理由）。班次行被硬删掉的人照旧计票，只在名字那一格留空。

        ``rows`` 就是 `_assignment_rows` 那一份（``{员工号: {营业日: 班次 id 或 None}}``）
        —— 不再打第二个「这个月谁排了什么班」的查询。
        """
        counts: dict[int, dict[int, int]] = {}
        for employee_id, theirs in rows.items():
            for shift_id in theirs.values():
                if shift_id is None:
                    continue
                per_person = counts.setdefault(employee_id, {})
                per_person[shift_id] = per_person.get(shift_id, 0) + 1
        if not counts:
            return {}
        shift_ids = {shift_id for per_person in counts.values() for shift_id in per_person}
        placeholders = ", ".join(["?"] * len(shift_ids))
        cur = await self._conn.execute(
            f"""SELECT id, name, sort_order FROM staff_shifts WHERE id IN ({placeholders})""",
            tuple(sorted(shift_ids)),
        )
        names: dict[int, tuple[str, int]] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            names[int(mapping["id"])] = (mapping["name"], int(mapping["sort_order"]))
        defaults: dict[int, str] = {}
        for employee_id, per_person in counts.items():
            # 先比次数（多的赢），再比显示顺序（靠前的赢），最后拿班次号兜底 ——
            # 顺序是全序，两次调用不会给出两个答案。
            best = min(
                per_person,
                key=lambda shift_id: (
                    -per_person[shift_id],
                    names.get(shift_id, ("", 0))[1],
                    shift_id,
                ),
            )
            name = names.get(best, ("", 0))[0]
            if name:
                defaults[employee_id] = name
        return defaults

    @_needs_migration
    async def set_manual_columns(
        self, employee_id: int, values: dict[str, Optional[str]]
    ) -> dict[str, Any]:
        """把四个手填列写在本月那**一行**台账上（票 05）。返回写完之后的那四个值。

        `values` 里**只放点名要改的那几列**（键取自 `MANUAL_FIELDS`）：没点名的列一个都
        不碰 —— 这就是「只更新自己那几列」。点名成 `None`（或空串）是**清空**：存 NULL，
        与从没填过同义（读回来照旧退回「本月班次」那条默认）。

        缺表（0027 没应用）时与读侧同一个出口：`AttendanceError("not_migrated")`，
        HTTP 层翻成 503 点名那个脚本 —— 店长该看到的是「去做数据库迁移」，不是一个 500。

        台账那一行是**票 04 的封账与票 05 的手填列共用**的一行（`UNIQUE(员工, 月份)`）：
        整行覆盖会把对方写下的数抹掉，所以 INSERT 时只写 `employee_id` / `month`（别的列
        留给各自的写入口），冲突时**只有点名的那几列进 `SET`**，`closing_balance` 与
        `sealed_at` 一个都不在语句里 —— 哪怕这一次是新建那一行（`DO UPDATE` 因此也
        **不更新** `created_at`，那一行是什么时候建的仍然说得清）。
        """
        fields = [field for field in MANUAL_FIELDS if field in values]
        if not fields:
            raise AttendanceError("nothing_to_write")
        month = self.today()[:7]
        now = self._now_iso()
        primary = ", ".join(f'"{field}"' for field in fields)
        placeholders = ", ".join(["?"] * len(fields))
        do_update = ", ".join(f'"{field}" = excluded."{field}"' for field in fields)
        try:
            await self._conn.execute(
                f"""INSERT INTO attendance_ledger
                        (tenant_id, employee_id, "month", {primary}, created_at, updated_at)
                    VALUES (1, ?, ?, {placeholders}, ?, ?)
                    ON CONFLICT (tenant_id, employee_id, "month") DO UPDATE SET
                        {do_update}, updated_at = excluded.updated_at""",
                (
                    int(employee_id),
                    month,
                    *(_text(values[field]) for field in fields),
                    now,
                    now,
                ),
            )
            await self._conn.commit()
        except Exception:
            # 半截事务不能留在连接上（同 `serialized_write` 的理由）。
            await self._rollback_quietly()
            raise
        # 回执**重读那一行**（不是拿这次写进去的几列拼一个）：写完之后这一行上是什么，
        # 卡片与导出件就照着那个走，调用方不用自己拼「没点名的那几列还是原样」。
        ledger = await self._ledger_rows(month)
        return {
            "employee_id": int(employee_id),
            "month": month,
            **_manual_columns(ledger.get((int(employee_id), month)), default_shift_name=None),
        }

    @_needs_migration
    async def sheet(self) -> dict:
        """当月那一张考勤表：月框 + 一人一张卡。**唯一的读口**，没有月份参数。"""
        today = self.today()
        month = today[:7]
        start = date.fromisoformat(f"{month}-01")
        next_month = _next_month(start)
        # 区间右开：`[本月一号, 下月一号)` —— 不自己数月份长度（2 月与 31 天的月各一次坑）。
        first, until = start.isoformat(), next_month.isoformat()

        rows = await self._assignment_rows(first, until)
        totals, recorded = await self._month_entries(first, until)
        # 还没批的加班 / 补钟：只标色，不进净时长、也不进存假（故事 14）。
        pending = await self._pending_entries(first, until)
        # 当月的法定节假日（补休要用）。日历缺表时与缺排班表一样报 `not_migrated`。
        holidays = await self._statutory_holidays(month)
        # 本月与上一月的台账行：算「上月存假」（已封账的结余 / 人工期初）。
        previous_month = (start - timedelta(days=1)).isoformat()[:7]
        ledger = await self._ledger_rows(previous_month, month)
        shifts = await self._shifts()
        # 「本月班次」的默认值（票 05）：当月众数，并列取班次显示顺序。没有排班的人不进这份
        # 字典 —— 卡片上留空（不是报错，也不是编一个班次）。
        default_shift_names = await self._shift_name_defaults(rows)
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
                        }
                        for day in days
                    ],
                    # 四个手填列（票 05）：卡片上那四个输入口读的就是这一份 —— 顺序与
                    # `0027_attendance_ledger.sql` 的列序一致。
                    # * `shift_name` 是**这一格上该显示的值**：台账里填过就用填的，没填过
                    #   （或清空了）就是**当月众数**（并列取班次显示顺序），当月压根没排班
                    #   的人留空。
                    # * `default_shift_name` 另给一份**没填过时的默认**：页面把它放在输入框
                    #   的提示语里，店长才分得清「这是默认」与「这是我定的」—— 给没填过的人
                    #   预填上默认值，一存就把这条默认固化成了他定的值。
                    **_manual_columns(
                        ledger.get((employee_id, month)),
                        default_shift_name=default_shift_names.get(employee_id),
                    ),
                }
            )

        return {
            "month": month,
            "today": today,
            # 当月只算不落库（`docs/adr/0113`）：这个月的每一个数都还会随排班与加班变，
            # 页面照着这一位标「暂算值（未封账）」。封账封的是**已经过完的**月份
            # （票 04 写台账那一行），当月永远不封 —— 所以这一位眼下恒为 True。
            "provisional": True,
            "days": days,
            "employees": employees,
        }


# 卡片上那四个**手填**列（票 05），列名与 `0027_attendance_ledger.sql` 一一对应：
# 系统里没有这几个字段（是否住宿 / 借调门店 / 是否离职在花名册里压根没有），所以由超管
# 在卡片上填一次、存在**月度考勤台账**当月那一行上，只进导出件与卡片，**不写回员工档案**。
MANUAL_FIELDS = ("shift_name", "lodging", "borrowed_store", "resigned")


def _manual_columns(row: Optional[dict], *, default_shift_name: Optional[str]) -> dict[str, Any]:
    """卡片上那四个手填列（票 05）。``row`` 是这个人**本月**台账那一行（没有就是 `None`）。

    * **本月班次**：台账里填过就用填的；没填过（或清空了 —— 「从没填过」与「清空」同义）
      退回 ``default_shift_name`` —— 当月众数，并列取班次显示顺序；当月一条排班都没有的
      人两个都没有，留空（`None`），不是报错。
    * **是否住宿 / 借调门店 / 是否离职**：台账里有什么给什么，没有就是 `None`（页面上
      显示为空）。空串一律当没填（`None`）—— 一个「存进去的空串」不该在页面上与
      「没填过」长得不一样。

    返回值恒有那四个键（`None` = 空）**外加** `default_shift_name`（没填过时的默认，
    页面放进输入框的提示语），前端不用判 `undefined`。
    """
    values = {field: _text(row.get(field)) if row else None for field in MANUAL_FIELDS}
    if values["shift_name"] is None:
        values["shift_name"] = _text(default_shift_name)
    # 没填过时的默认另给一份（提示语用）—— `shift_name` 是**这一格上该显示的值**，
    # 不能靠它自己反推「这是默认还是他定的」。
    values["default_shift_name"] = _text(default_shift_name)
    return values


def _text(value: Any) -> Optional[str]:
    """手填列的文本：空串 / 空白一律当没填（`None`）。不是字符串的原样转成字符串。"""
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text.strip() or None


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
) -> dict[str, Any]:
    """卡片上那一块假期算式（票 03，ADR 0113 的口径）。**全是派生值，不落库。**

    ``theirs`` 是这个人当月的排班行（``{营业日: 班次 id 或 None}``）；``holidays`` 是
    当月法定节假日（`HolidayCalendar.days_in_month`，日历是唯一依据）；``employee`` 是
    花名册那一行（生日从身份证号派生、工龄奖与入职日期都在里面）。

    * **实际出勤天数**只数**已过**的日子 —— 今天之后排了班也是「还没发生」。
    * **法定节假日补休**按**当月每个**法定节假日算：那天排了班（有排班行、班次非空）
      就 +1，休的不给，没标日历的没有。这条不跟「已过」：日历与轮转都已经知道那天他
      上班，池子又是整月的量（票 03 验收 3）。
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
    """
    month = today[:7]
    actual_days = 0.0
    for business_date, shift_id in theirs.items():
        if business_date <= today and shift_id is not None:
            actual_days += 1.0
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
