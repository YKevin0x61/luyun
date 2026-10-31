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
  到」，不是「待补」。**入职日期之前的那些天也是 `None`**（2026-10-29 用户裁定：14 号
  入职的人，1–13 号不该是「待补」）—— 判据同 `_day_state`，只比到天。
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

* **出勤天数**：按**排班**数 —— 本月「排了班、又没标休」的天数，**今天之后已经排了班
  的日子也算**（2026-10-29 用户裁定「按排班预结算」；未来没排班的天没有排班行，不算）。
  **标了「休半天」的那天算 0.5 天**（票 07，ADR 0114）。
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
未来一律拒；已经封过的月再封一次也拒，一个字都不改已写下的那一行。

票 09 让那一笔账**可以人工定稿**：期末存假（本月剩余）在当月未封账前随时可填可改
（月级写口的 `closing_balance`），填了就以它为准。于是卡片上的 `leave.balance` 是
**有效值**（手填 ?? 公式），另给 `balance_computed`（公式值 = 池 − 本月自然天数，页面上
的「系统建议」）与 `balance_is_manual`；封账写的是**有效值**（有手填值就写手填值，没有
就照旧写公式值），封过之后只拒 `closing_balance` 这一个字段（409 `month_sealed`），
另外几个手填列照旧可写（05 的裁定）。**不加新列、不出迁移** —— 就是 `0027` 那一列
（`closing_balance`）：人工填过就是它，没填过就是 `NULL`（读取时才算公式值，不落库）。
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
from services.business_day import current_workbench_date
from services.holiday_calendar import HolidayCalendar, HolidayError
from services.identity import EmployeeAccounts, EmployeeAccountsError, serialized_write
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
        """当前工作台日（**自然日，00:00 切**，北京时）—— 与 `staff_assignments.business_date` 同一个词。

        2026-11-01 之前这里是 06:00 的营业日，于是「每月 1 日 06:00 之前考勤表停在上一月」
        （GLOSSARY 与 ADR 0112 都按那条写的）。改成自然日后，每月 1 号 00:00 就翻月。
        字段名 `business_date` 不变，口径见 ADR 0125。
        """
        return current_workbench_date(self._now_dt())

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

    async def _leave_deductions(self, month: str) -> dict[int, int]:
        """那个月**已经批下来**的补钟抵扣天数：``{员工号: 天数}``（ADR 0124）。

        只数 `approved` —— 待批 / 驳回 / 撤回 / 作废都不减少存假（与净时长那条口径一致：
        「有单子等着批」是审批队列里的事，不是已经用掉的假）。多次抵扣在同一张表里是
        多行，`SUM(days)` 合起来才是这个人当月用掉的天数。

        缺 0042 那张表时**回空字典**而不是 `not_migrated`：抵扣是新功能，老库先跑着，
        考勤表与封账不能因为这张表还没建就整页 503。表一旦建好，下一次读就自动生效。
        """
        try:
            cur = await self._conn.execute(
                """SELECT employee_id, SUM(days) AS days FROM overtime_leave_deductions
                   WHERE "month" = ? AND status = ? GROUP BY employee_id""",
                (month, "approved"),
            )
            rows = await cur.fetchall()
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
            await self._rollback_quietly()
            return {}
        return {
            int(dict(row)["employee_id"]): int(dict(row)["days"] or 0) for row in rows
        }

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

        形状不动（**两格月份**）：只想读一个月（票 05 写完的回执）时两格给同一个月份 ——
        少一个调用形状，票 04 的封账那一处也不用跟着改。

        四个手填列（`shift_name` / `lodging` / `borrowed_store` / `resigned`，票 05）
        与假期那几列一起读出来 —— 读的是同一行，不再打第二个查询。写这一行的是封账
        （票 04，`closing_balance` / `sealed_at`）与手填列（票 05，那四列）。缺表
        （0027 没应用）时照旧走 `not_migrated` 那条出口。
        """
        cur = await self._conn.execute(
            """SELECT employee_id, "month", opening_balance, closing_balance, sealed_at,
                      shift_name, lodging, borrowed_store, resigned
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
        self, employee_id: int, values: dict[str, Optional[Any]]
    ) -> dict[str, Any]:
        """把点名的几列写在本月那**一行**台账上（票 05 的四个手填列 + 人工期初 + 票 09 的
        期末存假）。返回写完之后那四个手填列。

        `values` 里**只放点名要改的那几列**（键取自 `LEDGER_WRITE_FIELDS`）：没点名的列一个都
        不碰 —— 这就是「只更新自己那几列」。点名成 `None`（手填列空串也一样）是**清空**：存
        NULL，与从没填过同义（读回来照旧退回「本月班次」那条默认；期初退回 0；期末存假退回
        公式值）。数值那两位在 SQL 里是 `DOUBLE PRECISION`：可为负（欠班）、可带 .5。

        **人工期初（`opening_balance`）**是故事 35 的那一次手工录入：写的是**本月这一行**，
        与手填列同一条语句、同一条 upsert，于是「一次点名一列」的纪律对它也成立。

        **期末存假（`closing_balance`，票 09）**是那一对里的另一半：当月未封账前随时可填可
        改，填了卡片上的 `leave.balance`（有效值）与导出件的「存休天数」就以它为准；封账把
        它定稿（有手填值就写手填值，没有就写公式值）。它落在**票 04 封账也写的那一列**上，
        所以多一道闸：那一行的 `sealed_at` 非空而本次点名了它 → `month_sealed`（HTTP 409），
        判在写之前，被拒的那一次一个字节都不动。**只锁这一列** —— 同一次点名别的字段照旧
        放行（05 的裁定：封账定的是存假那一笔账，不是整行只读）。

        缺表（0027 没应用）时与读侧同一个出口：`AttendanceError("not_migrated")`，
        HTTP 层翻成 503 点名那个脚本 —— 店长该看到的是「去做数据库迁移」，不是一个 500。

        台账那一行是**票 04 的封账、票 05 的手填列与人工期初、票 09 的期末存假**共用的一行
        （`UNIQUE(员工, 月份)`）：整行覆盖会把别人写下的数抹掉，所以 INSERT 时只写
        `employee_id` / `month`（别的列留给各自的写入口），冲突时**只有点名的那几列进
        `SET`**，没点名的列（含 `sealed_at`）一个都不在语句里 —— 哪怕这一次是新建那一行
        （`DO UPDATE` 因此也**不更新** `created_at`，那一行是什么时候建的仍然说得清）。

        **写之前先认人**（返工 ④）：`employee_id` 上得真有一个员工，否则报
        `unknown_employee` —— 不校验的话，一个手搓的 id 会在台账里建出一行**孤儿**
        （`sheet()` 念的是花名册，那一行永远不显示，只有 Admin 数据浏览器看得见）。
        逐日那条写口（排班的单日覆盖）本来就是这么校验的，这一条对齐它。
        """
        # 先认人再谈写什么：「这个 id 上有没有人」比「你点名了哪几列」更根本。
        await self._require_employee(employee_id)
        fields = [field for field in LEDGER_WRITE_FIELDS if field in values]
        if not fields:
            raise AttendanceError("nothing_to_write")
        month = self.today()[:7]
        # **封过账的那一行只锁期末存假这一格**（票 09 的口径，05 的裁定不变）：本次点名了
        # `closing_balance` 而那一行的 `sealed_at` 非空就拒（409 `month_sealed`），只点名
        # 别的字段照旧放行 —— 另外几列（本月班次 / 是否住宿 / 借调门店 / 是否离职）与期初
        # 都是系统推不出来的手填值，封账定的是存假那一笔账，不是整行只读。
        # 判在**写之前**（只读一次台账），被拒的那一次一个字节都不动。
        if "closing_balance" in fields and int(employee_id) in await self._sealed_employee_ids(
            month
        ):
            raise AttendanceError("month_sealed", month)
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
                    *(_ledger_value(field, values[field]) for field in fields),
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
        ledger = await self._ledger_rows((month, month))
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

        rows, half_rests = await self._assignment_rows(first, until)
        totals, recorded = await self._month_entries(first, until)
        # 还没批的加班 / 补钟：只标色，不进净时长、也不进存假（故事 14）。
        pending = await self._pending_entries(first, until)
        # 本月已经批下来的**补钟用存假抵扣**天数（ADR 0124）：`{员工号: 天数}`。
        # 缺 0042 那张表时回空字典 —— 抵扣是新功能，不该让考勤表整体 503。
        deductions = await self._leave_deductions(month)
        # 当月的法定节假日（补休要用）。日历缺表时与缺排班表一样报 `not_migrated`。
        holidays = await self._statutory_holidays(month)
        # 本月与上一月的台账行：算「上月存假」（已封账的结余 / 人工期初）。
        previous_month = (start - timedelta(days=1)).isoformat()[:7]
        ledger = await self._ledger_rows((previous_month, month))
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
            # 这个人当月标了「休半天」的那几天（票 07）：格子上写出来、例休折算按 0.5 天。
            their_half_rests = half_rests.get(employee_id, set())
            hire_date = employee.get("hire_date") or None
            # 本月那一行上的**期末存假**（票 09）：台账里的原始值，没填过 / 清空 = `None`。
            # 有它就以它为准（有效值），没有就照公式值；卡片另给 `closing_balance_stored`
            # 给前端判「填过没有」（照 `shift_name_stored` 的先例）。
            stored_closing_balance = _ledger_number(
                ledger.get((employee_id, month)), "closing_balance"
            )
            employees.append(
                {
                    "id": employee_id,
                    "name": employee.get("name") or "",
                    "job_title": employee.get("job_title") or "",
                    "base_salary": employee.get("base_salary"),
                    "seniority_bonus": employee.get("seniority_bonus"),
                    # 入职日期落在当月 = 新入职；没填入职日期 = 留空（`None`），不是「不是新来的」。
                    "new_hire": None if not hire_date else str(hire_date)[:7] == month,
                    # 离职（2026-10-29 取代「停用」）：`resigned` 是**到今天为止**算不算已离职
                    # （判据在 `services/identity/accounts.py` 的 `resigned_today`），
                    # `resigned_at` 是原始日期（`''` = 已离职、日期待补）。
                    "resigned": bool(employee.get("resigned")),
                    "resigned_at": employee.get("resigned_at"),
                    # 净时长（ADR 0124）：已批的补钟抵扣加回来 —— 被抵掉的那部分时间按
                    # 休假算、不再欠班，与加班账单（`monthly_stats`）同一条口径。
                    "net_hours": (
                        totals.get(employee_id, 0)
                        + deductions.get(employee_id, 0) * 17
                    )
                    / 2,
                    # 这一页的逐日改格（票 06）能挑的班次：**启用中的**那一份。
                    # 每个员工带一份，前端不用再打第二个接口，也就不会有两份会漂的名单。
                    "shifts": shifts,
                    # 还没批的加班 / 补钟（故事 14）：页面拿 `pending_count` 标色、拿
                    # `pending_hours` 说清还有几小时等着批；它们**不进** `net_hours`。
                    "pending_hours": pending.get(employee_id, {}).get("half_hours", 0) / 2,
                    "pending_count": pending.get(employee_id, {}).get("count", 0),
                    # 假期台账（票 03）：这一块全是**暂算值**，当月只算不落库。
                    # 票 09 在算式外面盖了一层**人工定稿**：台账本月那一行填过
                    # `closing_balance` 就以它为准，`balance` 因此是**有效值**
                    # （手填 ?? 公式），公式值另给 `balance_computed`。
                    "leave": _with_manual_balance(
                        _leave_terms(
                            today=today,
                            theirs=theirs,
                            half_rest_days=their_half_rests,
                            holidays=holidays,
                            employee=employee,
                            # **本月在岗天数**（2026-10-29）：入职当月从他入职那天算起，
                            # 不是整月自然天数 —— 见 `_on_duty_days`。
                            month_days=_on_duty_days(
                                start, len(days), hire_date, employee.get("resigned_at")
                            ),
                            previous_balance=_previous_balance(
                                ledger.get((employee_id, previous_month)),
                                ledger.get((employee_id, month)),
                            ),
                            # 本月经补钟抵扣（ADR 0124）：从余额里减，池不动。
                            deducted_days=deductions.get(employee_id, 0),
                        ),
                        closing_balance=stored_closing_balance,
                    ),
                    "days": [
                        {
                            "business_date": day["business_date"],
                            # 入职之前的那些天与「今天之后」一样留空（2026-10-29）：
                            # 不是待补 —— 那几天这个人还不在店里。
                            "state": _day_state(
                                today,
                                day["business_date"],
                                theirs,
                                hire_date,
                                employee.get("resigned_at"),
                            ),
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
                    # 「本月班次」的**原始**值（返工 ③）：台账上真写了什么，没填过（或清空）
                    # 就是 `None`。上面那个 `shift_name` 一个字没改 —— 导出件的 A 列读的
                    # 仍是它；页面上的输入框改绑这一位，一次「只改了是否住宿」的保存才不会
                    # 把那条众数默认固化成「他定的值」。
                    "shift_name_stored": _stored_shift_name(ledger.get((employee_id, month))),
                    # **人工期初**（故事 35）：上线当月由超管手工给一次的「上月末余额」。
                    # 读的是**本月那一行**的 `opening_balance`（票 03 的裁定），写口是那条
                    # 月级写口（`set_manual_columns`）。它与 `leave.previous_balance` 是两件
                    # 事：后者只在上一月**还没封账**时才读它（封过账就以封下的结余为准）。
                    "opening_balance": _ledger_number(
                        ledger.get((employee_id, month)), "opening_balance"
                    ),
                    # **期末存假（本月剩余）的原始值**（票 09）：台账上真写了什么，没填过
                    # （或清空）就是 `None`。与 `leave.balance`（有效值）刻意分开 —— 页面上
                    # 那一格改绑这一位，没填过时输入框就是**空**的，公式值只出现在
                    # 「系统建议」那句话里（`leave.balance_computed`）。
                    "closing_balance_stored": stored_closing_balance,
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
    async def leave_summary(self, employee_id: int, month: Optional[str] = None) -> dict:
        """**某个人某个月**的存假一块（员工端整月页那张卡，2026-10-31 用户裁定）。

        `sheet()` 是「当月 × 全店」的管理端读口；这一条是「任意月 × 一个人」的员工
        读口 —— 整月页能翻月（`stepMonth` 只挡未来），所以月份是参数，但**接口上没有
        `employee_id`**：员工会话读得到的只有自己，口径同 `/api/scheduling/me/month`。

        与卡片同一条算式（`_leave_terms` + `_with_manual_balance`）：

        * 有效值 = 超管手填的 `closing_balance`（那个月封过账 / 当月填过期末）优先，
          否则公式值 —— 员工与店长看到的是同一个数，不另起一套「员工版口径」；
        * `deducted_days` = 那个月已经批下来的补钟抵扣天数（ADR 0124），**已从余额里减**；
        * `provisional` = 那个月还没封账：「这个数还会变」。当月恒为 `True`（当月永远不封）。

        过去月份把「今天」顶到那个月最后一天再算（与 `seal()` 的 `as_of` 同一口径）——
        生日假与年假按 `month` 判，不拿今天的年月去给历史月算假。
        """
        target = _validated_month(month or self.today()[:7])
        start = date.fromisoformat(f"{target}-01")
        days_in_month = monthrange(start.year, start.month)[1]
        first, until = start.isoformat(), _next_month(start).isoformat()
        month_end = (start + timedelta(days=days_in_month - 1)).isoformat()
        # 未来月按今天算（排班还没铺，数只是暂算）；过去月按月末算（与封账同口径）。
        today = self.today()
        as_of = min(today, month_end)
        before = _previous_month(target)

        employee_id = int(employee_id)
        roster = await EmployeeAccounts(
            self._write_lock_owner, now=self._now
        ).list_roster()
        employee = next(
            (item for item in roster if int(item["id"]) == employee_id), None
        )
        if employee is None:
            raise AttendanceError("unknown_employee", "unknown_employee")

        rows, half_rests = await self._assignment_rows(first, until)
        holidays = await self._statutory_holidays(target)
        ledger = await self._ledger_rows((before, target))
        deductions = await self._leave_deductions(target)
        hire_date = employee.get("hire_date") or None
        stored = ledger.get((employee_id, target))
        leave = _with_manual_balance(
            _leave_terms(
                today=as_of,
                theirs=rows.get(employee_id, {}),
                half_rest_days=half_rests.get(employee_id, set()),
                holidays=holidays,
                employee=employee,
                month_days=_on_duty_days(
                    start, days_in_month, hire_date, employee.get("resigned_at")
                ),
                previous_balance=_previous_balance(
                    ledger.get((employee_id, before)),
                    stored,
                ),
                month=target,
                deducted_days=deductions.get(employee_id, 0),
            ),
            closing_balance=_ledger_number(stored, "closing_balance"),
        )
        sealed_at = stored.get("sealed_at") if stored is not None else None
        return {
            "employee": {"id": employee_id, "name": employee.get("name") or ""},
            "month": target,
            "today": today,
            "as_of": as_of,
            "days_in_month": days_in_month,
            # 还没封账 = 这个月的数还会随着排班与抵扣变（当月恒为 True）。
            "provisional": not sealed_at,
            "sealed_at": sealed_at,
            "previous_month": {
                "month": before,
                "sealed_at": _sealed_at(ledger, before),
            },
            "leave": leave,
        }

    async def leave_balance_days(self, employee_id: int, month: Optional[str] = None) -> float:
        """某个人某个月的**有效存假天数**（扣过已批抵扣、含人工定稿）。

        给补钟抵扣那条链路用（`OvertimeLedger` 在提交与审批时校验「抵得起吗」）：
        它是同一条算式的一个薄封装，不另写一份余额 —— 两个数一旦分家，员工看到的余额
        与能抵的上限就会对不上。
        """
        data = await self.leave_summary(employee_id, month)
        return float(data["leave"]["balance"])

    @_needs_migration
    async def ensure_day_editable(self, business_date: Any) -> str:
        """逐日写口（票 06 的改格 / 票 07 的休半天）**共用**的三道服务端闸。

        这一页只算当月、只记已经发生的班。两条写路各自的旧实现各漏了一部分：
        `set_half_rest` 只挡了非当月，排班那层的单日覆盖**能写未来日子、也能写已经封账的
        上月**。三道闸所以统一在这里，路由在分派到任何一条支路**之前**先过它：

        * **非当月**（`out_of_month`）：这一页不提供月份选择，别的月份一个格子都不显示；
        * **今天之后**（`future_day`，故事 45）：「还没到」不是「待补」，更不该被填成事实；
        * **那个月已经有封账行**（`month_sealed`，故事 33）：封过的月只读 —— 它里面每一格
          都已经结过账，再改它等于让封下的结余与现状各说各话。

        **判序把封账放在最前**：上月既非当月又已封账时，店长该看到的是更具体的那一句
        「封过的月只读」，而不是泛泛的「只算当月」。日期格式照旧是 `invalid_business_date`
        （同 `_require_business_date`）。返回规整过的营业日，调用方不用再解析一次。
        """
        today = self.today()
        day = _require_business_date(business_date)
        if await self._sealed_rows(day[:7]):
            raise AttendanceError("month_sealed", day[:7])
        if day[:7] != today[:7]:
            raise AttendanceError("out_of_month", today)
        if day > today:
            raise AttendanceError("future_day", day)
        return day

    @_needs_migration
    @serialized_write
    async def set_half_rest(
        self, employee_id: int, business_date: str, half_rest: bool = True
    ) -> dict:
        """把某人某一天标成「休半天」/ 取消（票 07，ADR 0114）。

        2026-10-29 起它**不是唯一的门**：排班周表的「休半天」笔走批量端点
        `action=half_rest`，与这里写同一个 `half_rest` 标志、同一句校验（那天得有
        结果行且班次非空），见 ADR 0122。两处都是「只改标志」这一种终态。

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
        写下去的是那一行 `closing_balance` 的**定稿值**：那个月手填过期末存假（票 09）就以
        手填值为准，没填过就照旧写公式值（本月结余 = 池 − 本月自然天数，04 的既有行为）；
        `sealed_at` 与它一起落库。**行里别的列一个字都不动** —— 期初（票 03 的人工录入）与
        手填列（票 05）是别人写下的真相，封账只加自己那一份。

        三道闸：

        * **只有已经过完的月能封**（`month_not_finished`）：当月与未来一律拒 —— 这个月的
          实际出勤、例休、补休都还会变，封了就是把暂算值当定稿。
        * **封过的月不再封**（`already_sealed`）：整月拒，已写下的那一行一个字都不改
          （重算一遍历史只会得到另一个数，见 ADR 0113）。
        * 缺表（0027 没应用）照旧走 `not_migrated`。

        **名单 = 那个月有记录的人**（排过班或登过加班，即 `sheet()` 的 `on_record`）——
        与卡片那条「在职的也在表上」的口径**刻意不同**：卡片说的是「现在谁在职」，
        封账说的是「那个月谁有账」。一个当前在职、那个月却零排班零加班的人（9 月入职的
        新人、整月请假的老人）在卡片上要看得见，但**不该被那个月封上一笔「出勤 0 天」的
        负数**——封下去就是永久冻结的假账。封的算式与 `sheet()` 同一条：那个月的实际出勤、
        当月法定节假日补休、生日假、年假与**再上一月**的存假。所以封下的数就是那个月
        卡片上那张账。

        写入用 `ON CONFLICT ... WHERE sealed_at IS NULL` 兜并发：两个请求同时走到这里时
        后一个不覆盖前一个。`sealed_employees` 报的是**这次真落库的行数**（被那一句跳过
        的行不计）—— 它是「封了几个人」的答案，不是「循环转了几圈」。
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
        # 封的那个月已经批下来的补钟抵扣（ADR 0124）：封账写下的余额必须已经减过它，
        # 否则封完账抵扣就凭空消失（下月起「上月存假」按封下的数走，没人再算得回来）。
        deductions = await self._leave_deductions(target)

        sealed = 0
        for employee in roster:
            employee_id = int(employee["id"])
            # 名单只认**那个月有记录的人**（`on_record` = 排过班或登过加班）。
            # `_is_on_the_sheet` 的「在职也进表」是**卡片**的口径，封账不跟它：
            # 「现在在职」的人那个月可能一条记录都没有，给他封一笔负数就是永久冻结的假账。
            if employee_id not in on_record:
                continue
            leave = _with_manual_balance(
                _leave_terms(
                    today=as_of,
                    theirs=rows.get(employee_id, {}),
                    # 封账与当月卡片同一套算式：标了「休半天」的那天算 0.5 天出勤（票 07）。
                    # 少了这一句，封下来的存假会比卡片上多算半天。
                    half_rest_days=half_rests.get(employee_id, set()),
                    holidays=holidays,
                    employee=employee,
                    # 与 `sheet()` 同一条（2026-10-29）：入职当月按在岗天数算，不是整月 ——
                    # 两边差一个分母，封下来的数就会和那个月卡片上的对不上。
                    month_days=_on_duty_days(
                        start,
                        month_days,
                        employee.get("hire_date"),
                        employee.get("resigned_at"),
                    ),
                    previous_balance=_previous_balance(
                        ledger.get((employee_id, before)),
                        ledger.get((employee_id, target)),
                    ),
                    month=target,
                    deducted_days=deductions.get(employee_id, 0),
                ),
                # 票 09：那个月那一行上**手填过**期末存假就以它为准（先填 10 → 封账 → 10），
                # 没填过就是 `None`，照旧写公式值（04 的既有行为不破）。封的是**已经过完的**
                # 月，所以那一行还没封过（`already_sealed` 上面挡着）—— 手填值在这里就是定稿值。
                closing_balance=_ledger_number(
                    ledger.get((employee_id, target)), "closing_balance"
                ),
            )
            outcome = await self._conn.execute(
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
            # 数字只认**真落库的行**：上面那句 `ON CONFLICT ... WHERE sealed_at IS NULL`
            # 可能把这一笔整条跳过（那一行已经被别人封过），此时影响行数是 0 —— 无条件
            # `+1` 报出去的就是「循环转了几圈」，不是「写进去几个人」。
            sealed += 1 if (outcome.rowcount or 0) > 0 else 0
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

    async def _sealed_employee_ids(self, month: str) -> set[int]:
        """那个月**已经封过账**的员工号（空集 = 那个月还没封过 / 谁都没封）。

        `set_manual_columns` 用它判「期末存假那一格锁没锁」（票 09）：「那一行封了吗」只有
        一个判据 —— `sealed_at` 非空，与逐日改格的 `ensure_day_editable` 同源（同一句
        `WHERE sealed_at IS NOT NULL`）。
        """
        return {int(row["employee_id"]) for row in await self._sealed_rows(month)}

    async def _require_employee(self, employee_id: int) -> None:
        """这个 id 上得真有一个员工，否则 `unknown_employee`（返工 ④）。

        走公共层的 `get_profile_row`：找不到人时它抛 `employee_not_found` —— 员工档案的
        真相只有一个出处（同排班那几处都从公共层念人）。这里翻成考勤这一层的
        `unknown_employee`，HTTP 层映射成 **404**，与排班（`api/scheduling.py`）和
        加班（`api/overtime.py`）里同名 code 的状态码一致。

        停用的、待批准的员工都算「存在」：这里挡的是**压根没有这个人**，不是「这个人
        现在能不能上考勤表」（那几个手填列本来就是给当月离职的人填的）。
        """
        try:
            await EmployeeAccounts(self._write_lock_owner, now=self._now).get_profile_row(
                int(employee_id)
            )
        except EmployeeAccountsError as exc:
            raise AttendanceError("unknown_employee", str(employee_id)) from exc


# 卡片上那四个**手填**列（票 05），列名与 `0027_attendance_ledger.sql` 一一对应：
# 系统里没有这几个字段（是否住宿 / 借调门店 / 是否离职在花名册里压根没有），所以由超管
# 在卡片上填一次、存在**月度考勤台账**当月那一行上，只进导出件与卡片，**不写回员工档案**。
# 2026-10-29：**「是否离职」退役** —— 离职改成花名册上的状态（`resigned_at`），导出件
# 直接从那里派生。台账上那一列还在（迁移只做加成性变更），但写口与读口都不再碰它。
MANUAL_FIELDS = ("shift_name", "lodging", "borrowed_store")

# 那条**月级写口**能点名的列（票 05 的四个手填列 + 修 ② 的 `opening_balance` +
# 票 09 的 `closing_balance`）。
# 「人工期初」是故事 35 让超管在**上线当月手工给一次**的「上月末余额」—— 它写的是
# **本月那一行**的 `opening_balance`（票 03 的裁定：人工期初读的就是本月这一行），
# 所以并进同一条 upsert：一次点名只写点名的列，别的手填列一个都不碰（`_ledger_value`
# 只管值的归一）。
# 「期末存假（本月剩余）」（票 09）走**同一条** upsert：写的是本月那一行的
# `closing_balance` —— 当月未封账前随时可填可改，填了就以它为准（有效值）。
# 它落在票 04 封账也写的那一列上：封账把当时的手填值（没有就照公式值）定稿，
# 所以**封过账的那一行只拒这一列**（`set_manual_columns` 里那一道闸）。
LEDGER_WRITE_FIELDS = MANUAL_FIELDS + ("opening_balance", "closing_balance")

# 台账上那几位**数字**列（可为负、可带 .5，`None` = 清空）。手填列是自由文本，不走这里。
_NUMERIC_LEDGER_FIELDS = ("opening_balance", "closing_balance")


def _manual_columns(row: Optional[dict], *, default_shift_name: Optional[str]) -> dict[str, Any]:
    """卡片上那四个手填列（票 05）。``row`` 是这个人**本月**台账那一行（没有就是 `None`）。

    * **本月班次**：台账里填过就用填的；没填过（或清空了 —— 「从没填过」与「清空」同义）
      退回 ``default_shift_name`` —— 当月众数，并列取班次显示顺序；当月一条排班都没有的
      人两个都没有，留空（`None`），不是报错。
    * **是否住宿 / 借调门店**：台账里有什么给什么，没有就是 `None`（页面上
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


def _stored_shift_name(row: Optional[dict]) -> Optional[str]:
    """台账那一行上「本月班次」的**原始**值（没有那一行 / 没填过 / 清空 = `None`）。

    与 `_manual_columns` 的 `shift_name` 刻意分开：那是「这一格该显示的值」（没填过时是
    当月众数），这是「他自己到底填过什么」。卡片把这一位给页面上的输入框 —— 输入框读
    `shift_name` 的话，店长只改「是否住宿」再点保存，就会把那条**默认值**当成他填的值
    写进台账，从此再也不跟排班变（返工 ③）。**导出件的 A 列读的仍是 `shift_name`**：
    那一格要的是「该显示什么」。空串按没填（`_text` 的口径，同那四个手填列）。

    写回执（`set_manual_columns`）里不给这一位：回执说的是**这一次写的结果**，那一次
    点名写了什么调用方手里就有，两位在回执里必然相等。
    """
    return _text(row.get("shift_name")) if row else None


def _text(value: Any) -> Optional[str]:
    """手填列的文本：空串 / 空白一律当没填（`None`）。不是字符串的原样转成字符串。"""
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text.strip() or None


def _ledger_value(field: str, value: Any) -> Any:
    """写台账那一行时把一位值归一：手填列是文本（空串 = 清空 = NULL），
    `opening_balance` / `closing_balance` 是数字（可为负、可带 .5，`None` = 清空）。

    数字那两位的**值域**由 HTTP 层的 `Optional[float]` 把着（`ManualColumnsRequest`），
    这里只管把「清空」与「一个数」分开 —— 手填列那几位是自由文本，服务端不做值域校验
    （票 05 的裁定），存假那两位照旧只想拿到一个数，拿到别的就是调用方的错。
    """
    if field in _NUMERIC_LEDGER_FIELDS:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise AttendanceError(f"invalid_{field}", str(value)) from exc
        # **非有限数一律拒**（NaN / ±Infinity）：HTTP 层的 `Optional[float]` 认它们，
        # PostgreSQL 的 DOUBLE PRECISION 也收它们，可 `sheet()` 的 JSON 序列化用
        # `allow_nan=False` —— 一个 NaN 落库就是整张考勤表永久 500，而页面上清不掉
        # （只能进库手改）。所以拦在写台账之前，code 与「给了非数字」共用同一句文案。
        if not math.isfinite(number):
            raise AttendanceError(f"invalid_{field}", str(value))
        return number
    return _text(value)


def _ledger_number(row: Optional[dict], column: str) -> Optional[float]:
    """台账那一行上的一个数字列（没有那一行 / 没填过 = `None`，**不是 0**）。

    「从没给过期初」与「期初是 0」是两件事：页面上一格留空、`0` 是一个真写下的数。

    非有限数（NaN / ±Infinity）按「没填」读：写口已经拒它们了，这里兜的是**存量坏值**
    —— 老版本写进去的那一行不该让整张表（以及导出件）永远 500。
    """
    if not row or row.get(column) is None:
        return None
    number = float(row[column])
    return number if math.isfinite(number) else None


def _is_on_the_sheet(employee: dict, on_record: bool) -> bool:
    """这个人进不进当月的表。

    在职（已批准、未停用）的一律在；此外**当月有排班或加班记录**的也在 —— 当月离职的
    人那天还有班，漏掉他就漏结。反过来，一个早已停用、当月又一条记录都没有的人不在表上。
    """
    return on_record or (
        bool(employee.get("approved")) and not employee.get("resigned")
    )


def _day_state(
    today: str,
    business_date: str,
    theirs: dict[str, Any],
    hire_date: Any = None,
    resigned_at: Any = None,
) -> Optional[str]:
    """那天这个人是什么状态；今天之后、**入职之前**都是 `None`（留空，不是「待补」）。

    2026-10-29 用户报：14 号才入职的人，1–13 号在卡片上全是「待补」—— 那几天他还没来，
    不该让店长去补一排不存在的班，也不该进「待补排行」。入职日期只比到天（库里读出来
    可能是 `date`，也可能是 `YYYY-MM-DD` 串，`str()[:10]` 两种都认）。
    """
    if business_date > today:
        return None
    if hire_date and business_date < str(hire_date)[:10]:
        return None
    # 离职日**之后**留空（离职日当天还算上班，与登录/排班同一条判据）。空串是「已离职、
    # 日期待补」—— 不知道哪天走的，就不剪历史，照旧按三态显示。
    if resigned_at:
        left = str(resigned_at)[:10]
        if left and business_date > left:
            return None
    if business_date not in theirs:
        return STATE_TODO
    return STATE_REST if theirs[business_date] is None else STATE_WORK


def _on_duty_days(
    month_start: date, month_days: int, hire_date: Any, resigned_at: Any = None
) -> int:
    """这个人**本月应在岗**的自然天数（2026-10-29 用户裁定：入职当月从中入职那天算起）。

    假期算式里的「本月自然天数」原来一律是整月天数。可入职当月的人前半段还没来，
    减去整月就等于把他根本不用上的那些天当成了欠班 —— 14 号入职、还没排班的人
    存假凭空是 `−31 天`。所以：

    * 入职日期为空 / 早于月初（老员工）：整月天数，口径一个字不变；
    * 入职日期落在本月：入职日到月底的天数（14 号入职 = 当月剩余天数）；
    * 入职日期在月末之后（还没到）：0 —— 那时不该有欠班；
    * **离职日期落在本月**（2026-10-29 一并）：截到离职日那天为止 —— 当月来又走的人，
      分母是「入职日到离职日」，不是整月。

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
    # 离职（2026-10-29）：截到离职日为止 —— 离职日**当天仍算在岗**（那天他还上班），
    # 与 `_day_state` 的「离职日之后留空」是同一条线。空串（日期待补）不剪：不知道哪天走。
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
    """卡片上那一块假期算式（票 03，ADR 0113 的口径）。**全是派生值，不落库。**

    ``theirs`` 是这个人当月的排班行（``{营业日: 班次 id 或 None}``）；``half_rest_days``
    是其中**标了「休半天」**的那几天（票 07，只含班次非空的日子）；``holidays`` 是
    当月法定节假日（`HolidayCalendar.days_in_month`，日历是唯一依据）；``employee`` 是
    花名册那一行（生日从身份证号派生、工龄奖与入职日期都在里面）。``month_days`` 是
    **本月在岗天数**（见 `_on_duty_days`：老员工 = 整月，入职当月 = 入职日起算）。

    * **出勤天数**按**排班**数（2026-10-29 用户裁定「按排班预结算」）：已过的日子 + 今天
      之后**已经排了班**的日子都算 —— 店长在周表给 30/31 排了早班，考勤表当月的出勤
      天数与存假就该跟着变，不用等到 30 号。**没排班的未来日子不算**（没有排班行）。
      封账算的是整月都过完的月份，两种口径同值。**标了「休半天」的那天算 0.5 天**
      （`GLOSSARY.md` 的月例休：「出勤半天算 0.5 天参与折算」）：月例休与存假都跟着
      这半个天走。未到日子的半天休也按 0.5 算（它是计划的一部分）。
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
    * **可用天数池** = 上面五项 + 上月存假；**本月出勤天数** = `min(池, 本月在岗天数)`；
      **存假** = `池 − 本月在岗天数`（可为负 = 欠班、可带 .5）。

      「本月在岗天数」是 `month_days` 这一位（2026-10-29 用户裁定）：老员工就是整月
      自然天数，**入职当月从他入职那天算起**（14 号入职 = 当月剩余的天数）—— 否则
      新入职的人的存假会把他没入职的前半段当成欠班（`_on_duty_days`）。

    **加班与补钟不参与这一套**（`GLOSSARY.md` 的存假：净时长单独结算）—— 唯一的例外是
    2026-10-31 用户裁定的**补钟用存假抵扣**（ADR 0124）：``deducted_days`` 是那个人那个月
    已经批下来的抵扣天数，它从**余额**里减（`balance = pool − month_days − deducted_days`），
    池本身不动 —— 抵扣用的是他攒下的假，不是让这个月的出勤多铺出一天。天数由
    `OvertimeLedger` 那张 `overtime_leave_deductions` 表给，这一层只减、不判该不该抵。

    `month` 是「算的是哪个月」—— `sheet()` 不给，就取 `today` 那个月（当月的卡片）；
    封账（票 04）算的是**上一个自然月**，于是它明说哪个月，并把 `today` 顶到那个月的
    最后一天（整月都过完了，那个月的每一天都算「已过」）。生日假与年假按 `month` 判，
    不跟 `today` 的月份 —— 否则封 8 月的账会拿 9 月的生日去给假。
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
        # 本月经补钟抵扣的天数（ADR 0124）：只从余额里减，池与明细保持原样 ——
        # 卡片上那九项说的是「这个月攒了多少」，抵扣是「用掉多少」。
        "deducted_days": float(deducted_days),
        # 池比自然天数还小的时候按池填（这个月剩下的假不够铺满），所以这里不能 clamp 到 0。
        "month_attendance_days": min(pool, float(month_days)),
        "balance": pool - month_days - float(deducted_days),
    }


def _with_manual_balance(
    terms: dict[str, Any], closing_balance: Optional[float]
) -> dict[str, Any]:
    """给算式的结果盖上**人工定稿**那一层（票 09）。``terms`` 就是 `_leave_terms` 的返回值。

    `_leave_terms` 只算**公式值**（池 − 本月自然天数）；人工那一位是本票加在它**外面**的
    一层覆盖：台账那一行填过 `closing_balance` 就以它为准，没填过就照公式值。覆盖刻意
    留在算式之外 —— 07 的半天休折算与 03 的口径用例因此仍然读得出「公式说的是什么」。

    返回的 dict 在原有键之外多两位（`balance` 的含义因此从「公式值」变成「**有效值**」，
    导出件的「存休天数」读的就是它，所以导出件自动跟着走）：

    * ``balance_computed`` —— 公式值（页面上的「系统建议」）；
    * ``balance_is_manual`` —— 这一次用的是不是手填值。

    ``closing_balance`` 是台账上的**原始**值（`None` = 没填过 / 清空 = 退回公式值）。
    """
    computed = terms["balance"]
    terms["balance_computed"] = computed
    terms["balance_is_manual"] = closing_balance is not None
    if closing_balance is not None:
        terms["balance"] = closing_balance
    return terms


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
