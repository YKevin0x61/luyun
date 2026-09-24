#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班：规则 → 未来 90 天的排班结果。

排班是**独立系统**（`spec.md` 的「分层」）：这里不 import 卫生（`services.hygiene.*`），
也不认识卫生的表名；它从公共层取「这个人是谁」（`services.identity`）、算自己的规则，
把结果**物化**进 `staff_assignments`。下游只读那份数据，两边靠数据对接而不靠调用。

三件事在这一层：

- **班次**（`staff_shifts`）：白班、夜班是**数据**不是常量，加第三个班次是改数据。
- **规则**（`scheduling_rules`）：一人一条轮转规则 —— `cycle` 的长度就是周期天数，
  每格是班次 id、`None` 表示休；`anchor_date` 是周期起点，相位由它和营业日之差算。
- **展开**（`expand`）：把规则铺成 `staff_assignments` 的行，滚动铺未来 90 天。

**已经写下的行不重算**：展开时窗口内哪天（哪个人）已经有行就跳过。所以「改规则只影响
今天以后」是这个做法本身的结果，不是额外加的一段逻辑 —— 过去那些行早就写在那儿了，
没人会去动它们（`spec.md` 的「展开」）。

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
from services.business_day import current_business_date, shift_business_date
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
    "DEFAULT_SHIFTS",
    "EXPANSION_DAYS",
    "KIND_MANUAL",
    "MAX_CYCLE_DAYS",
    "REST",
    "SOURCE_OVERRIDE",
    "SOURCE_RULE",
    "SchedulingError",
    "SchedulingStore",
]

# 排班窗口：从今天起铺这么多天（含今天）。
EXPANSION_DAYS = 90

# 排班结果这一行是谁写的。
SOURCE_RULE = "rule"
SOURCE_OVERRIDE = "override"

# 单日覆盖是谁写的（`scheduling_overrides.kind`）：本票唯一会写的是店长在月历上
# 点着改的。票 08 的请假、票 09 的换班各写各的值 —— 它们进的是同一张表
# （见 `migrations/pg/0007_scheduling_overrides.sql`），不用再出一次迁移。
KIND_MANUAL = "manual"

# 员工「今天」页往后看几天：今天、明天、后天、大后天（原型 A 的「往后三天」）。
# 窗口长度是服务层的数，前端不再写一份 —— 以后要改成「往后一周」只动这里。
MY_WINDOW_DAYS = 4

# 规则里「那天休」那一格。
REST = None

# 周期长度的上限。轮转是给人看的（「白白白夜夜休休」= 7 天），几十天已经荒谬；
# 定一个上限是为了让 `cycle` 这个 JSON 文本有界（输入校验，不是性能考虑）。
MAX_CYCLE_DAYS = 60

# 首次启动时放进班次表的默认两条（`migrations/pg/0005_scheduling.sql` 里也有同样的
# 两行，那是给「装完就看」的；见 `prepare()` 为什么还留了一手）。
DEFAULT_SHIFTS = (("白班", 10), ("夜班", 20))

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


class SchedulingError(ValueError):
    """排班层的输入错误。``code`` 是给 API 层用的稳定标识，不是给员工看的文案。"""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


def _require_business_date(value: Any) -> str:
    text = str(value or "")
    if not _DATE_RE.match(text):
        raise SchedulingError("invalid_business_date", "invalid_business_date")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise SchedulingError("invalid_business_date", "invalid_business_date")


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


def _rule_anchor(rule: dict) -> date:
    """规则的周期起点。

    `anchor_date` 是 TEXT 列，人工 SQL 能写进任何东西 —— 跟 `_decode_cycle` 一样，
    脏数据在这里变成一条明确的输入错误，而不是一路冒到接口层变成 500。
    """
    try:
        return date.fromisoformat(rule["anchor_date"])
    except (TypeError, ValueError):
        raise SchedulingError("invalid_anchor", "invalid_anchor")


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
        """当前营业日（06:00 切，北京时）。"""
        return current_business_date(self._now_dt())

    def _window(self) -> tuple[str, str]:
        """展开窗口：``[今天, 今天 + EXPANSION_DAYS - 1]``，两端都含。"""
        first = self.today()
        return first, shift_business_date(first, EXPANSION_DAYS - 1)

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
        # 班次表在、固定责任区表不在：半迁移（0005 应用了、0006 没应用）。启动照旧，
        # 但名单面板会 503，日志里先把该跑哪个脚本说清楚。
        try:
            await (await self._conn.execute("SELECT 1 FROM scheduling_zone_defaults LIMIT 1")).fetchone()
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
            await self._rollback_quietly()
            logger.error(
                "❌ scheduling_zone_defaults 表不存在：请在 Admin「系统更新 → 数据库迁移」"
                "应用 migrations/pg/0006_scheduling_zone_defaults.sql"
                "（缺它时排班的名单面板打不开，配不了固定责任区）"
            )
            return False
        if row is not None and int(dict(row)["n"] or 0) > 0:
            return True
        stamp = self._now_iso()
        for name, sort_order in DEFAULT_SHIFTS:
            await self._conn.execute(
                """INSERT INTO staff_shifts (name, sort_order, is_active, created_at, updated_at)
                   VALUES (?, ?, 1, ?, ?)""",
                (name, sort_order, stamp, stamp),
            )
        await self._conn.commit()
        logger.info("排班班次表为空，已放入默认班次 %s", "、".join(n for n, _ in DEFAULT_SHIFTS))
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
        sql = """SELECT id, name, sort_order, is_active FROM staff_shifts"""
        if not include_inactive:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY sort_order ASC, id ASC"
        cur = await self._conn.execute(sql)
        shifts = []
        for row in await cur.fetchall():
            mapping = dict(row)
            shifts.append({
                "id": int(mapping["id"]),
                "name": mapping["name"],
                "sort_order": int(mapping["sort_order"]),
                "is_active": bool(int(mapping["is_active"] or 0)),
            })
        return shifts

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

    # ── 责任区 ──────────────────────────────────────────────────────────

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
        """公共层的责任区名单。写锁跟着走 —— 它只读，但共用同一个连接。"""
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

    async def list_rules(self) -> dict[int, dict]:
        """``employee_id → {cycle, anchor_date}``；没配规则的人不在里面。"""
        cur = await self._conn.execute(
            "SELECT employee_id, cycle, anchor_date FROM scheduling_rules",
        )
        rules: dict[int, dict] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            rules[int(mapping["employee_id"])] = {
                "cycle": self._decode_cycle(mapping["cycle"]),
                "anchor_date": mapping["anchor_date"],
            }
        return rules

    @_needs_migration
    async def set_rule(
        self,
        employee_id: int,
        cycle: Any,
        anchor_date: Optional[str] = None,
    ) -> dict:
        """给一个人配轮转规则，并把**今天以后**的规则行重铺一遍。

        校验在锁外（都是读）；真正落地的三步 —— 写规则 → 删今天以后的旧行 → 重铺 ——
        在**一次**写锁、一次提交里做完，中途不会留下「规则改了、行还是旧的」。
        """
        employee_id = int(employee_id)
        normalized = self._normalize_cycle(cycle)
        roster = await EmployeeAccounts(self._write_lock_owner).list_roster()
        if employee_id not in {employee["id"] for employee in roster}:
            raise SchedulingError("unknown_employee", "unknown_employee")
        known = {shift["id"] for shift in await self.list_shifts()}
        for slot, shift_id in enumerate(normalized, start=1):
            if shift_id is not REST and shift_id not in known:
                # 说清是**第几格**：规则编辑页照这个数字就能指出错在哪一天
                # （票 04 的验收项）。`unknown_shift` 留给责任区那条路
                # （`set_zone_default`：给某人某个班次配区时选了个不存在的班次）。
                raise SchedulingError("unknown_shift_in_cycle", str(slot))
        anchor = self.today() if anchor_date in (None, "") else _require_business_date(anchor_date)

        await self._apply_rule(employee_id, normalized, anchor)
        return {
            "employee_id": employee_id,
            "cycle": normalized,
            "anchor_date": anchor,
        }

    @serialized_write
    async def _apply_rule(self, employee_id: int, cycle: list, anchor_date: str) -> None:
        await self._write_rule_row(employee_id, cycle, anchor_date)
        await self._delete_future_rule_rows(employee_id)
        await self._expand_rows(employee_id, {"cycle": cycle, "anchor_date": anchor_date})
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

    async def _delete_future_rule_rows(self, employee_id: int) -> None:
        """规则改了 → 今天以后那些「按规则写出来的行」作废重铺；过去一行不动。

        `source = 'rule'` 这个条件是为后面的单日覆盖留的：手改/请假写下的行
        （`override`）不归规则管，改规则不该把它们抹掉。
        """
        await self._conn.execute(
            """DELETE FROM staff_assignments
               WHERE employee_id = ? AND business_date >= ? AND source = ?""",
            (employee_id, self.today(), SOURCE_RULE),
        )

    @_needs_migration
    async def clear_rule(self, employee_id: int) -> None:
        """把一个人的规则拿掉，连今天以后的规则行一起（一次写锁、一次提交）。"""
        await self._clear_rule(int(employee_id))

    @serialized_write
    async def _clear_rule(self, employee_id: int) -> None:
        await self._delete_rule_row(employee_id)
        await self._delete_future_rule_rows(employee_id)
        await self._conn.commit()

    async def _delete_rule_row(self, employee_id: int) -> None:
        await self._conn.execute(
            "DELETE FROM scheduling_rules WHERE employee_id = ?",
            (employee_id,),
        )

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

        覆盖是**整天的快照**：这一天的班次和责任区由这次调用定下来 ——
        `scheduling_overrides` 记一条、`staff_assignments` 写一行（`source='override'`）。
        规则以后怎么变都不再动这一天（`_delete_future_rule_rows` 只删 `source='rule'`
        的行），想让它回到规则就撤掉覆盖（`clear_override`）。

        `is_rest=True` = 那天休，班次与责任区都要留空（同 `staff_assignments` 的口径：
        `shift_id IS NULL` 就是休）；否则 `shift_id` 必给。`zone_id` 不给时跟这个班次的
        **固定区**（`scheduling_zone_defaults`），跟展开时是同一条口径。

        校验在锁外（都是读）；写覆盖记录与写结果行在**一次**写锁、一次提交里。
        """
        employee_id = int(employee_id)
        day = _require_business_date(business_date)
        if day < self.today():
            # 过去不改（`spec.md`）：那时那天是什么样就是什么样，改它等于改历史。
            raise SchedulingError("past_day", "past_day")
        if day > self._window()[1]:
            # 窗口尽头之后是「还没铺到」。在那里写一行，月历上就会出现一个
            # 「规则还没算到、却已经有人上班」的格子 —— 那是另一件事（先配规则）。
            # 第二参填进那句提示的 `{}`：报**真实的窗口末日**，别把 `EXPANSION_DAYS`
            # 写死在文案里（那个数一改，店长看到的就是假话）。
            raise SchedulingError("beyond_window", self._window()[1])
        roster = await EmployeeAccounts(self._write_lock_owner).list_roster()
        if employee_id not in {employee["id"] for employee in roster}:
            raise SchedulingError("unknown_employee", "unknown_employee")

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

        await self._apply_override(employee_id, day, shift, zone)
        return {
            "employee_id": employee_id,
            "business_date": day,
            "shift_id": shift,
            "zone_id": zone,
            "is_rest": shift is REST,
        }

    @serialized_write
    async def _apply_override(
        self,
        employee_id: int,
        day: str,
        shift_id: Optional[int],
        zone_id: Optional[int],
    ) -> None:
        stamp = self._now_iso()
        cur = await self._conn.execute(
            """SELECT id FROM scheduling_overrides
               WHERE employee_id = ? AND business_date = ?""",
            (employee_id, day),
        )
        existing = await cur.fetchone()
        if existing is None:
            await self._conn.execute(
                """INSERT INTO scheduling_overrides
                       (employee_id, business_date, shift_id, zone_id, kind, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (employee_id, day, shift_id, zone_id, KIND_MANUAL, stamp, stamp),
            )
        else:
            # 再改一次就是**替换**这一天的样子（不是叠加）：`kind` 也跟着回到手改 ——
            # 请假改过的那天，店长又点着改了一次，那天现在就是店长定的。
            await self._conn.execute(
                """UPDATE scheduling_overrides
                   SET shift_id = ?, zone_id = ?, kind = ?, updated_at = ?
                   WHERE employee_id = ? AND business_date = ?""",
                (shift_id, zone_id, KIND_MANUAL, stamp, employee_id, day),
            )
        await self._write_day_row(employee_id, day, shift_id, zone_id, SOURCE_OVERRIDE, stamp)
        await self._conn.commit()

    @_needs_migration
    async def clear_override(self, employee_id: int, business_date: str) -> dict:
        """撤掉某人某天的覆盖，那天回到规则铺出来的样子（票 07 的验收项）。

        今天以后按**现在的规则**重算一遍；已经过去的日期只把覆盖记录摘掉、结果行不动
        —— 那天当时确实是被改过的，写下来的历史就是那样（`spec.md` 的「过去不改」）。
        """
        employee_id = int(employee_id)
        day = _require_business_date(business_date)
        await self._clear_override(employee_id, day)
        return {"employee_id": employee_id, "business_date": day}

    @serialized_write
    async def _clear_override(self, employee_id: int, day: str) -> None:
        await self._conn.execute(
            "DELETE FROM scheduling_overrides WHERE employee_id = ? AND business_date = ?",
            (employee_id, day),
        )
        if day >= self.today():
            await self._restore_day(employee_id, day)
        await self._conn.commit()

    async def _restore_day(self, employee_id: int, day: str) -> None:
        """把某一天按**现在的规则**重算回 `source='rule'`。不自己上锁、不提交。

        没有规则、或那天已经在窗口外：把这一行删掉，那天回到「还没铺到」——
        跟月历上窗口尽头的空格是同一个状态，不是「那天休」。
        """
        rule = (await self.list_rules()).get(employee_id)
        if rule is None or day > self._window()[1]:
            await self._conn.execute(
                """DELETE FROM staff_assignments
                   WHERE employee_id = ? AND business_date = ? AND source = ?""",
                (employee_id, day, SOURCE_OVERRIDE),
            )
            return
        shift_id = _cycle_shift(rule, day)
        zone_id = None
        if shift_id is not REST:
            zone_id = (await self._zone_defaults_for(employee_id)).get(shift_id)
        await self._write_day_row(
            employee_id, day, shift_id, zone_id, SOURCE_RULE, self._now_iso()
        )

    async def _write_day_row(
        self,
        employee_id: int,
        day: str,
        shift_id: Optional[int],
        zone_id: Optional[int],
        source: str,
        stamp: str,
    ) -> None:
        """把某人某天的结果行写成给定样子（有则改、无则插）。不自己上锁、不提交。"""
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
        else:
            await self._conn.execute(
                """UPDATE staff_assignments
                   SET shift_id = ?, zone_id = ?, source = ?, updated_at = ?
                   WHERE employee_id = ? AND business_date = ?""",
                (shift_id, zone_id, source, stamp, employee_id, day),
            )

    # ── 展开 ────────────────────────────────────────────────────────────

    @_needs_migration
    async def expand(self, employee_id: Optional[int] = None) -> int:
        """把规则铺成结果行，返回这次新写了多少行。

        一个人一次上锁、一次提交：第一次铺 90 天就是 90 行，不为此握着全局写锁
        （月历哪天有人第一次打开都会走到这里）。
        """
        rules = await self.list_rules()
        if employee_id is not None:
            key = int(employee_id)
            rules = {key: rules[key]} if key in rules else {}
        written = 0
        for emp_id, rule in rules.items():
            written += await self._expand_one(emp_id, rule)
        return written

    @serialized_write
    async def _expand_one(self, employee_id: int, rule: dict) -> int:
        written = await self._expand_rows(employee_id, rule)
        if written:
            await self._conn.commit()
        return written

    async def _expand_rows(self, employee_id: int, rule: dict) -> int:
        """铺窗口内还缺的那些天，返回写了几行。不自己上锁、不提交（调用方管）。

        只管「还缺的天」：已经存在的行（含单日覆盖写下的）一律不碰。
        """
        first, last = self._window()
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
            """SELECT business_date, shift_id, zone_id FROM scheduling_overrides
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

        `lead` = 第一格前面空几格（表头周日开头，周日=0）。`window_end` 之后的日子注定是空的 ——
        前端得说出来这是「还没铺到」，不是「那天没人上班」/「那天休」。
        """
        return {
            "month": start.isoformat()[:7],
            "first_date": start.isoformat(),
            "today": today,
            "lead": start.isoweekday() % 7,
            "window_end": self._window()[1],
            "days": days,
        }

    @_needs_migration
    async def month_calendar(self, month: str) -> dict:
        """一个月的月历：每天各班的**人数**（票 02 的验收项）。

        只数 `shift_id IS NOT NULL` 的行：休不是「上了某个班」，人数不该把它算进去。
        `overridden` 是那天有几行是**单日覆盖**写的（票 07）：月历靠它在格子上打个点，
        点开看「谁被改了、改了成什么」—— 它和人数走的是两条查询，「改成休」那种改动
        在人数里看不出来，在这个计数里看得出来。
        """
        start, next_month = _month_bounds(month)
        first = start.isoformat()

        cur = await self._conn.execute(
            """SELECT business_date, shift_id, COUNT(*) AS n FROM staff_assignments
               WHERE business_date >= ? AND business_date < ? AND shift_id IS NOT NULL
               GROUP BY business_date, shift_id""",
            (first, next_month.isoformat()),
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
            """SELECT business_date, COUNT(*) AS n FROM staff_assignments
               WHERE business_date >= ? AND business_date < ? AND source = ?
               GROUP BY business_date""",
            (first, next_month.isoformat(), SOURCE_OVERRIDE),
        )
        overridden: dict[str, int] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            overridden[mapping["business_date"]] = int(mapping["n"])

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
            })

        return {**self._month_frame(start, today, days), "shifts": shifts}

    @_needs_migration
    async def day_detail(self, business_date: str) -> dict:
        """某一天：各班**是谁、在哪个区**（月历点开那天看的就是这个）。

        键叫 `groups` 而不是 `shifts`：`/calendar` 的 `shifts` 是班次列表，这里每一组
        还带人数和名字，同名会让调用方以为形状一样。

        责任区读的是**结果行上的 `zone_id`**，不是现在的固定区配置：那天写下来是什么
        就是什么 —— 事后改固定区不该改写已经过去的日子（`spec.md` 的「过去不改」）。

        每个人带一个 `overridden`：这一行的 `source` 是 `override`（票 07 的单日覆盖），
        也就是「这天他跟规则不一样」。面板靠它标出来、并给出「撤销覆盖」这个动作。

        休的人不只给一个数：`off_people` 是跟 `groups` 同形的一份名单。他们也要能被点开
        —— 店长把某人改成休之后，得从那格再把他改回上班（或者撤掉那次改动）。
        """
        day = _require_business_date(business_date)
        names = await self._name_index()
        zone_names = await self._zone_name_index()

        cur = await self._conn.execute(
            """SELECT employee_id, shift_id, zone_id, source FROM staff_assignments
               WHERE business_date = ?""",
            (day,),
        )
        by_shift: dict[Any, list[tuple[int, Optional[int], bool]]] = {}
        for row in await cur.fetchall():
            mapping = dict(row)
            key = REST if mapping["shift_id"] is None else int(mapping["shift_id"])
            zone = None if mapping["zone_id"] is None else int(mapping["zone_id"])
            overridden = mapping["source"] == SOURCE_OVERRIDE
            by_shift.setdefault(key, []).append((int(mapping["employee_id"]), zone, overridden))

        def person(employee_id: int, zone: Optional[int], overridden: bool) -> dict:
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
        return {
            "business_date": day,
            "groups": groups,
            "total": sum(group["count"] for group in groups),
            "off_count": len(rest_ids),
            "off_people": [person(*item) for item in rest_ids],
            # 过去的日子不改写（口径 5）：这天的覆盖撤不掉 —— 前端据此把「撤销」收起来，
            # 而不是给一个按了没反应的按钮。
            "undoable": day >= self.today(),
        }

    @_needs_migration
    async def my_days(self, employee_id: int, days: int = MY_WINDOW_DAYS) -> dict:
        """员工自己的今天和往后几天：一天一行，带那天写下的班次与责任区。

        「今天」是排班自己那个 06:00 切日的今天（`self.today()`），跟月历、当日分工
        读的是同一个日期 —— 员工凌晨一点看到的和店长看到的必须是同一天。

        每人先单独补齐（只这一人，不是全店）：店长刚配完规则、月历还没人打开过，
        员工这一眼也得是对的。窗口尽头的天数就是「还没排」，不报错。

        没铺过的日子**也占一行**：`scheduled=False` 是「还没有你的班」，`scheduled=True`
        而 `shift_id=None` 是「那天休」。两者在员工页上是两句话，不能让调用方从
        「缺行」里去猜 —— 判据在服务层，前端只翻译。
        """
        await self.expand(employee_id)
        first = self.today()
        count = max(int(days), 1)
        cur = await self._conn.execute(
            """SELECT business_date, shift_id, zone_id FROM staff_assignments
               WHERE employee_id = ? AND business_date >= ? AND business_date <= ?
               ORDER BY business_date""",
            (employee_id, first, shift_business_date(first, count - 1)),
        )
        rows, shifts = await self._rows_and_shifts(cur)
        zone_names = await self._zone_name_index()

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
                "zone_id": zone_id,
                # 区名来自结果行上的 zone_id：那天写在行上的是哪个区就是哪个区，
                # 事后改固定区不改写过去（跟 `day_detail` 同一条口径）。
                "zone_name": None if zone_id is None else zone_names.get(zone_id),
            })
        return {"today": first, "days": out}

    @_needs_migration
    async def my_month(self, employee_id: int, month: Optional[str] = None) -> dict:
        """员工自己的一个月：一天一格，格子里是那天的班别（票 06，「今天」页点进整月）。

        跟 `month_calendar`（店长那一版数的是各班**人数**）不是一回事：这条只回答
        「我那天上什么班」。`scheduled` / `shift_id` / `shift_name` 跟 `my_days` 是
        同一套口径 —— 休（行在、`shift_id` 空）、还没铺到（没有行）、班次被删（行在、
        名字 None）三种状态在服务层分好，前端只翻成人话。

        格子只写班别、不带责任区：手机一行七格放不下「白班 · 案板」，责任区在「今天」
        页那张卡上（`spec.md` 留给第 6 步的那个待定项按这个口径定）。

        翻到过去的月份只显示已经铺过的日子（展开只往今天以后补，不回头），`window_end`
        之后的格子注定是「还没排」—— 跟店长月历同一个说法。
        """
        await self.expand(employee_id)
        start, next_month = _month_bounds(self.today()[:7] if month is None else month)

        cur = await self._conn.execute(
            """SELECT business_date, shift_id FROM staff_assignments
               WHERE employee_id = ? AND business_date >= ? AND business_date < ?
               ORDER BY business_date""",
            (employee_id, start.isoformat(), next_month.isoformat()),
        )
        rows, shifts = await self._rows_and_shifts(cur)

        today = self.today()
        days = []
        for day in _each_day(start, next_month):
            key = day.isoformat()
            row = rows.get(key)
            shift_id = None if row is None or row["shift_id"] is None else int(row["shift_id"])
            shift = shifts.get(shift_id) if shift_id is not None else None
            days.append({
                "business_date": key,
                "day": day.day,
                "is_today": key == today,
                "scheduled": row is not None,
                "shift_id": shift_id,
                "shift_name": None if shift is None else shift["name"],
            })

        return self._month_frame(start, today, days)

    @_needs_migration
    async def roster_with_rules(self) -> dict:
        """名单：全体花名册 + 每人当前那条规则 + 每人每班次的固定区。

        花名册来自**公共层**（`EmployeeAccounts.list_roster`），责任区名单也来自公共层
        （`ZoneDirectory.list_zones`，读的是卫生建的那张 `hygiene_zones`）；排班不自己去
        join `hygiene_employees` —— 表名是公共层的实现细节（`spec.md` 的「分层」）。
        """
        employees = await EmployeeAccounts(self._write_lock_owner).list_roster()
        rules = await self.list_rules()
        defaults = await self.zone_defaults()
        return {
            "shifts": await self.list_shifts(),
            "zones": await self._zone_directory().list_zones(),
            "employees": [
                {
                    "id": employee["id"],
                    "name": employee["name"],
                    "phone": employee["phone"],
                    "job_title": employee["job_title"],
                    "approved": employee["approved"],
                    "disabled": employee["disabled"],
                    "rule": rules.get(employee["id"]),
                    # {班次 id: 责任区 id}；没配过就是空对象
                    "zone_defaults": defaults.get(employee["id"], {}),
                }
                for employee in employees
            ],
        }

    async def _name_index(self) -> dict[int, str]:
        employees = await EmployeeAccounts(self._write_lock_owner).list_roster()
        return {
            employee["id"]: (employee["name"] or employee["phone"])
            for employee in employees
        }
