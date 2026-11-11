#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""存假台账（`GLOSSARY.md` 的「存假台账」）的**只读投影** —— 不写库、不持锁（`docs/adr/0139`）。

它把「某个人某个月攒了多少假、用了多少、还剩多少」这一套派生值收到一处：考勤表
（`services/attendance/store.py` 的 `sheet()` / `leave_summary()` / `seal()`）与加班
抵扣（`services/overtime/ledger.py` 的 `deduction_capacity()`）都念这一份，两边不会
各算一套余额。口径本身仍是 ADR 0113（月度封账）与 ADR 0124（补钟用存假抵扣）：

* **池** = 实际出勤 + 月例休 + 法定节假日补休 + 生日假 + 年假 + 上月存假；
* **存假** = 池 − 本月在岗天数 − 本月已批抵扣（可为负 = 欠班、可带 .5）；
* 台账里**手填过** `closing_balance` 就以手填值为有效值，公式值另给 `balance_computed`；
* 封账只读 `attendance_ledger` 的 `sealed_at`，这一层不写它。

数据来源：排班日格（`DayGrid`）、法定节假日（`HolidayCalendar`）、台账
（`attendance_ledger`）、加班登记（`overtime_entries`，只数已批补钟）、抵扣
（`overtime_leave_deductions`，只数已批）与花名册（`EmployeeAccounts`）。后两张附表
缺表时按「没有」处理（0042 / 0027 没应用的老库先跑着），缺法定节假日日历则照旧报错
—— 那张表是这一页的前提，缺了不能静默算成 0。
"""

from __future__ import annotations

import logging
import math
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Optional

import asyncpg

from db_core.utils import CHINA_TZ
from services.business_day import current_workbench_date
from services.holiday_calendar import HolidayCalendar, HolidayError
from services.identity import EmployeeAccounts
from services.identity.profile import (
    birthday_in_year,
    seniority_due_month,
    seniority_should_be,
)
from services.overtime_contract import (
    HALF_HOURS_PER_LEAVE_DAY,
    HOURS_PER_DAY,
    STATUS_APPROVED,
)
from services.scheduling.day_grid import DayGrid

logger = logging.getLogger(__name__)

# 假期台账那几个数（票 03）。每一条都在 `GLOSSARY.md` 的「考勤与假期」里定死：
# 月例休每出勤 6 天折 1 天、上限 4 天；年假只在工龄奖调整那个月给，管理层 5 天、
# 其他员工 3 天（判据是**管理能力开关非空**，与职位名无关）。
MONTHLY_REST_STEP = 6
MONTHLY_REST_MAX = 4
ANNUAL_LEAVE_MANAGEMENT = 5
ANNUAL_LEAVE_STAFF = 3


def _month_start(month: Any) -> date:
    """``YYYY-MM`` → 那个月一号。解析不了就让 `ValueError` 上去（这一层不做领域报错）。"""
    return date.fromisoformat(f"{str(month).strip()}-01")


class LeaveLedger:
    """某个人 / 某个月（或整店）的存假台账投影。

    构造与 `DayGrid` 同口径：给 ``DatabaseManager`` 或已打开的连接都行；``now`` 可注入，
    时钟与 `AttendanceStore` 同一条（工作台日 = 自然日，00:00 切，ADR 0125）。
    """

    def __init__(
        self,
        conn_or_db: Any,
        *,
        now: Optional[Callable[[], datetime]] = None,
    ):
        conn = getattr(conn_or_db, "_conn", conn_or_db)
        if conn is None:
            raise RuntimeError("LeaveLedger requires an open database connection")
        self._conn = conn
        self._now = now or (lambda: datetime.now(CHINA_TZ))
        # 只用来读别人（EmployeeAccounts / DayGrid / HolidayCalendar）；这一层自己不取锁。
        self._write_lock_owner = conn_or_db

    def _now_dt(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TZ)
        return value

    def today(self) -> str:
        """当前工作台日 —— 与 `AttendanceStore.today()` 同一个 helper、同一条切点。"""
        return current_workbench_date(self._now_dt())

    async def _rollback_quietly(self) -> None:
        try:
            await self._conn.rollback()
        except Exception:  # pragma: no cover - 回滚失败不该盖住原始错误
            logger.debug("回滚失败（忽略）", exc_info=True)

    # ── 读口 ────────────────────────────────────────────────────────────────

    async def ledger_row(
        self, employee_id: int, month: Optional[str] = None
    ) -> Optional[dict]:
        """某个人某个月的台账行；不在花名册（或名单里查无此人）时是 ``None``。

        ``month`` 省略就是当月。实现走下面那份批量（只传他一个人）—— 算式只有一份。
        """
        employee_id = int(employee_id)
        rows = await self.ledger_rows(month or self.today()[:7], [employee_id])
        return rows.get(employee_id)

    async def ledger_rows(
        self, month: str, employee_ids: Optional[Iterable[int]] = None
    ) -> dict[int, dict]:
        """某个月一批人的台账行：``{员工号: 行}``。

        ``employee_ids`` 省略 = 花名册全员（含离职）；显式给名单时**不在花名册的人直接
        跳过**（不会凭空造一行）。一次读名单、一次读当月日格、一次读节假日、一次读
        台账（上月 + 本月）、一次读已批抵扣，再逐人套同一个算式。
        """
        return await self._rows(month, employee_ids)

    async def capacity(
        self, employee_id: int, month: Optional[str] = None
    ) -> Optional[dict]:
        """那个人那个月**还能抵几天**：补钟与存假两头都封顶（ADR 0124 的用户裁定）。

        * 补钟侧：已批补钟小时 − 已抵小时，够 `days × 8.5` 才抵得动；
        * 存假侧：当前有效余额（已扣掉已批抵扣）够 `days` 天才抵得动 —— **不能把存假
          抵成负数**；
        * 已封账的月 `max_days` 直接 0（「封过账就不能再抵」的唯一判据）。

        返回键与旧 `OvertimeLedger.deduction_capacity()` 逐字一致；未知员工回 ``None``，
        这一层不抛领域错误（代码是什么由调用方自己判）。
        """
        employee_id = int(employee_id)
        row = await self.ledger_row(employee_id, month)
        if row is None:
            return None
        target = row["month"]
        leave = row["leave"]
        makeup_half = await self._approved_makeup_half_hours(employee_id, target)
        # 抵扣那一半从同一行来（`ledger_rows` 已经读过已批抵扣），不再打第二个查询。
        deducted_days = int(leave["deducted_days"] or 0)
        deducted_half = deducted_days * HALF_HOURS_PER_LEAVE_DAY
        available_half = max(0, makeup_half - deducted_half)
        balance = float(leave["balance"])
        by_makeup = available_half // HALF_HOURS_PER_LEAVE_DAY
        by_leave = math.floor(balance) if balance > 0 else 0
        sealed = row["sealed_at"] is not None
        return {
            "month": target,
            "makeup_half_hours": makeup_half,
            "makeup_hours": makeup_half / 2,
            "deducted_days": int(deducted_half // HALF_HOURS_PER_LEAVE_DAY),
            "deducted_half_hours": deducted_half,
            "deducted_hours": deducted_half / 2,
            "available_half_hours": available_half,
            "available_hours": available_half / 2,
            "leave_balance": balance,
            "max_days_by_makeup": int(by_makeup),
            "max_days_by_leave": int(by_leave),
            "max_days": 0 if sealed else int(min(by_makeup, by_leave)),
            "sealed": bool(sealed),
            "half_hours_per_day": HALF_HOURS_PER_LEAVE_DAY,
            "hours_per_day": HOURS_PER_DAY,
        }

    async def _rows(
        self, month: str, employee_ids: Optional[Iterable[int]]
    ) -> dict[int, dict]:
        start = _month_start(month)
        month = start.isoformat()[:7]
        days_in_month = monthrange(start.year, start.month)[1]
        month_end = (start + timedelta(days=days_in_month - 1)).isoformat()
        previous_month = (start - timedelta(days=1)).isoformat()[:7]
        today = self.today()
        # 过去月顶到月末（与封账同口径），当月就是今天，未来月也按今天（排班还没铺）。
        as_of = min(today, month_end)

        # 节假日日历先读：缺 0026 时整页该报 `not_migrated`（503），不能因为当月一个人
        # 都没有就静默回一张空表 —— 那是「日历没应用」与「这个月没有节假日」的区别。
        holidays = await self._statutory_holidays(month)

        wanted: Optional[set[int]] = None
        if employee_ids is not None:
            wanted = {int(item) for item in employee_ids}
            if not wanted:
                return {}

        roster = await EmployeeAccounts(
            self._write_lock_owner, now=self._now
        ).list_roster()
        people = [
            employee
            for employee in roster
            if wanted is None or int(employee["id"]) in wanted
        ]
        if not people:
            return {}

        # 排班日格：一个月一次取全（含 half_rest），不逐人查。
        cells = await DayGrid(self._write_lock_owner, now=self._now).cells(
            start.isoformat(), month_end, roster_only=False
        )
        theirs: dict[int, dict[str, Any]] = {}
        half_rests: dict[int, set[str]] = {}
        for cell in cells:
            theirs.setdefault(cell.employee_id, {})[cell.business_date] = cell.shift_id
            if cell.half_rest:
                half_rests.setdefault(cell.employee_id, set()).add(cell.business_date)

        ledger = await self._ledger_rows((previous_month, month))
        deductions = await self._leave_deductions(month)

        result: dict[int, dict] = {}
        for employee in people:
            employee_id = int(employee["id"])
            stored = ledger.get((employee_id, month))
            leave = _with_manual_balance(
                _leave_terms(
                    # 历史月按月末算（与封账同口径），当月 / 未来月按今天 —— 生日与年资
                    # 都按目标月判，不能拿「今天所在月」去套历史月。
                    today=as_of,
                    theirs=theirs.get(employee_id, {}),
                    half_rest_days=half_rests.get(employee_id, set()),
                    holidays=holidays,
                    employee=employee,
                    month=month,
                    # 本月在岗天数：入职当月从入职那天算起，离职月截到离职日。
                    month_days=_on_duty_days(
                        start,
                        days_in_month,
                        employee.get("hire_date"),
                        employee.get("resigned_at"),
                    ),
                    previous_balance=_previous_balance(
                        ledger.get((employee_id, previous_month)),
                        stored,
                    ),
                    deducted_days=deductions.get(employee_id, 0),
                ),
                closing_balance=_ledger_number(stored, "closing_balance"),
            )
            sealed_at = _sealed_at(ledger, month)
            result[employee_id] = {
                "employee_id": employee_id,
                "month": month,
                "today": today,
                "as_of": as_of,
                "days_in_month": days_in_month,
                "sealed_at": sealed_at,
                "provisional": sealed_at is None,
                "previous_month": {
                    "month": previous_month,
                    "sealed_at": _sealed_at(ledger, previous_month),
                },
                "leave": leave,
            }
        return result

    # ── 数据来源（读法与考勤 / 加班两边同一份）─────────────────────────────

    async def _statutory_holidays(self, month: str) -> list[str]:
        """当月法定节假日（升序的日期串）。日历缺表照旧报错，由考勤那一层翻成 503。"""
        calendar = HolidayCalendar(self._write_lock_owner, now=self._now)
        try:
            return await calendar.days_in_month(month)
        except HolidayError:
            await self._rollback_quietly()
            raise

    async def _ledger_rows(self, months: tuple[str, str]) -> dict[tuple[int, str], dict]:
        """本月与上一月的台账行：``{(员工号, 月份): 那一行}``，一次读完。

        缺 0027 那张表时**按没有台账处理**（未封账、期初 0）—— 这一层是只读投影，
        老库也照常看数；封账与手填列的写路径仍由考勤那一层自己报 `not_migrated`。
        """
        try:
            cur = await self._conn.execute(
                """SELECT employee_id, "month", opening_balance, closing_balance, sealed_at
                   FROM attendance_ledger WHERE "month" IN (?, ?)""",
                months,
            )
            rows = await cur.fetchall()
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
            await self._rollback_quietly()
            logger.warning(
                "存假台账：attendance_ledger 还没建（0027），按未封账 / 期初 0 处理"
            )
            return {}
        result: dict[tuple[int, str], dict] = {}
        for row in rows:
            mapping = dict(row)
            result[(int(mapping["employee_id"]), mapping["month"])] = mapping
        return result

    async def _leave_deductions(self, month: str) -> dict[int, int]:
        """那个月**已经批下来**的补钟抵扣天数：``{员工号: 天数}``（ADR 0124）。

        只数 `approved` —— 待批 / 驳回 / 撤回 / 作废都不减少存假。缺 0042 时回空字典
        （抵扣是新功能，老库不该因此整页 503）。
        """
        try:
            cur = await self._conn.execute(
                """SELECT employee_id, SUM(days) AS days FROM overtime_leave_deductions
                   WHERE "month" = ? AND status = ? GROUP BY employee_id""",
                (month, STATUS_APPROVED),
            )
            rows = await cur.fetchall()
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
            await self._rollback_quietly()
            logger.warning(
                "存假台账：overtime_leave_deductions 还没建（0042），抵扣按 0 天处理"
            )
            return {}
        return {
            int(dict(row)["employee_id"]): int(dict(row)["days"] or 0) for row in rows
        }

    async def _approved_makeup_half_hours(self, employee_id: int, month: str) -> int:
        """那个人那个月**已批准**的补钟小时（正数、半小时单位）—— 可抵的池子。"""
        cur = await self._conn.execute(
            """SELECT COALESCE(SUM(-half_hours), 0) AS half_hours FROM overtime_entries
               WHERE employee_id = ? AND entry_date LIKE ? AND status = ?
                 AND half_hours < 0""",
            (int(employee_id), f"{month}%", STATUS_APPROVED),
        )
        row = await cur.fetchone()
        return int(dict(row)["half_hours"] or 0) if row is not None else 0


# ── 算式（从 `services/attendance/store.py` 原样迁入，ADR 0139）──────────────


def _ledger_number(row: Optional[dict], column: str) -> Optional[float]:
    """台账里那一列的原始数值；没填过 / 清空 / NaN / Inf 一律当 ``None``。"""
    if not row or row.get(column) is None:
        return None
    number = float(row[column])
    return number if math.isfinite(number) else None


def _on_duty_days(
    month_start: date, month_days: int, hire_date: Any, resigned_at: Any = None
) -> int:
    """这个人**本月应在岗**的自然天数（2026-10-29 用户裁定：入职当月从中入职那天算起）。

    * 入职日期为空 / 早于月初（老员工）：整月天数，口径一个字不变；
    * 入职日期落在本月：入职日到月底的天数；
    * 入职日期在月末之后（还没到）：0 —— 那时不该有欠班；
    * **离职日期落在本月**：截到离职日那天为止 —— 当月来又走的人，分母是「入职日到
      离职日」，不是整月。

    解析不了的坏值按「老员工」处理（不剪）：剪错了等于替他认账。
    """
    month_end = month_start + timedelta(days=month_days - 1)
    first_day = month_start
    last_day = month_end
    if hire_date:
        try:
            hired = datetime.strptime(str(hire_date)[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            hired = None
        if hired is not None and hired > first_day:
            first_day = hired
    # 离职（2026-10-29）：截到离职日为止 —— 离职日**当天仍算在岗**，与 `_day_state`
    # 的「离职日之后留空」是同一条线。空串（日期待补）不剪：不知道哪天走。
    if resigned_at:
        left_text = str(resigned_at)[:10]
        if left_text:
            try:
                left = datetime.strptime(left_text, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                left = None
            if left is not None and left < last_day:
                last_day = left
    if last_day < first_day:
        return 0
    return (last_day - first_day).days + 1


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
    deducted_days: float = 0.0,
) -> dict[str, Any]:
    """存假那一块算式（票 03，ADR 0113 的口径）。**全是派生值，不落库。**

    口径细节见 `GLOSSARY.md` 的「存假」与 ADR 0124，这里只记几个容易改坏的约束：

    * 出勤天数按**排班**数（含未来已排的班），标了「休半天」的那天算 0.5 天；
    * 法定节假日补休按**当月每个**法定节假日算，那天排了班就 +1，半天休照给整天；
    * 生日假按身份证派生的生日落在当月 +1（平年 2/29 折 28 由 `birthday_in_year` 管）；
    * 年假只在**工龄奖调整发生的那一个月**计入（当前值 == 应为值），管理层 5 天、
      其他员工 3 天，判据是管理能力开关非空，与职位名无关；
    * 池 = 五项 + 上月存假；存假 = 池 − 本月在岗天数 − 已批抵扣（可为负、可带 .5）。
    """
    month = month or today[:7]
    half_rest_days = half_rest_days or set()
    actual_days = 0.0
    for business_date, shift_id in theirs.items():
        # 预结算（2026-10-29 用户裁定）：**未来已排的班也算**，不按「已过」截止 ——
        # 店长按排班看数；封账时整月都过完了，两种口径同值。未来没排班的天不会进
        # `theirs`（没有排班行），所以不用再判一次日期。
        if shift_id is not None:
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
        # 本月经补钟抵扣的天数（ADR 0124）：只从余额里减，池与明细保持原样。
        "deducted_days": float(deducted_days),
        # 池比自然天数还小的时候按池填，所以这里不能 clamp 到 0。
        "month_attendance_days": min(pool, float(month_days)),
        "balance": pool - month_days - float(deducted_days),
    }


def _with_manual_balance(
    terms: dict[str, Any], closing_balance: Optional[float]
) -> dict[str, Any]:
    """给算式的结果盖上**人工定稿**那一层（票 09）。

    台账那一行填过 `closing_balance` 就以它为准，没填过就照公式值。返回的 dict 在原有
    键之外多两位：``balance_computed``（公式值）与 ``balance_is_manual``（这一次用的是
    不是手填值）；``balance`` 的含义因此从「公式值」变成「**有效值**」。
    """
    computed = terms["balance"]
    terms["balance_computed"] = computed
    terms["balance_is_manual"] = closing_balance is not None
    if closing_balance is not None:
        terms["balance"] = closing_balance
    return terms


def _sealed_at(ledger: dict[tuple[int, str], dict], month: str) -> Optional[str]:
    """这个月**已经封账**的时刻（一行都没有 / 都没封账时是 `None`）。

    取的是一行上的 `sealed_at` —— 同一次封账写下的行时刻相同，取最大值只为确定性。
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
    （ADR 0113）。上一月还没有已封账的行时退回读**本月这一行的期初余额** —— 那是人工
    录入一次的「上月末余额」。两个都没有就是 0（新员工从零开始）。
    """
    if previous_row is not None and previous_row.get("sealed_at"):
        return float(previous_row.get("closing_balance") or 0.0)
    if current_row is not None and current_row.get("opening_balance") is not None:
        return float(current_row["opening_balance"])
    return 0.0
