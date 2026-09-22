#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""写路径的两个地基：全局写锁真的共享，异常不把半截事务留在连接上。

背景（审查报告 §2.2）：

* ``HygieneWork`` / ``EmployeeAccounts`` 的 ``_write_lock`` 会优先取 owner
  （``DatabaseManager``）上的锁；但 ``DatabaseManager._write_lock`` 一直是 ``None``，
  于是两边各自退回一把局部锁——两个 service 的写并不互斥。
* aiosqlite 是隐式事务：``submit_daily`` 里 ``_ensure_instance`` 的 INSERT 发生在
  ``try`` 之外，磁盘满/权限错时既不回滚也不清理，半截写会跟着下一个 ``commit``
  落库，或者被别人的 ``rollback`` 连坐。
"""

import asyncio
import io
from datetime import datetime
from unittest import mock

import pytest
from PIL import Image

from config import settings
from database import CHINA_TZ, DatabaseManager
from services.app_runtime import AppRuntime, set_runtime
from services.hygiene.accounts import EmployeeAccounts
from services.hygiene.captures import FakeCaptureStore
from services.hygiene.images import ImageVariantGenerator
from services.hygiene.work import HygieneWork, serialized_write

SUPER = {"kind": "super"}
PHONE = "13800138000"
PASSWORD = "password123"


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _jpeg():
    output = io.BytesIO()
    Image.new("RGB", (80, 60), (120, 180, 220)).save(output, format="JPEG")
    return output.getvalue()


@pytest.fixture
def runtime(tmp_path):
    old = settings.DATABASE_DIR
    settings.DATABASE_DIR = str(tmp_path)
    db = DatabaseManager()
    _run(db.connect())
    set_runtime(AppRuntime(db=db))
    accounts = EmployeeAccounts(
        db, now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ)
    )
    work = HygieneWork(
        db,
        captures=FakeCaptureStore(),
        now=lambda: datetime(2026, 9, 13, 10, 0, tzinfo=CHINA_TZ),
        image_variants=ImageVariantGenerator(),
    )
    _run(work.prepare())
    yield db, accounts, work
    _run(db.close())
    set_runtime(None)
    settings.DATABASE_DIR = old


def _count(db, sql, params=()):
    cur = _run(db._conn.execute(sql, params))
    return int(dict(_run(cur.fetchone()))["n"])


def test_services_share_one_global_write_lock(runtime):
    db, accounts, work = runtime
    assert db._write_lock is not None, "connect() 必须把全局写锁建出来"
    assert work._write_lock is db._write_lock, "HygieneWork 必须用全局锁"
    assert accounts._write_lock is db._write_lock, "EmployeeAccounts 必须用全局锁"


def test_failed_capture_leaves_no_open_transaction(runtime):
    db, accounts, work = runtime
    zone = _run(work.list_zones())[0]
    item = _run(
        work.add_daily_item(
            SUPER,
            zone["id"],
            "案板-台面",
            {"bytes": _jpeg(), "content_type": "image/jpeg", "markup": []},
        )
    )
    employee = _run(accounts.register(PHONE, PASSWORD, "张三"))
    _run(accounts.approve(employee["id"]))
    _run(accounts.pick_assignment(employee["id"], "白班", zone["id"]))
    actor = {
        "kind": "staff",
        "id": employee["id"],
        "permission": "普通员工",
        "name": "张三",
        "phone": PHONE,
        "shift": "白班",
        "zone_id": zone["id"],
    }

    boom = mock.AsyncMock(side_effect=OSError(28, "No space left on device"))
    with mock.patch.object(work._captures, "put_async", boom):
        with pytest.raises(OSError):
            _run(
                work.submit_daily(
                    actor,
                    item["id"],
                    {"bytes": _jpeg(), "live": True, "content_type": "image/jpeg"},
                )
            )

    assert db._conn.in_transaction() is False, "异常后连接上不能挂着未提交事务"
    assert _count(db, "SELECT COUNT(*) AS n FROM hygiene_daily_instances") == 0
    assert _count(db, "SELECT COUNT(*) AS n FROM hygiene_daily_submissions") == 0


def test_capture_io_runs_outside_the_write_lock(runtime):
    """拍照落盘不该占着全局写锁。

    ``_store_capture`` 只碰磁盘（3 次 fsync + PIL 编解码），却原来被
    ``@serialized_write`` 罩着：一张 12MP 照片 ≈100ms 里，注册/选班/验收/开单
    全都排队。现在锁只包住纯 SQL 的 ``_commit_daily_submission``。
    """
    db, accounts, work = runtime
    zone = _run(work.list_zones())[0]
    item = _run(
        work.add_daily_item(
            SUPER,
            zone["id"],
            "案板-台面",
            {"bytes": _jpeg(), "content_type": "image/jpeg", "markup": []},
        )
    )
    employee = _run(accounts.register(PHONE, PASSWORD, "张三"))
    _run(accounts.approve(employee["id"]))
    _run(accounts.pick_assignment(employee["id"], "白班", zone["id"]))
    actor = {
        "kind": "staff",
        "id": employee["id"],
        "permission": "普通员工",
        "name": "张三",
        "phone": PHONE,
        "shift": "白班",
        "zone_id": zone["id"],
    }

    observed = []
    original = work._store_capture

    async def probe(*args, **kwargs):
        observed.append(work._write_lock.locked())
        return await original(*args, **kwargs)

    with mock.patch.object(work, "_store_capture", probe):
        _run(
            work.submit_daily(
                actor,
                item["id"],
                {"bytes": _jpeg(), "live": True, "content_type": "image/jpeg"},
            )
        )

    assert observed == [False], "存图过程中不应持有写锁"
    assert _count(db, "SELECT COUNT(*) AS n FROM hygiene_daily_submissions") == 1


def test_serialized_write_rolls_back_on_any_exception(runtime):
    """装饰器契约：加锁 + 任何异常都回滚（work 与 accounts 用同一份实现）。"""
    db, _accounts, work = runtime

    class _Writer:
        def __init__(self, conn, lock):
            self._conn = conn
            self._write_lock = lock

        @serialized_write
        async def half_write(self):
            await self._conn.execute(
                "INSERT INTO hygiene_zones (name, created_at, updated_at) "
                "VALUES (?, ?, ?)",
                ("半截区", "t", "t"),
            )
            raise OSError(28, "No space left on device")

    writer = _Writer(db._conn, work._write_lock)
    with pytest.raises(OSError):
        _run(writer.half_write())

    assert db._conn.in_transaction() is False, "异常后连接上不能挂着未提交事务"
    assert _count(
        db, "SELECT COUNT(*) AS n FROM hygiene_zones WHERE name = ?", ("半截区",)
    ) == 0, "半截写必须被回滚"
