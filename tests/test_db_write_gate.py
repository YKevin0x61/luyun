#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""写单元（Write Unit）：锁与事务的收尾只剩一处。

候选 05 的契约（docs/adr/0152）：

* ``async with db.write():`` 是**唯一**同时持有写锁、并在出口提交的地方；
* 块内 ``commit()`` 只表示「这一步写完了」，真正落库在出口；``rollback()`` 把整块标作废；
* 退出时异常 → 整块回滚，不留半截事务；
* ``@serialized_write`` 在块内退化成被动（不重复取锁、不提前提交），块外语义不变。

可见性用**另一条连接**（``tests.pg_probe``）看：同一个连接看不见自己未提交的东西，
这件事本来就成立，证明不了「提交被推迟」。
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from database import DatabaseManager
from db_core.write_gate import serialized_write
from tests import pg_probe

ZONE_SQL = "INSERT INTO hygiene_zones (name, created_at, updated_at) VALUES (?, ?, ?)"
COUNT_SQL = "SELECT COUNT(*) AS n FROM hygiene_zones WHERE name = ?"


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _line(db, sql, params=()):
    async def _one():
        cur = await db._conn.execute(sql, params)
        return dict(await cur.fetchone())

    return _run(_one())


async def _visible(name):
    rows = await pg_probe.fetch_all_async(COUNT_SQL, (name,))
    return int(rows[0][0])


@pytest.fixture
def db():
    manager = DatabaseManager()
    _run(manager.connect())
    yield manager
    _run(manager.close())


class _Writer:
    """最小的「有 ``_conn`` 的服务」——装饰器只认这个入口，不认具体是哪个领域。"""

    def __init__(self, conn):
        self._conn = conn

    @serialized_write
    async def insert(self, name):
        await self._conn.execute(ZONE_SQL, (name, "t", "t"))
        await self._conn.commit()

    @serialized_write
    async def insert_then_call_inner(self, name):
        await self.insert(name)

    @serialized_write
    async def open_a_unit(self):
        async with self._conn.write():
            pass


# ── 写单元：锁 + 事务 ────────────────────────────────────────────────────


def test_commit_inside_the_unit_waits_for_the_exit(db):
    """块内 commit 不落库；退出时才提交 —— 这就是「单元拥有事务」。"""

    async def scenario():
        async with db.write():
            await db._conn.execute(ZONE_SQL, ("单元里的区", "t", "t"))
            await db._conn.commit()
            inside = await _visible("单元里的区")
            locked = db._write_lock.locked()
        return inside, locked, await _visible("单元里的区")

    inside, locked, after = _run(scenario())
    assert inside == 0, "块内 commit 就把行放出去了：事务不在单元手里"
    assert locked is True, "单元里必须真的持有写锁"
    assert after == 1
    assert db._write_lock.locked() is False, "退出后锁没还回来"


def test_exception_rolls_back_the_whole_unit(db):
    """异常 → 整块回滚（包括块内已经 commit 过的那部分），连接上不留事务。"""

    async def scenario():
        with pytest.raises(RuntimeError):
            async with db.write():
                await db._conn.execute(ZONE_SQL, ("半截区", "t", "t"))
                await db._conn.commit()
                raise RuntimeError("写到一半炸了")
        return db._conn.in_transaction(), await _visible("半截区")

    in_tx, rows = _run(scenario())
    assert in_tx is False
    assert rows == 0


def test_nested_units_commit_once_at_the_outer_exit(db):
    """嵌套单元只在外层结束提交；内层退出不落库。"""

    async def scenario():
        async with db.write():
            await db._conn.execute(ZONE_SQL, ("外层区", "t", "t"))
            async with db.write():
                await db._conn.execute(ZONE_SQL, ("内层区", "t", "t"))
                await db._conn.commit()
            kept = (await _visible("外层区"), await _visible("内层区"))
        return kept, (await _visible("外层区"), await _visible("内层区"))

    kept, after = _run(scenario())
    assert kept == (0, 0)
    assert after == (1, 1)


def test_rollback_inside_the_unit_marks_it_aborted(db):
    """块内 rollback 不立刻回滚，而是把整块标作废 —— 出口统一收尾。"""

    async def scenario():
        async with db.write():
            await db._conn.execute(ZONE_SQL, ("作废区", "t", "t"))
            await db._conn.rollback()
        return await _visible("作废区")

    assert _run(scenario()) == 0


def test_the_lock_is_the_shared_one(db):
    """锁只有一把：DatabaseManager 与 DatabaseConnection 指向同一个对象。"""
    assert db._write_lock is db._conn.write_lock
    assert db._write_lock is db._conn.write_lock, "属性每次返回的必须是同一把锁"


# ── 装饰器：块内被动、块外保持原样 ──────────────────────────────────────


def test_serialized_method_inside_a_unit_defers_its_commit(db):
    """装饰方法在单元里不自己提交，也不另取锁（否则死锁）。"""
    writer = _Writer(db._conn)

    async def scenario():
        async with db.write():
            await writer.insert("装饰方法区")
            return await _visible("装饰方法区")
        return None

    inside = _run(scenario())
    assert inside == 0
    assert _run(_visible("装饰方法区")) == 1


def test_serialized_methods_nest_without_deadlocking(db):
    """装饰方法互相调用不死锁（写锁不可重入，作用域要认自己的任务）。"""
    writer = _Writer(db._conn)

    async def scenario():
        await asyncio.wait_for(writer.insert_then_call_inner("嵌套装饰区"), timeout=5)

    _run(scenario())
    assert _run(_visible("嵌套装饰区")) == 1


def test_a_unit_may_not_open_inside_a_serialized_method(db):
    """反方向不允许：装饰器已经拿着锁，再开单元会等自己那把锁。

    宁可当场报错，也不要让一次请求挂在这种自锁上。
    """
    writer = _Writer(db._conn)
    with pytest.raises(RuntimeError):
        _run(writer.open_a_unit())
    assert db._write_lock.locked() is False, "报错路径也要把锁还回来"


# ── 守卫：锁与提交只剩一处实现 ──────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
SKIP_PARTS = {".git", ".venv", "node_modules", ".scratch", ".worktrees"}


def _python_files():
    for path in ROOT.rglob("*.py"):
        if SKIP_PARTS.intersection(path.parts):
            continue
        yield path


def _definitions(name):
    hits = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                hits.append(path.relative_to(ROOT).as_posix())
    return sorted(hits)


def test_serialized_write_is_defined_once():
    assert _definitions("serialized_write") == ["db_core/write_gate.py"], (
        "装饰器只该有 db_core/write_gate.py 那一份；领域层各自抄一份就又分叉了"
    )


def test_services_do_not_grow_their_own_write_lock():
    locks = [path for path in _definitions("_write_lock") if path.startswith("services/")]
    assert locks == [], f"服务层不该再自己造写锁：{locks}"
