#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""等全局串行写锁必须有上限（PERF-08：排队阶段不受 statement/lock timeout 约束）。

2026-09-23 检核的实测：进程内只有一条 ``PgConnection``，task A ``ensure_transaction()``
拿着写事务不放，task B ``await conn.execute("SELECT 1")`` 等了 6.504s 仍然
``task.done() -> False``——``lock_timeout`` 明明是 5s，因为 GUC 只在语句**真正执行
到 PG** 时才开始计时，排队等应用内的那把锁根本不算。

这里用真实连接复现同一姿态，断言两件事：

* 上限生效：等锁超过 ``LUYUN_PG_WRITE_LOCK_TIMEOUT_MS`` 就抛 ``DatabaseBusy``
  （HTTP 层 503 + ``Retry-After`` + ``retryable: true``），不再无限期排住；
* 对照：把上限显式关成 0，等锁的请求**没有**任何期限——这就是修复前的形态，
  证明「只靠 PG 的 GUC 兜不住排队」。

**判据写法（评审 R-T1-02）**：「正在等待」一律用**确定性状态判据**描述——等待者
挂在锁的等待队列里、串行锁仍被持有者拿着、``waiter.done() is False``；需要等调度
时只让出事件循环（若干轮 ``await asyncio.sleep(0)``），**不用**「睡 0.5s 再看它是否
还在等」或 ``wait_for(waiter, 2)`` 这类墙钟期限。旧版给「无期限」这条套了 2s 墙钟
硬期限，满负载下先到期并取消内部任务 → ``TimeoutError``；清理偏偏用
``contextlib.suppress(Exception)``，而 ``CancelledError`` 属 ``BaseException`` 不被
抑制，真实失败于是被替换成裸 ``CancelledError``（最深帧落在
``db_core/backend/pg.py:912``），三个成员先后据此误判成生产竞态。

探针语句只有 ``SELECT 1``，不读不写任何业务表；写单元用的是 ``ensure_transaction()``
+ ``rollback()``，不落任何数据。
"""

import asyncio
import contextlib
import os
import time
import unittest
from typing import Callable, List, Optional
from unittest import mock

try:  # asyncpg 是 PG 后端依赖；缺失时跳过（与 test_pg_timeouts.py 同款守卫）
    import asyncpg
except ImportError:  # pragma: no cover
    asyncpg = None  # type: ignore[assignment]

from db_core.backend import pg as pg_backend
from db_core.backend.pg import DatabaseBusy

WRITE_LOCK_ENV = "LUYUN_PG_WRITE_LOCK_TIMEOUT_MS"
# 检核实测的对照量级：修复前 6.504s 都没有返回。
TICKET_WAIT_SECONDS = 6.504
# 独立验证用例（verifier-db）的专用探针表：只放一个 integer 列，不碰业务表。
PROBE_TABLE = "ver1_write_lock_probe"

# 清理阶段吸收到的问题：不掩盖用例结果，但也不让它悄悄消失。
_CLEANUP_ISSUES: List[str] = []
# 这两个是进程级信号，清理阶段必须继续往上抛。
_CLEANUP_PASSTHROUGH = (KeyboardInterrupt, SystemExit)


@contextlib.contextmanager
def _absorb_cleanup_errors(what: str):
    """清理阶段吸收一切异常（**含 ``CancelledError``**）并记录，不掩盖真实失败。

    ``CancelledError`` 继承 ``BaseException``：旧版清理用
    ``contextlib.suppress(Exception)`` 拦不住它，一次清理期的取消就会把「断言失败 /
    超时」替换成裸 ``CancelledError``（评审 R-T1-02）。这里改成吸收
    ``BaseException``（进程级信号除外）并记进 ``_CLEANUP_ISSUES``，用例结束时会打印。
    """
    try:
        yield
    except _CLEANUP_PASSTHROUGH:
        raise
    except BaseException as exc:  # noqa: BLE001 —— 清理阶段不能让它掩盖真实失败
        _CLEANUP_ISSUES.append(f"{what}: {type(exc).__name__}: {exc}")


def pg_available() -> bool:
    if asyncpg is None:
        return False

    async def probe() -> bool:
        try:
            conn = await asyncpg.connect(pg_backend.dsn_from_env(), timeout=2)
        except Exception:
            return False
        await conn.close()
        return True

    try:
        return asyncio.run(probe())
    except Exception:
        return False


@unittest.skipUnless(pg_available(), "PostgreSQL 不可用，跳过写锁排队测试")
class WriteLockQueueTimeoutTest(unittest.IsolatedAsyncioTestCase):
    """seam：``pg_backend.connect()`` 建出的真实连接 + 它的串行写锁。"""

    async def asyncSetUp(self) -> None:
        _CLEANUP_ISSUES.clear()
        self.addCleanup(self._report_cleanup_issues)

    def _report_cleanup_issues(self) -> None:
        if _CLEANUP_ISSUES:
            print("清理阶段吸收到的问题（不影响用例结果）：" + "；".join(_CLEANUP_ISSUES))

    async def _connect_with(self, **env):
        """在指定排队上限下建连接（未给出的先清掉 = 生产形态）。"""
        with mock.patch.dict(os.environ):
            os.environ.pop(WRITE_LOCK_ENV, None)
            os.environ.update({k: v for k, v in env.items() if v is not None})
            return await pg_backend.connect()

    async def _hold_lock(self, conn, seconds: float):
        """占着串行锁不还：真实的写事务姿态（ensure_transaction 后就握着）。"""

        async def _holder() -> None:
            await conn.ensure_transaction()
            await asyncio.sleep(seconds)

        return asyncio.create_task(_holder())

    async def _hold_lock_until_released(self, conn):
        """占着串行锁**直到测试放行**：持有者不会自己结束，不依赖墙钟睡眠。

        返回 ``(task, release_event)``。这是「无期限」对照用例的姿态：只要持有者还
        活着，等待者就不可能因为「持有者刚好睡醒」而被误判成已经返回，锁也不会被
        ``acquire`` 的接管路径（持有者已结束）抢走——那条路径正是旧版在满负载下
        假红的原因之一。
        """
        release = asyncio.Event()

        async def _holder() -> None:
            await conn.ensure_transaction()
            await release.wait()

        return asyncio.create_task(_holder()), release

    async def _wait_until_held(self, conn, attempts: int = 5000) -> None:
        """等写单元真的拿住串行锁（纯让出调度，不用墙钟期限）。"""
        for _ in range(attempts):
            if conn._guard.depth > 0:
                return
            await asyncio.sleep(0)
        self.fail(f"前置失败：写单元没拿住串行锁（让出调度 {attempts} 次仍未取得）")

    async def _yield_until(
        self,
        predicate: Callable[[], bool],
        message: str,
        attempts: int = 5000,
    ) -> None:
        """让出事件循环直到判据成立；判据不成立时用明确消息报出真实原因。

        轮询的是**状态**（谁持有锁、任务是否还在等），不是「等了多久」——每次
        ``asyncio.sleep(0)`` 只是一个调度点，没有墙钟期限，所以负载再重也不会因为
        「期限先到期」而假红。
        """
        for _ in range(attempts):
            if predicate():
                return
            await asyncio.sleep(0)
        self.fail(f"{message}（让出调度 {attempts} 次判据仍未成立）")

    @staticmethod
    def _pending_lock_waiters(conn) -> Optional[List[asyncio.Future]]:
        """挂在 ``asyncio.Lock`` 等待队列里的未完成 future（None = 该实现没暴露）。"""
        waiters = getattr(conn._guard._lock, "_waiters", None)
        if waiters is None:
            return None
        return [w for w in waiters if not w.done()]

    async def _release(self, conn, holder, release: Optional[asyncio.Event] = None) -> None:
        """收尾：结束持有者并让它的事务与锁正常归还。"""
        if release is not None:
            release.set()
        if holder is not None:
            holder.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await holder
        # 事务还没结束：走正常 rollback 路径（同时释放串行锁）。
        await conn.rollback()

    async def test_waiting_for_the_write_lock_times_out(self):
        """等锁超过上限 → DatabaseBusy（可重试），而不是无限排队。"""
        conn = await self._connect_with(**{WRITE_LOCK_ENV: "300"})
        holder = await self._hold_lock(conn, seconds=5)
        try:
            await self._wait_until_held(conn)
            started = time.monotonic()
            with self.assertRaises(DatabaseBusy) as ctx:
                await conn.execute("SELECT 1")
            elapsed = time.monotonic() - started
        finally:
            with _absorb_cleanup_errors("回收写事务持有者"):
                await self._release(conn, holder)
            with _absorb_cleanup_errors("关闭连接"):
                await conn.close()

        self.assertLess(
            elapsed,
            1.5,
            f"排队应当在 0.3s 的配置上限附近返回，实际等了 {elapsed:.3f}s（"
            f"修复前实测 {TICKET_WAIT_SECONDS}s 都没有返回）",
        )
        self.assertIn("重试", str(ctx.exception))

    async def test_lock_is_usable_again_after_the_holder_releases(self):
        """超时只是「这次排队放弃」，锁本身没有损坏：持有者一归还就能继续。"""
        conn = await self._connect_with(**{WRITE_LOCK_ENV: "300"})
        holder, release = await self._hold_lock_until_released(conn)
        try:
            await self._wait_until_held(conn)
            with self.assertRaises(DatabaseBusy):
                await conn.execute("SELECT 1")
            # 持有者正常收尾（归还事务与锁）。
            release.set()
            await holder
            await conn.rollback()
            # 持有者已结束：下一个写单元应当立刻拿到锁（不会被永久占住）。
            # 这里不加测试自己的墙钟期限：连接上的排队上限就是 0.3s，锁若没还清，
            # ``ensure_transaction()`` 自己会抛 DatabaseBusy——判据在实现里。
            await conn.ensure_transaction()
            await conn.rollback()
        finally:
            with _absorb_cleanup_errors("回收写事务持有者"):
                await self._release(conn, holder, release)
            with _absorb_cleanup_errors("关闭连接"):
                await conn.close()

    async def test_write_entrance_queue_also_times_out(self):
        """独立验证（verifier-db）：**写语句**排队同样有时限（写接口 503 的来源）。

        实现者的用例从读语句（``conn.execute("SELECT 1")``）排队进；这条走真实写路径
        ``INSERT``（本用例专用的探针表，不碰业务表），并顺带核对 ``reason`` /
        ``retryable`` 这两个 HTTP 层要读的字段。

        注：排队发生在 ``PgCursor.execute`` 取 ``guard()`` 的时候——``ensure_transaction()``
        本身是「已有活动事务就直接返回」的语义，直接调它不会经过写锁排队。
        """
        conn = await self._connect_with(**{WRITE_LOCK_ENV: "300"})
        await conn.execute(
            f"CREATE TABLE IF NOT EXISTS {PROBE_TABLE} (id integer)"
        )
        holder = await self._hold_lock(conn, seconds=5)
        try:
            await self._wait_until_held(conn)
            started = time.monotonic()
            with self.assertRaises(DatabaseBusy) as ctx:
                # 兜底 wait_for：判据坏掉时用例要「红」，而不是把整套测试挂住。
                await asyncio.wait_for(
                    conn.execute(f"INSERT INTO {PROBE_TABLE} (id) VALUES (?)", (1,)), 3
                )
            elapsed = time.monotonic() - started
        finally:
            with _absorb_cleanup_errors("回收写事务持有者"):
                await self._release(conn, holder)
            with _absorb_cleanup_errors("删除探针表"):
                await conn.execute(f"DROP TABLE IF EXISTS {PROBE_TABLE}")
            with _absorb_cleanup_errors("关闭连接"):
                await conn.close()

        self.assertLess(
            elapsed,
            1.5,
            f"写路径排队应当在 0.3s 的配置上限附近返回，实际等了 {elapsed:.3f}s",
        )
        self.assertEqual(ctx.exception.reason, "write_lock_timeout")
        self.assertTrue(ctx.exception.retryable, "HTTP 层据此回 503 + retryable")

    async def test_without_the_limit_the_queue_has_no_deadline(self):
        """对照：上限关成 0 时，等锁的请求**没有任何期限**，会一直等到锁可用。

        这正是票 03 的实测形态（6.504s、``task.done() -> False``）：PG 的
        ``lock_timeout`` 管不到排队阶段，只有应用侧上限能兜。

        判据全部是确定性的状态量，没有任何「等一段时间看它是否还在等」的墙钟期限
        （旧版就是在这里套了 2s ``wait_for``，满负载下先到期 → ``TimeoutError``，
        清理又把它换成裸 ``CancelledError``，见评审 R-T1-02）：

        1. 配置确实是「无限等待」（``write_lock_timeout is None``）；
        2. 持有者在飞事务仍握着串行锁，等待者**挂在锁的等待队列里**、未完成、
           未取消、也没抛异常；
        3. 事件循环再多空转若干轮（不是墙钟睡眠）后，它**仍然**停在队列里；
        4. 持有者归还之后，等待者立刻恢复执行——说明它此前一直在等，只是没有期限。
        """
        conn = await self._connect_with(**{WRITE_LOCK_ENV: "0"})
        self.assertIsNone(conn.write_lock_timeout, "0 应当表示无限等待")
        holder, release = await self._hold_lock_until_released(conn)
        waiter = None
        try:
            await self._wait_until_held(conn)
            waiter = asyncio.create_task(conn.execute("SELECT 1"))
            # 让事件循环跑足够多轮（纯调度让出）：等待者会被调度到并停在锁的
            # 等待队列里——不需要拿墙钟去猜它「等够了没有」。
            for _ in range(200):
                await asyncio.sleep(0)

            # 判据 ②：等待者处于阻塞态，串行锁仍被在飞事务的持有者拿着。
            self.assertFalse(waiter.done(), "没有上限时等锁的请求不该已经返回")
            self.assertFalse(waiter.cancelled(), "等待者不该被任何期限取消")
            self.assertTrue(
                conn._guard._lock.locked(), "串行锁应当仍被持有者拿着，没被等锁者抢走"
            )
            self.assertEqual(conn._guard.depth, 1, "持有者的锁深度应当还是 1")
            self.assertIs(conn._guard.owner, holder, "锁的持有者仍是那个在飞写事务")
            pending = self._pending_lock_waiters(conn)
            if pending is not None:
                self.assertEqual(
                    len(pending),
                    1,
                    "等待者应当正好有一个挂在锁的等待队列里（阻塞态，而不是已完成）",
                )

            # 判据 ③：再多调度若干轮，它仍在原地等——没有期限把它打断。
            for _ in range(200):
                await asyncio.sleep(0)
            self.assertFalse(waiter.done(), "多轮调度之后等待者仍然不该返回")
            self.assertTrue(conn._guard._lock.locked(), "串行锁不该在等待中被放开")

            # 判据 ④：持有者归还锁 → 等待者立刻恢复执行（此前确实一直在等）。
            await self._release(conn, holder, release)
            holder = None
            await self._yield_until(
                lambda: waiter.done() or conn._guard.owner is waiter,
                "持有者归还锁之后等待者没有恢复执行",
            )
            if waiter.done() and not waiter.cancelled():
                self.assertIsNone(
                    waiter.exception(),
                    f"等待者恢复执行后报错：{waiter.exception()!r}",
                )
        finally:
            if waiter is not None:
                with _absorb_cleanup_errors("取消等锁的请求"):
                    waiter.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await waiter
            if holder is not None:
                with _absorb_cleanup_errors("回收写事务持有者"):
                    await self._release(conn, holder, release)
            with _absorb_cleanup_errors("关闭连接"):
                await conn.close()


if __name__ == "__main__":
    unittest.main()
