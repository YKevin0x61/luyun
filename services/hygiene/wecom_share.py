#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收通过的实拍 → 卫生群（企业微信群机器人）。

两步走：验收的事务里**只登记**一行 `pending`（与验收同生共死，不存在"验收成功了但没
登记"），真正的发送在写锁之外由这里串行做 —— webhook 是 10 秒超时的网络 IO，塞进
`@serialized_write` 会把注册、选班、验收一起拖住。

两个驱动入口：验收提交后 `kick()` 立即打一枪（低延迟），卫生的逾期循环每轮再
`flush_pending()` 一次兜底（进程重启后积压的 pending 会被捡起来）。

同一张照片只发一次：`dedupe_key`（业务类型 + 业务引用 + 采集图 id）上有唯一约束 ——
重复点验收、弱网重传都不刷屏；员工重拍后再次通过是新的采集图，会再发一次。

写锁契约（重要）：业务库只有一条共享连接，事务边界是全局的，所以这里的每个 DB 动作
都必须落在 `DatabaseManager._write_lock` 里 —— 唯独**网络发送与读文件在锁外**。
`enqueue()` 是例外：它由持有写锁的验收路径调用，自己绝不能再拿一次锁（asyncio 的锁
不可重入，会直接死锁）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from database import CHINA_TZ

logger = logging.getLogger(__name__)

SHARE_STATUS_PENDING = "pending"
SHARE_STATUS_SENT = "sent"
SHARE_STATUS_SKIPPED = "skipped"
SHARE_STATUS_FAILED = "failed"

# 推送日志里的类型标识（「企微推送」页的日志区按它显示；不进定时任务的可选类型）。
SHARE_PUSH_TYPE = "hygiene_photo"

MAX_ATTEMPTS = 3
BATCH_SIZE = 20

# 文字与图片之间、以及多个卫生群之间的间隔：两条消息连着发容易被当成刷屏。
SEND_GAP_SECONDS = 0.3

# 优先发预览变体（手机上与原图无差，体积只有原图的 3.5%–8%）；超限再退到缩略图，
# 最后才考虑原图。变体缺失（老照片还没回填）时逐级往下退。
IMAGE_VARIANT_ORDER = ("preview", "thumb")


def build_dedupe_key(kind: str, ref_key: str, capture_id: str) -> str:
    return f"{kind}:{ref_key}:{capture_id}"


class HygieneCaptureSharer:
    """把「验收通过的实拍」发到卫生群。"""

    def __init__(self, db, captures, *, autoflush: bool = True, now=None):
        self._db = db
        self._conn = db._conn
        self._captures = captures
        self._autoflush = autoflush
        self._now = now or (lambda: datetime.now(CHINA_TZ))
        self._flush_lock = asyncio.Lock()
        self._local_write_lock: Optional[asyncio.Lock] = None
        self._flush_task: Optional[asyncio.Task] = None
        # 分享表是否存在（迁移 0013 可能还没应用）。与 HygieneWork 对
        # hygiene_board_events 扩展列的判据同一形状：缺表要**降级**，不能让验收跟着挂。
        self._has_share_table: Optional[bool] = None

    @property
    def _write_lock(self):
        shared = getattr(self._db, "_write_lock", None)
        if shared is not None:
            return shared
        if self._local_write_lock is None:
            self._local_write_lock = asyncio.Lock()
        return self._local_write_lock

    # ── 登记（在验收的事务里，由调用方 commit） ────────────────────────────

    async def enqueue(
        self,
        *,
        kind: str,
        ref_key: str,
        capture_id: str,
        caption: str = "",
        extra_capture_id: str = "",
    ) -> bool:
        """登记一次分享；同一张照片登记过就返回 False。

        ``extra_capture_id`` 是配对的那张图（专项的「前」，主图存「后」）：两条一起
        拼成一张对照图发出去，仍然只算**一次**分享。

        **调用方必须已经持有写锁**（验收路径就是那样），这里不再拿锁。
        """
        if not capture_id or not await self._share_table_ready():
            return False
        dedupe_key = build_dedupe_key(kind, str(ref_key), str(capture_id))
        cur = await self._conn.execute(
            "SELECT id FROM hygiene_wecom_shares WHERE dedupe_key = ?", (dedupe_key,)
        )
        if await cur.fetchone() is not None:
            return False
        await self._conn.execute(
            """INSERT INTO hygiene_wecom_shares
               (kind, ref_key, capture_id, extra_capture_id, dedupe_key, caption,
                status, attempts, last_error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, '', ?)""",
            (
                kind,
                str(ref_key),
                str(capture_id),
                str(extra_capture_id or ""),
                dedupe_key,
                caption,
                SHARE_STATUS_PENDING,
                self._now().isoformat(),
            ),
        )
        return True

    # ── 触发 ──────────────────────────────────────────────────────────────

    def kick(self) -> None:
        """提交后打一枪：只触发，不等它跑完（调用方这时可能还握着写锁）。"""
        if not self._autoflush:
            return
        if self._flush_task is not None and not self._flush_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # 没有事件循环（同步上下文 / 关停中）：留给循环兜底
            return
        self._flush_task = loop.create_task(self._flush_safely())

    async def _flush_safely(self) -> None:
        try:
            await self.flush_pending()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("卫生群照片发送异常: %s", exc)

    async def flush_pending(self, limit: int = BATCH_SIZE) -> int:
        """把待发的分享发出去；返回真正发成功的条数。同一时刻只有一个 flush 在跑。"""
        async with self._flush_lock:
            if not await self._share_table_ready():
                return 0
            async with self._write_lock:
                rows = await self._pending_rows(limit)
            sent = 0
            for row in rows:
                if await self._send_one(row):
                    sent += 1
            return sent

    # ── 单条 ──────────────────────────────────────────────────────────────

    async def _send_one(self, row: Dict[str, Any]) -> bool:
        share_id = int(row["id"])
        async with self._write_lock:
            groups = await self._hygiene_groups()
        if not groups:
            async with self._write_lock:
                await self._settle(share_id, SHARE_STATUS_SKIPPED, "没有启用中的卫生群")
            logger.warning("卫生群照片未发送：没有启用中的卫生群 share=%s", share_id)
            return False

        # 锁外：读图 + 发网络。这段是秒级的，绝不能占着写锁。
        try:
            image_bytes, variant = await self._resolve_share_image(row)
        except Exception as exc:
            async with self._write_lock:
                await self._retry(share_id, row, f"读取照片失败：{exc}")
            return False

        results: List[Tuple[Dict[str, Any], bool, str]] = []
        for index, group in enumerate(groups):
            if index:
                await asyncio.sleep(SEND_GAP_SECONDS)
            ok, response_text = await self._deliver(group, image_bytes)
            results.append((group, ok, response_text))

        async with self._write_lock:
            for group, ok, response_text in results:
                await self._log(group, ok, response_text, len(image_bytes))
            failures = [
                f"{group.get('name') or group.get('id')}: {response_text}"
                for group, ok, response_text in results
                if not ok
            ]
            if failures:
                await self._retry(share_id, row, "；".join(failures))
                return False
            await self._settle(share_id, SHARE_STATUS_SENT, "")
        logger.info(
            "卫生群照片已发送 share=%s variant=%s bytes=%s groups=%s",
            share_id,
            variant,
            len(image_bytes),
            len(groups),
        )
        return True

    async def _deliver(
        self, webhook: Dict[str, Any], image_bytes: bytes
    ) -> Tuple[bool, str]:
        """一条分享 = **一张图**。

        2026-10 起不再发那行说明文字（用户口径：群里只要照片）。说明文字仍然照旧
        构造并落库到 `hygiene_wecom_shares.caption`，留档；要恢复成图文两条，只需
        在这里把 `send_text` 加回来。
        """
        from services.wecom_push_service import decrypt_webhook_url, wecom_push_service

        try:
            url = decrypt_webhook_url(webhook["webhook_url_encrypted"])
        except Exception as exc:
            return False, f"webhook 地址解密失败：{exc}"
        return await wecom_push_service.send_image(url, image_bytes)

    # ── 图片：优先预览变体，超限逐级降档 ──────────────────────────────────

    async def _resolve_share_image(self, row: Dict[str, Any]) -> Tuple[bytes, str]:
        """这次分享要发的那张图。

        配对图（专项的「前」）在时，现场拼成一张对照图 —— 两条消息合成一条，
        也省一半限流额度。
        """
        paired = str(row.get("extra_capture_id") or "")
        if not paired:
            return await self._resolve_single(str(row["capture_id"]))
        from services.hygiene.images import compose_before_after

        before = await self._resolve_single(paired)
        after = await self._resolve_single(str(row["capture_id"]))
        composed = compose_before_after(before[0], after[0])
        logger.info(
            "前后对照图已合成 %sx%s labels=%s bytes=%s",
            composed.width,
            composed.height,
            composed.label_language,
            len(composed.data),
        )
        return composed.data, "before-after"

    async def _resolve_single(self, source_capture_id: str) -> Tuple[bytes, str]:
        from services.wecom_push_service import WECOM_IMAGE_BYTE_LIMIT

        variants = await self._variant_ids(source_capture_id)
        for name in IMAGE_VARIANT_ORDER:
            variant_id = variants.get(name)
            if not variant_id:
                continue
            try:
                data = await self._captures.get_async(variant_id)
            except FileNotFoundError:
                continue
            if len(data) <= WECOM_IMAGE_BYTE_LIMIT:
                return data, name
        # 兜底：变体缺失时用原图，但只在它本身就装得下的时候 —— 否则如实报错，
        # 不静默丢掉这张照片。
        raw = await self._captures.get_async(source_capture_id)
        if len(raw) <= WECOM_IMAGE_BYTE_LIMIT:
            return raw, "original"
        raise ValueError(
            f"{len(raw)} 字节超过企业微信 image 上限 {WECOM_IMAGE_BYTE_LIMIT} 字节"
        )

    async def _variant_ids(self, source_capture_id: str) -> Dict[str, str]:
        async with self._write_lock:
            cur = await self._conn.execute(
                """SELECT variant, capture_id FROM hygiene_capture_variants
                    WHERE source_capture_id = ?""",
                (source_capture_id,),
            )
            rows = await cur.fetchall()
        return {str(dict(row)["variant"]): str(dict(row)["capture_id"]) for row in rows}

    # ── 落库 ──────────────────────────────────────────────────────────────

    async def _share_table_ready(self) -> bool:
        """分享表是否已就位。

        迁移（`0013_hygiene_wecom_shares.sql`）还没应用时要**降级**：验收照常成功，
        照片暂不推送 —— 否则一条 `INSERT` 撞在缺表上会让整个验收事务回滚，
        把"没发成照片"升级成"验收失败"。结果缓存，不每次问 information_schema。
        """
        if self._has_share_table is None:
            cur = await self._conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
                ("hygiene_wecom_shares",),
            )
            self._has_share_table = await cur.fetchone() is not None
            if not self._has_share_table:
                logger.warning(
                    "hygiene_wecom_shares 未建（迁移 0013 未应用）：验收照片暂不推送"
                )
        return bool(self._has_share_table)

    async def _pending_rows(self, limit: int) -> List[Dict[str, Any]]:
        cur = await self._conn.execute(
            """SELECT id, kind, ref_key, capture_id, extra_capture_id, caption, attempts
                 FROM hygiene_wecom_shares
                WHERE status = ?
                ORDER BY id ASC
                LIMIT ?""",
            (SHARE_STATUS_PENDING, int(limit)),
        )
        return [dict(row) for row in await cur.fetchall()]

    async def _hygiene_groups(self) -> List[Dict[str, Any]]:
        return await self._db.wecom_webhooks_all(
            include_disabled=False, hygiene_only=True
        )

    async def _settle(self, share_id: int, status: str, error: str) -> None:
        await self._conn.execute(
            """UPDATE hygiene_wecom_shares
                  SET status = ?, last_error = ?, sent_at = ?
                WHERE id = ?""",
            (
                status,
                error,
                self._now().isoformat() if status == SHARE_STATUS_SENT else None,
                share_id,
            ),
        )
        await self._conn.commit()

    async def _retry(self, share_id: int, row: Dict[str, Any], error: str) -> None:
        attempts = int(row.get("attempts") or 0) + 1
        status = (
            SHARE_STATUS_FAILED if attempts >= MAX_ATTEMPTS else SHARE_STATUS_PENDING
        )
        await self._conn.execute(
            """UPDATE hygiene_wecom_shares
                  SET attempts = ?, status = ?, last_error = ?
                WHERE id = ?""",
            (attempts, status, str(error)[:500], share_id),
        )
        await self._conn.commit()
        logger.warning(
            "卫生群照片发送失败 share=%s attempts=%s status=%s: %s",
            share_id,
            attempts,
            status,
            error,
        )

    async def _log(
        self,
        webhook: Dict[str, Any],
        ok: bool,
        response_text: str,
        byte_size: int,
    ) -> None:
        """每次发送都进「企微推送」页现有的日志区（类型标成 hygiene_photo）。"""
        try:
            await self._db.wecom_log_add({
                "job_id": None,
                "webhook_id": webhook.get("id"),
                "webhook_name": webhook.get("name", ""),
                "push_type": SHARE_PUSH_TYPE,
                "status": "success" if ok else "failed",
                "message_bytes": int(byte_size),
                "error": "" if ok else str(response_text),
                "response_text": str(response_text),
                "sent_at": self._now().isoformat(),
            })
        except Exception as exc:
            logger.error("写卫生群照片推送日志失败: %s", exc)
