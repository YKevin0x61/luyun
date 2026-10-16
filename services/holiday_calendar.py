#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""法定节假日日历（`GLOSSARY.md` 的「法定节假日日历」，迁移 0026）。

一条记录 = 一个**自然日** + 一个名称（国庆、春节）。超级管理员在管理端逐日标记，
**只标法定节假日、不标调休上班日** —— 「法定节假日补休」（ADR 0113）只用得到前者。

**这是「哪天是法定节假日」的唯一依据。** 代码里没有兜底的年度表：空的日历上一天都
不是节假日（`tests/test_holiday_calendar.py` 钉着这一条）。补休那一步（票 03）只调
:meth:`HolidayCalendar.days_in_month`，不自己写 SQL、也不认日期常识。

日期是 `YYYY-MM-DD` 的**自然日字符串**（不是 06:00 切的营业日：节假日是日历上那一天，
与 POS 的口径无关），沿用仓库里业务表日期一律 TEXT 的做法。
"""

from __future__ import annotations

import functools
import logging
import re
from datetime import date, datetime
from typing import Any, Callable, Optional

import asyncpg

from db_core.utils import CHINA_TZ
from services.identity import serialized_write

logger = logging.getLogger(__name__)

__all__ = ["MAX_NAME", "HolidayCalendar", "HolidayError"]

# 名称限长：它是一格里的标签（「国庆」「春节」「临时放假」），不是留言板。超了报错而不是
# 悄悄截断（同排班的 `MAX_REQUEST_NOTE` 口径：人写下的东西不该被系统改掉）。
MAX_NAME = 20

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

# 读日历只取这两列。日期的范围条件走字符串比较（TEXT 存 ISO 日期，字典序就是先后序）。
_DAY_COLUMNS = "day, name"


class HolidayError(ValueError):
    """日历的错误，带一个稳定 code（HTTP 层把它翻成一句中文）。

    形状同 `services/overtime/ledger.py` 的 `OvertimeError`：`code` 是稳定标识，
    `message` 放在 `args[0]`，带 `{}` 的文案靠它补细节（比如名称上限是几个字）。

    这里不抛库里那些异常的原文：缺表（0026 没应用）也是一种「还没升级完」，
    与「参数写错了」是两件事，调用方要能分开。
    """

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


def _needs_migration(method):
    """表还没建（0026 没应用）时，把 asyncpg 的缺表异常换成一句能照做的错。

    照 `services/overtime/ledger.py` 的同一个装饰器：真正用到日历的那一刻不该是一个
    500 traceback，超管要看到的是「去 Admin『系统更新 → 数据库迁移』应用 0026」。
    顺带把失败的事务回滚掉，否则这条连接后面的查询会全跟着报
    `current transaction is aborted`。
    """

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        try:
            return await method(self, *args, **kwargs)
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            raise HolidayError("not_migrated", str(exc)) from exc

    return wrapper


def _validated_day(day: Any) -> str:
    """把日期收敛成 `YYYY-MM-DD`，写错就报 `invalid_day`。

    只认格式还不够：`2026-10-32` 与 `2026-13-01` 都过得了正则，得真解析一次
    （`date.fromisoformat` 顺带把 `2026-02-30` 这种也拦下来）。
    """
    text = str(day or "").strip()
    if not _DAY_RE.match(text):
        raise HolidayError("invalid_day", f"invalid day: {day!r}")
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise HolidayError("invalid_day", str(exc)) from exc
    return text


def _validated_name(name: Any) -> str:
    """名称为空 / 只有空白 / 超过 :data:`MAX_NAME` 个字都拒。

    两端空白先剥掉再算字数：「  国庆 」写进去的是「国庆」——不然 20 个字的上限会被
    空白挤掉两格，页面上看着还没满却存不进去。
    """
    text = str(name or "").strip()
    if not text:
        raise HolidayError("missing_name", "name is required")
    if len(text) > MAX_NAME:
        raise HolidayError("name_too_long", str(MAX_NAME))
    return text


class HolidayCalendar:
    """法定节假日日历的读写口。构造要一个已打开的库连接（同 `OvertimeLedger` 的口径）。"""

    def __init__(self, conn_or_db, now: Optional[Callable[[], Any]] = None):
        conn = getattr(conn_or_db, "_conn", conn_or_db)
        if conn is None:
            raise RuntimeError("HolidayCalendar requires an open database connection")
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
            import asyncio

            self._local_write_lock = asyncio.Lock()
        return self._local_write_lock

    def _now_iso(self) -> str:
        value = self._now()
        if getattr(value, "tzinfo", None) is None:
            value = value.replace(tzinfo=CHINA_TZ)
        return value.isoformat()

    async def _rollback_quietly(self) -> None:
        try:
            await self._conn.rollback()
        except Exception:  # pragma: no cover - 回滚失败不该盖住原始错误
            logger.debug("回滚失败（忽略）", exc_info=True)

    async def _rows(self, where: str, params: tuple) -> list[dict]:
        cur = await self._conn.execute(
            f"SELECT {_DAY_COLUMNS} FROM statutory_holidays"
            f" WHERE {where} ORDER BY day",
            params,
        )
        return [dict(row) for row in await cur.fetchall()]

    @_needs_migration
    async def days_of_year(self, year: Any) -> list[dict]:
        """这一年被标成法定节假日的日子（按日期升序）。

        `year` 收 4 位整数；`26` / `1999` / `2101` 都是 `invalid_year` —— 页面上翻年的
        按钮只会给出合理的年份，写错了就是参数错了，不该静默当成某一世纪。
        """
        text = str(year or "").strip()
        if not re.match(r"^\d{4}$", text) or not 2000 <= int(text) <= 2100:
            raise HolidayError("invalid_year", f"invalid year: {year!r}")
        # 日期是 TEXT 的 ISO 字符串，所以范围条件就是前缀（字典序 = 先后序）。
        return await self._rows("day LIKE ?", (f"{text}-%",))

    @_needs_migration
    async def days_in_month(self, month: Any) -> list[str]:
        """这个自然月里的法定节假日（按日期升序）。

        **补休（票 03）的唯一读口**：`day` 的列表，调用方拿它判断「那天排了班的给
        1 天假」。日历是空的就返回空表 —— 没有兜底的年度表。
        """
        text = str(month or "").strip()
        if not _MONTH_RE.match(text):
            raise HolidayError("invalid_month", f"invalid month: {month!r}")
        try:
            date.fromisoformat(f"{text}-01")
        except ValueError as exc:
            raise HolidayError("invalid_month", str(exc)) from exc
        rows = await self._rows("day LIKE ?", (f"{text}-%",))
        return [row["day"] for row in rows]

    @_needs_migration
    @serialized_write
    async def mark(self, day: Any, name: Any) -> dict:
        """把这一天标成法定节假日（写名称）。同一天重复标记是**覆盖**那一行。

        返回 `created` 告诉调用方这一次是新建还是覆盖 —— 页面上那句回执要靠它。
        """
        value = _validated_day(day)
        label = _validated_name(name)
        existing = await self._rows("day = ?", (value,))
        now = self._now_iso()

        if existing:
            await self._conn.execute(
                "UPDATE statutory_holidays SET name = ?, updated_at = ?"
                " WHERE day = ?",
                (label, now, value),
            )
        else:
            # 并发下两个请求同时走到这里：日期唯一索引兜住，第二条走 DO UPDATE
            # （结果与先 UPDATE 再无事发生一致：留下最后写进去的那个名称）。
            await self._conn.execute(
                "INSERT INTO statutory_holidays (day, name, created_at, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT (tenant_id, day) DO UPDATE SET"
                " name = excluded.name, updated_at = excluded.updated_at",
                (value, label, now, now),
            )
        await self._conn.commit()
        return {"day": value, "name": label, "created": not existing}

    @_needs_migration
    @serialized_write
    async def unmark(self, day: Any) -> dict:
        """取消这一天的标记。那天本来就没标也不报错（`removed` 是 False）。"""
        value = _validated_day(day)
        cursor = await self._conn.execute(
            "DELETE FROM statutory_holidays WHERE day = ?", (value,)
        )
        await self._conn.commit()
        return {"day": value, "removed": bool(getattr(cursor, "rowcount", 0) > 0)}
