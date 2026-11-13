#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工作台「任务」域的持久层。

设计见 `docs/adr/0131-work-tasks-independent-domain.md`（独立任务域）、
`0132-task-assignment-people-and-pools.md`（指派）与
`0133-recurring-task-definition-and-instances.md`（定义 + 期实例）。

票 01（地基）先只落**照片台账**这一块：上传成功后写一行 `work_task_images`，
后面的票（02 发布、03 提交、04 验收）再往里接定义 / 实例 / 提交。这样照片的
落盘、归属与读取权限在第一张票里就是真的，后面的票只加业务行。

写路径口径与 `services/wastage/store.py` 一致：
  · 直接拿 `DatabaseManager` 的底层连接（`?` 占位统一由方言层改写）；
  · 写后显式 `commit`，失败 `rollback` 并把刚落的文件删掉（不留「库没行、盘上有图」）；
  · 表还没迁移时抛 `TaskStoreError("not_migrated")`，由 API 映射成 503 —— 那是
    「这台机器还没升级完」，不是请求参数写错。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import asyncpg

from database import CHINA_TZ

logger = logging.getLogger(__name__)

#: 照片用途：完成照必须现场拍（服务端校验 live 标记），参考图允许从相册选。
PURPOSE_SUBMISSION = "submission"
PURPOSE_REFERENCE = "reference"
PURPOSES = frozenset({PURPOSE_SUBMISSION, PURPOSE_REFERENCE})


@dataclass(frozen=True)
class TaskActor:
    """一次请求的操作人。管理端共享账号没有员工号，员工端有员工号与姓名快照。"""

    kind: str
    employee_id: Optional[int]
    name: str

    @property
    def owner_key(self) -> str:
        """照片台账的归属键：管理端是 `admin`，员工是 `staff:<员工号>`。"""
        return "admin" if self.kind == "admin" else f"staff:{self.employee_id}"


class TaskStoreError(Exception):
    """稳定错误码（`not_migrated` / `images_unavailable`），由 API 翻成中文与状态码。"""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


class TaskStore:
    def __init__(self, conn_or_db: Any, *, captures: Any = None, now=None):
        # 与损耗/卫生同一条取连接的口径：`DatabaseManager` 拿 `_conn`，测试可直接给
        # `DatabaseConnection`。`db.table()` 只认启动时绑定的表，任务表不在那份清单里，
        # 所以这里不经过 TableView。
        self._conn = getattr(conn_or_db, "_conn", conn_or_db)
        self._captures = captures
        self._now = now or (lambda: datetime.now(CHINA_TZ))

    # ── 基础 ────────────────────────────────────────────────────────────────
    def _now_iso(self) -> str:
        return self._now().isoformat()

    async def _rows(self, sql: str, params: tuple = ()) -> list:
        try:
            cur = await self._conn.execute(sql, params)
            return list(await cur.fetchall())
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            raise TaskStoreError("not_migrated", str(exc)) from exc

    async def _execute(self, sql: str, params: tuple = ()):
        try:
            cur = await self._conn.execute(sql, params)
            row = await cur.fetchone()
            await self._conn.commit()
            return row
        except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
            await self._rollback_quietly()
            raise TaskStoreError("not_migrated", str(exc)) from exc
        except Exception:
            await self._rollback_quietly()
            raise

    async def _rollback_quietly(self) -> None:
        try:
            await self._conn.rollback()
        except Exception:  # noqa: BLE001 - 回滚失败不能盖住原始错误
            logger.warning("任务写入失败后回滚也失败了", exc_info=True)

    # ── 照片台账 ────────────────────────────────────────────────────────────
    async def add_image(
        self,
        actor: TaskActor,
        data: bytes,
        content_type: str,
        purpose: str,
    ) -> dict:
        """先把字节落盘，再记一行台账；写库失败时把盘上那份删掉。

        同一套两步口径：上传只是拿 `capture_id`，真正绑到任务上要等提交（票 03）。
        所以这张表允许 `purpose=submission` 且没被任何提交引用的「孤儿图」——
        留着可查、可清，不做定时清理（与损耗的 `wastage_images` 同一条路）。
        """
        if self._captures is None:
            raise TaskStoreError("images_unavailable")
        capture_id = await self._captures.put_async(data, content_type=content_type)
        try:
            row = await self._execute(
                "INSERT INTO work_task_images"
                " (capture_id, purpose, owner_kind, owner_employee_id, owner_key,"
                "  content_type, byte_size, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (
                    capture_id,
                    purpose,
                    actor.kind,
                    actor.employee_id,
                    actor.owner_key,
                    content_type,
                    len(data),
                    self._now_iso(),
                ),
            )
        except Exception:
            await self._captures.delete_async(capture_id)
            raise
        return {
            "id": int(row["id"]) if row is not None else None,
            "capture_id": capture_id,
            "purpose": purpose,
            "content_type": content_type,
            "byte_size": len(data),
            # 前端直接把这个地址塞进 <img src>，读接口自己再判一次权限。
            "url": f"/api/tasks/images/{capture_id}",
        }

    async def _image_is_bound(self, capture_id: str) -> bool:
        """这张图是否已经挂到某个任务上（完成照或参考图）。

        绑定之后就是任务内容的一部分：能看到任务的人都能看到它。没绑定的只有
        上传者自己能看 —— 上传后预览、提交失败重试都靠这一条，不能把别人刚传的
        图当公开图片读。
        """
        rows = await self._rows(
            "SELECT 1 FROM work_task_submission_photos WHERE capture_id = ?"
            " UNION ALL SELECT 1 FROM work_task_definitions WHERE reference_capture_id = ?"
            " UNION ALL SELECT 1 FROM work_task_instances WHERE reference_capture_id = ?"
            " LIMIT 1",
            (capture_id, capture_id, capture_id),
        )
        return bool(rows)

    async def image_meta(self, actor: TaskActor, capture_id: str) -> Optional[dict]:
        """这张图这个人能不能看 —— 能就回元数据，不能回 `None`（API 一律回 404）。

        不区分「不存在」与「不是你的」：后者要是回 403，别人传过什么图就能被一条
        403 问出来（与损耗的 `image_meta` 同一条理由）。
        """
        rows = await self._rows(
            "SELECT capture_id, content_type, byte_size, owner_key, purpose"
            " FROM work_task_images WHERE capture_id = ?",
            (str(capture_id or ""),),
        )
        if not rows:
            return None
        meta = dict(rows[0])
        if await self._image_is_bound(str(meta["capture_id"])):
            return meta
        if str(meta.get("owner_key") or "") == actor.owner_key:
            return meta
        return None

    async def image_bytes(self, actor: TaskActor, capture_id: str) -> Optional[tuple[bytes, str]]:
        """读图字节。权限在 `image_meta` 那一处判，这里只负责把字节取出来。"""
        if self._captures is None:
            return None
        meta = await self.image_meta(actor, capture_id)
        if meta is None:
            return None
        try:
            data = await self._captures.get_async(str(meta["capture_id"]))
        except FileNotFoundError:
            # 库里有行、盘上没文件（备份恢复只搬库不搬目录时会这样）：当它不存在。
            logger.warning("任务照片文件缺失 capture=%s", meta.get("capture_id"))
            return None
        return data, str(meta.get("content_type") or "application/octet-stream")
