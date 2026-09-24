#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排班：规则 → 未来 90 天的排班结果。

排班是**独立系统**（`spec.md` 的「分层」）：这里不 import 卫生（`services.hygiene.*`），
也不认识卫生的表名；它从公共层取「这个人是谁」（`services.identity`）、算自己的规则，
把结果**物化**进 `staff_assignments`。下游只读那份数据，两边靠数据对接而不靠调用。

五件事在这一层：

- **班次**（`staff_shifts`）：白班、夜班是**数据**不是常量，加第三个班次是改数据。
- **规则**（`scheduling_rules`）：一人一条轮转规则 —— `cycle` 的长度就是周期天数，
  每格是班次 id、`None` 表示休；`anchor_date` 是周期起点，相位由它和营业日之差算。
- **展开**（`expand`）：把规则铺成 `staff_assignments` 的行，滚动铺未来 90 天。
- **请假**（`scheduling_requests`，`kind=leave`）：员工提申请、店长批 / 驳（票 08）。批准的那几天
  写成**单日覆盖**（`kind=leave`、没有班次），于是「请假」与「本来就休」在结果表里有据可查
  —— 两者的 `shift_id` 都是空，区别只在覆盖记录的 `kind`。
- **换班**（同一张表，`kind=swap`）：员工指一个同事和一天，**对方先同意**才进店长待办，
  店长批了两人那天的班对调（票 09；责任区跟着各自的新班次走）。对方拒绝或申请人撤回，
  排班一个字不变。

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
    "KIND_LEAVE",
    "KIND_MANUAL",
    "KIND_SWAP",
    "MAX_CYCLE_DAYS",
    "MAX_REQUEST_NOTE",
    "REST",
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

# 排班结果这一行是谁写的。
SOURCE_RULE = "rule"
SOURCE_OVERRIDE = "override"

# 单日覆盖是谁写的（`scheduling_overrides.kind`）：`manual` 是店长在月历上点着改的，
# `leave` 是批准请假写下的（票 08）。票 09 的换班写 `swap` —— 它们进的是同一张表
# （见 `migrations/pg/0007_scheduling_overrides.sql`），不用再出一次迁移。
KIND_MANUAL = "manual"
KIND_LEAVE = "leave"
KIND_SWAP = "swap"

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
        kind: str = KIND_MANUAL,
    ) -> None:
        stamp = self._now_iso()
        await self._write_override_row(employee_id, day, shift_id, zone_id, kind, stamp)
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
    ) -> None:
        """把覆盖记录写成给定样子（有则改、无则插）。不自己上锁、不提交。

        再写一次就是**替换**这一天的样子（不是叠加）：`kind` 也跟着换成这次写的那个 ——
        请假批过的那天，店长又点着改了一次，那天现在就是店长定的（换回 `manual`）。
        `kind` 是参数而不是写死的 `manual`：批准请假写的就是同一条记录、另一个来源
        （票 08），两者进的是同一张表、同一行。
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
                       (employee_id, business_date, shift_id, zone_id, kind, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (employee_id, day, shift_id, zone_id, kind, stamp, stamp),
            )
        else:
            await self._conn.execute(
                """UPDATE scheduling_overrides
                   SET shift_id = ?, zone_id = ?, kind = ?, updated_at = ?
                   WHERE employee_id = ? AND business_date = ?""",
                (shift_id, zone_id, kind, stamp, employee_id, day),
            )

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

        同样是「这天没有班」，每人还带一个 `leave`：`True` 是**批了请假**，`False` 是本来
        就休。结果行里两者都是 `shift_id` 空，区别只在覆盖记录的 `kind`（票 08）——
        这个字段就是为那句「和本来就休看得出区别」给的判据，前端不自己猜。
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

        def person(
            employee_id: int,
            zone: Optional[int],
            overridden: bool,
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
                person(employee_id, zone, overridden, employee_id in leave_ids)
                for employee_id, zone, overridden in rest_ids
            ],
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

        `leave=True` 是第三种「没有班」：**批了的请假**（票 08）。员工自己提的那条申请
        批下来那天，他该看到的不是「休」而是「请假」。
        """
        await self.expand(employee_id)
        first = self.today()
        count = max(int(days), 1)
        last = shift_business_date(first, count - 1)
        cur = await self._conn.execute(
            """SELECT business_date, shift_id, zone_id FROM staff_assignments
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
                "zone_id": zone_id,
                # 区名来自结果行上的 zone_id：那天写在行上的是哪个区就是哪个区，
                # 事后改固定区不改写过去（跟 `day_detail` 同一条口径）。
                "zone_name": None if zone_id is None else zone_names.get(zone_id),
                # 批了的请假（票 08）：「那天休」与「那天请假」是两句话。
                "leave": key in leave_days,
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
        # 整月里批了请假的那几天（票 08）：`_leave_days` 的两端都含，所以末界是下月一号
        # 的前一天 —— 用 `shift_business_date` 退一天，不自己算月份长度。
        leave_days = await self._leave_days(
            employee_id, start.isoformat(), shift_business_date(next_month.isoformat(), -1)
        )

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
                # 跟 `my_days` 同一个字段、同一条口径：格子里的「请假」不是「休」。
                "leave": key in leave_days,
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
        if last < self.today():
            # 跟票 07 的 `past_day` 是同一条口径，但错的是「给过去提申请」而不是
            # 「改过去的排班」：文案分开，别让员工读到「排班写下的历史不重写」。
            raise SchedulingError("past_leave", "past_leave")
        if last > self._window()[1]:
            # 第二参填进那句提示的 `{}`：报**真实的窗口末日**（跟票 07 同一条口径，
            # 别把 `EXPANSION_DAYS` 写死在文案里）。
            raise SchedulingError("beyond_leave", self._window()[1])
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
        return await self._decide(int(row["id"]), STATUS_CANCELLED)

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
        不会动：过去不改写（跟票 07 同一条口径），先说清楚，别让店长以为批了就改了历史。

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
        rules = await self.list_rules()
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
            # `approved` / `disabled` 一起给出去，由前端决定要不要提醒 —— 停用的人
            # 不该天天挂在待办上，但「谁停用了」是名单的事，服务层不替它下结论。
            "without_rule": [
                {
                    "id": employee["id"],
                    "name": employee["name"],
                    "phone": employee["phone"],
                    "job_title": employee["job_title"],
                    "approved": employee["approved"],
                    "disabled": employee["disabled"],
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

        已经过去的日子**不重写**（票 07 同一条「过去不改」）：批得晚了就是晚了，那天当时
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
        return await self._decide(int(row["id"]), STATUS_REJECTED)

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
        批了就是他接走你的班、你那天空出来 —— 这也是一种换班。但**提的人那天得有一行**：
        手上没有班就没什么可换的（`nothing_to_swap`），先让店长配上规则再说。
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
        if peer["disabled"] or not peer["approved"]:
            # 停用 / 还没批准的人不进排班名单，也不该被拉来换班：跟「找不到这个人」
            # 是两件事（一个是名字错了，一个是人现在不在），分两句说。
            raise SchedulingError("peer_unavailable", "peer_unavailable")
        if not str(business_date or "").strip():
            # 字段整个没给：跟「格式不对」分开说（跟请假 `missing_day` 同一条口径）。
            raise SchedulingError("missing_swap_day", "missing_swap_day")
        day = _require_business_date(business_date)
        if day < self.today():
            raise SchedulingError("past_swap", "past_swap")
        if day > self._window()[1]:
            # 第二参填进那句提示的 `{}`：报真实的窗口末日（跟票 07、请假同一条口径）。
            raise SchedulingError("beyond_swap", self._window()[1])
        # 先把自己那天铺出来再问「有没有班」：规则配了但还没展开时不能误判成「没班」。
        await self.expand(employee_id)
        if not (await self._day_shift(employee_id, day))[0]:
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
        return await self._decide(
            int(row["id"]), STATUS_PENDING_MANAGER if agree else STATUS_REJECTED
        )

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
            if employee["id"] != me and employee["approved"] and not employee["disabled"]
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

        一次提交是有意的：批一半（写了三天、申请还是待批）比不批更糟 —— 店长会再点
        一次「批准」，或者以为没批成而重复处理。
        """
        if row["kind"] == KIND_SWAP:
            return await self._approve_swap(row)
        employee_id = int(row["employee_id"])
        today = self.today()
        last_day = self._window()[1]
        stamp = self._now_iso()
        applied: list[str] = []
        skipped: list[str] = []
        for day in _each_day_between(row["start_date"], row["end_date"]):
            if day < today or day > last_day:
                skipped.append(day)
                continue
            await self._write_override_row(employee_id, day, None, None, KIND_LEAVE, stamp)
            await self._write_day_row(employee_id, day, None, None, SOURCE_OVERRIDE, stamp)
            applied.append(day)
        await self._conn.execute(
            """UPDATE scheduling_requests
               SET status = ?, decided_at = ?, updated_at = ?
               WHERE id = ?""",
            (STATUS_APPROVED, stamp, stamp, int(row["id"])),
        )
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

    async def _approve_swap(self, row: dict) -> dict:
        """把两个人那天的班对调（票 09 的验收项）。调用方持写锁、负责提交。

        写的是**覆盖行**（`kind=swap`）+ 结果行（`source=override`），两个人都写 ——
        跟店长手改、批准请假走的是同一条路，于是那天之后规则再重铺也改不动它。

        责任区按各自**新班次**的固定区取（验收：区跟着班次走）；新班次是「休 / 没排到」
        时区也留空。当天已经过去 / 超出展开窗口的那一天只记「没动」：批得晚了就是晚了
        （跟请假那一支、票 07 同一条「过去不改」）。
        """
        employee_id = int(row["employee_id"])
        peer_id = self._peer_of_swap(row)
        day = row["start_date"]
        stamp = self._now_iso()
        applied: list[str] = []
        skipped: list[str] = []
        if day < self.today() or day > self._window()[1]:
            skipped.append(day)
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
        await self._conn.execute(
            """UPDATE scheduling_requests
               SET status = ?, decided_at = ?, updated_at = ?
               WHERE id = ?""",
            (STATUS_APPROVED, stamp, stamp, int(row["id"])),
        )
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

    @serialized_write
    async def _decide(self, request_id: int, status: str) -> dict:
        """把申请置成 `status` 并记下时间（驳回 / 撤回共用）。排班一个字不改。"""
        stamp = self._now_iso()
        await self._conn.execute(
            """UPDATE scheduling_requests
               SET status = ?, decided_at = ?, updated_at = ?
               WHERE id = ?""",
            (status, stamp, stamp, int(request_id)),
        )
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
