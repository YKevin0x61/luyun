#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收通过的实拍 → 订阅了「验收照片」的渠道（企业微信群机器人）。

两步走，与票 03 的卫生文字提醒共用**同一个出站**（`services/wecom_outbox.py`）：

1. **登记** —— 验收的事务里落一行待发：与验收同生共死，不存在"验收成功了但没登记"；
2. **发送** —— 真正的读图（专项还要拼一张前后对照图）与网络发送由**既有的 30 秒
   企微调度循环**驱动，在卫生写锁之外。webhook 是 10 秒超时的网络 IO，塞进
   `@serialized_write` 会把注册、选班、验收一起拖住。

图片在**发送时**渲染：出站行里存的是采集图引用而不是 base64（ADR 0095），所以这里
实现 `render()` 并在 `main.py` 注册给出站。渲染不出来的内容（图被清理 / 装不下）直接
记失败并写明原因，不无限重试。

同一张照片只投递一次：幂等键 = 内容类型 + 业务引用（业务类型 + 验收单引用 + 采集图 id）
+ 目标渠道 —— 重复点验收、弱网重传都不刷屏；员工重拍后再次通过是新的采集图，会再发一次。

写锁契约（重要）：业务库只有一条共享连接，事务边界是全局的，所以这里的每个 DB 动作
都必须落在 `DatabaseManager._write_lock` 里 —— 唯独**网络发送与读文件在锁外**。
`enqueue()` 是例外：它由持有写锁的验收路径调用，自己绝不能再拿一次锁（asyncio 的锁
不可重入，会直接死锁），也**不 commit**（出站行跟着验收的事务一起落库）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Tuple

from database import CHINA_TZ
from services.wecom_outbox import RenderedDelivery, wecom_outbox
from services.wecom_push_topics import TOPIC_HYGIENE_PHOTO, PushTrigger

logger = logging.getLogger(__name__)

# 优先发预览变体（手机上与原图无差，体积只有原图的 3.5%–8%）；超限再退到缩略图，
# 最后才考虑原图。变体缺失（老照片还没回填）时逐级往下退。
IMAGE_VARIANT_ORDER = ("preview", "thumb")


class CaptureImageUnavailable(RuntimeError):
    """要发的采集图已经不在（被清理 / 从没落盘）。

    这是**终态**：重试多少次都还是同一张不存在的图。出站把这句话写进发送记录并记失败
    （票面口径「图片已被清理」），不无限重试。
    """


class CaptureImageTooLarge(RuntimeError):
    """降档到底仍然超过企微 image 上限。

    图不会因为重试而变小，同样是终态：记失败并写明字节数与上限，不静默。
    """


def build_dedupe_key(kind: str, ref_key: str, capture_id: str) -> str:
    return f"{kind}:{ref_key}:{capture_id}"


def _image_too_large(byte_size: int, limit: int) -> CaptureImageTooLarge:
    """超限的原因只有这一处措辞：两个降档兜底出口都写同一句，排查的人不用分辨来源。"""
    return CaptureImageTooLarge(
        f"图片 {byte_size} 字节，超过企业微信 image 上限 {limit} 字节"
    )


class HygieneCaptureSharer:
    """把「验收通过的实拍」交给统一出站：事务里登记，写锁外发送。"""

    def __init__(self, db, captures, *, outbox=None, now=None):
        self._db = db
        self._conn = db._conn
        self._captures = captures
        self._outbox = outbox if outbox is not None else wecom_outbox
        self._now = now or (lambda: datetime.now(CHINA_TZ))
        self._local_write_lock: Optional[asyncio.Lock] = None
        # 出站表是否存在（迁移 0016 可能还没应用）。与 HygieneWork 对
        # hygiene_board_events 扩展列的判据同一形状：缺表要**降级**，不能让验收跟着挂。
        self._outbox_ready: Optional[bool] = None

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
        """登记一次投递，返回是否落了行。

        ``extra_capture_id`` 是配对的那张图（专项的「前」，主图存「后」）：发送时两条
        拼成一张对照图，仍然只算**一次**投递。

        没有采集图 / 没有订阅目标 / 出站表还没建时返回 False（一条不发，也不在以后补发）。
        **调用方必须已经持有写锁**（验收路径就是那样），这里不再拿锁；也**不提交** ——
        出站行跟着调用方的事务一起落库，验收回滚它就跟着消失。
        """
        if not capture_id or not await self._outbox_table_ready():
            return False
        try:
            outbox_ids = await self._outbox.enqueue_topic(
                self._db,
                TOPIC_HYGIENE_PHOTO,
                params={
                    "ref_key": str(ref_key),
                    "capture_id": str(capture_id),
                    "extra_capture_id": str(extra_capture_id or ""),
                },
                trigger=PushTrigger.EVENT,
                business_reference=build_dedupe_key(
                    kind, str(ref_key), str(capture_id)
                ),
                summary=caption,
                commit=False,
            )
        except Exception as exc:
            # 登记失败只记日志：照片发不出去不该把验收拖下水（验收结果由业务表说了算）。
            logger.error("验收照片登记失败（不影响验收）：%s", exc)
            return False
        if not outbox_ids:
            logger.warning("验收照片未登记：没有渠道订阅「验收照片」")
            return False
        return True

    # ── 渲染（发送时读取，由出站派发调用） ────────────────────────────────

    async def render(
        self, row: Mapping[str, Any], params: Mapping[str, Any]
    ) -> RenderedDelivery:
        """这次投递要发的那张图。

        走到这一步才碰磁盘（锁外、秒级）：出站行里只有采集图引用，图可能在这期间被
        保留期清理掉 —— 那就抛错，由出站记失败并写明原因。
        """
        image_bytes, variant = await self._resolve_share_image(params)
        logger.info(
            "验收照片已读取 outbox=%s variant=%s bytes=%s",
            row.get("id"),
            variant,
            len(image_bytes),
        )
        return RenderedDelivery(image_bytes=image_bytes)

    # ── 图片：优先预览变体，超限逐级降档 ──────────────────────────────────

    async def _resolve_share_image(self, params: Mapping[str, Any]) -> Tuple[bytes, str]:
        """这次投递要发的那张图。

        配对图（专项的「前」）在时，现场拼成一张对照图 —— 两条消息合成一条，
        也省一半限流额度。
        """
        paired = str(params.get("extra_capture_id") or "")
        if not paired:
            return await self._resolve_single(str(params.get("capture_id") or ""))
        from services.hygiene.images import compose_before_after

        before = await self._resolve_single(paired)
        after = await self._resolve_single(str(params.get("capture_id") or ""))
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
        oversized: Optional[int] = None
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
            oversized = len(data)
        # 兜底：变体缺失时用原图，但只在它本身就装得下的时候 —— 否则如实报错，
        # 不静默丢掉这张照片。
        try:
            raw = await self._captures.get_async(source_capture_id)
        except FileNotFoundError as exc:
            if oversized is not None:
                # 图还在（只是装不下），别把原因说成"已清理"。
                raise _image_too_large(oversized, WECOM_IMAGE_BYTE_LIMIT) from exc
            raise CaptureImageUnavailable(
                f"图片已被清理（采集图 {source_capture_id} 不存在，可能已过保留期）"
            ) from exc
        if len(raw) <= WECOM_IMAGE_BYTE_LIMIT:
            return raw, "original"
        raise _image_too_large(len(raw), WECOM_IMAGE_BYTE_LIMIT)

    async def _variant_ids(self, source_capture_id: str) -> Dict[str, str]:
        async with self._write_lock:
            cur = await self._conn.execute(
                """SELECT variant, capture_id FROM hygiene_capture_variants
                    WHERE source_capture_id = ?""",
                (source_capture_id,),
            )
            rows = await cur.fetchall()
        return {str(dict(row)["variant"]): str(dict(row)["capture_id"]) for row in rows}

    # ── 出站表就位 ────────────────────────────────────────────────────────

    async def _outbox_table_ready(self) -> bool:
        """出站表是否已就位。

        迁移（`0016_wecom_push_subscriptions.sql`）还没应用时要**降级**：验收照常成功，
        照片暂不推送 —— 否则一条 `INSERT` 撞在缺表上会把整个验收事务变成 aborted，
        把"没发成照片"升级成"验收失败"。结果缓存，不每次问 information_schema；
        探的是 `information_schema`（它一定在），这条 SELECT 自己不会把事务搞脏。
        """
        if self._outbox_ready is None:
            cur = await self._conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
                ("wecom_push_outbox",),
            )
            self._outbox_ready = await cur.fetchone() is not None
            if not self._outbox_ready:
                logger.warning(
                    "wecom_push_outbox 未建（迁移 0016 未应用）：验收照片暂不推送"
                )
        return bool(self._outbox_ready)
