#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bounded JPEG derivatives for hygiene captures. Originals remain authoritative."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

THUMB_MAX_EDGE = 480
THUMB_QUALITY = 78
PREVIEW_MAX_EDGE = 1600
PREVIEW_QUALITY = 85
MAX_DECODE_PIXELS = 40_000_000

# 浏览器 <img> 能直接渲染、同时 PIL 能按内容识别的格式。用白名单而不是黑名单：
# 认不出来的一律降级成 application/octet-stream，浏览器不会把它当文档执行。
SAFE_IMAGE_CONTENT_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
    "ICO": "image/x-icon",
}


def sniff_image_content_type(data: bytes) -> Optional[str]:
    """按字节内容判定图片类型；认不出来返回 None。

    上传的 Content-Type 由客户端说了算：只信它，``image/svg+xml`` 里的
    ``<script>`` 就会跟着同源响应头执行（存储型 XSS）。这里只做 header 级识别，
    不解码整张图，成本很低。
    """
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as probe:
            return SAFE_IMAGE_CONTENT_TYPES.get((probe.format or "").upper())
    except (UnidentifiedImageError, OSError, ValueError):
        return None


class InvalidImageError(ValueError):
    """The uploaded bytes cannot be decoded as a supported image."""


@dataclass(frozen=True)
class GeneratedVariant:
    variant: str
    data: bytes
    width: int
    height: int


class ImageVariantGenerator:
    """Generate display-only thumb and preview JPEGs without enlarging them."""

    def generate(self, data: bytes) -> dict[str, GeneratedVariant]:
        if not data:
            raise InvalidImageError("empty image")
        try:
            with Image.open(io.BytesIO(data)) as source:
                source.load()
                if source.width * source.height > MAX_DECODE_PIXELS:
                    raise InvalidImageError("image dimensions exceed limit")
                oriented = ImageOps.exif_transpose(source)
                if oriented.mode in ("RGBA", "LA") or (
                    oriented.mode == "P" and "transparency" in oriented.info
                ):
                    rgba = oriented.convert("RGBA")
                    flattened = Image.new("RGB", rgba.size, "white")
                    flattened.paste(rgba, mask=rgba.getchannel("A"))
                    oriented = flattened
                elif oriented.mode != "RGB":
                    oriented = oriented.convert("RGB")
                return {
                    "thumb": self._encode(
                        oriented,
                        "thumb",
                        THUMB_MAX_EDGE,
                        THUMB_QUALITY,
                    ),
                    "preview": self._encode(
                        oriented,
                        "preview",
                        PREVIEW_MAX_EDGE,
                        PREVIEW_QUALITY,
                    ),
                }
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            if isinstance(exc, InvalidImageError):
                raise
            raise InvalidImageError("invalid image") from exc

    def _encode(
        self,
        source: Image.Image,
        variant: str,
        max_edge: int,
        quality: int,
    ) -> GeneratedVariant:
        image = source.copy()
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(
            output,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
        )
        return GeneratedVariant(
            variant=variant,
            data=output.getvalue(),
            width=image.width,
            height=image.height,
        )


# ── 前后对照拼图（专项卫生） ────────────────────────────────────────────────

BEFORE_AFTER_MAX_EDGE = 1600
BEFORE_AFTER_GAP = 8
# 质量阶梯：拼图先按最高质量编，超限就往下退，而不是把照片丢掉。
BEFORE_AFTER_QUALITY_LADDER = (85, 70, 55)
BEFORE_AFTER_MAX_BYTES = 2 * 1024 * 1024

# 图内标签优先中文；找不到中文字体就退到拉丁字母。群的说明文字里始终写着
# 「左 前 / 右 后」，所以标签语言变了也不影响看懂。
BEFORE_AFTER_LABELS = (("前", "后"), ("BEFORE", "AFTER"))

# 常见中文字体位置（macOS / Debian 系 / Alpine）。门店服务器不一定装了，
# 装不上只是标签退化成英文，不影响功能。
CJK_FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)


class BeforeAfterTooLarge(InvalidImageError):
    """拼出来的对照图仍然超过群机器人的 image 上限。"""


@dataclass(frozen=True)
class ComposedPair:
    data: bytes
    width: int
    height: int
    label_language: str


@lru_cache(maxsize=8)
def _cjk_font_path() -> Optional[str]:
    for path in CJK_FONT_CANDIDATES:
        if not os.path.isfile(path):
            continue
        try:
            ImageFont.truetype(path, 20)
        except OSError:
            continue
        return path
    return None


@lru_cache(maxsize=16)
def _label_font(size: int):
    """返回 ``(font, (左标签, 右标签))``；中文字体找不到就退到拉丁。"""
    path = _cjk_font_path()
    if path:
        try:
            return ImageFont.truetype(path, size), BEFORE_AFTER_LABELS[0]
        except OSError:  # pragma: no cover - 探测过能加载，这里只是兜底
            pass
    try:
        return ImageFont.load_default(size=size), BEFORE_AFTER_LABELS[1]
    except TypeError:  # pragma: no cover - 老 Pillow 的 load_default 不接受 size
        return ImageFont.load_default(), BEFORE_AFTER_LABELS[1]


def _decode_rgb(data: bytes) -> Image.Image:
    if not data:
        raise InvalidImageError("empty image")
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.load()
            if source.width * source.height > MAX_DECODE_PIXELS:
                raise InvalidImageError("image dimensions exceed limit")
            oriented = ImageOps.exif_transpose(source)
            if oriented.mode in ("RGBA", "LA") or (
                oriented.mode == "P" and "transparency" in oriented.info
            ):
                rgba = oriented.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                return flattened
            if oriented.mode != "RGB":
                return oriented.convert("RGB")
            return oriented
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        if isinstance(exc, InvalidImageError):
            raise
        raise InvalidImageError("invalid image") from exc


def _pair_height(left: Image.Image, right: Image.Image, max_edge: int) -> int:
    """两张图统一到这个高度：拼完的长边不超过 ``max_edge``。"""
    aspect_sum = (left.width / left.height) + (right.width / right.height)
    if aspect_sum <= 0:  # pragma: no cover - 解码过的图不会有零边长
        return max_edge
    by_width = (max_edge - BEFORE_AFTER_GAP) / aspect_sum
    return max(1, int(min(max_edge, by_width)))


def _fit_height(image: Image.Image, height: int) -> Image.Image:
    if image.height == height:
        return image
    width = max(1, round(image.width * height / image.height))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _draw_label(draw: ImageDraw.ImageDraw, font, text: str, x: int, y: int) -> None:
    pad = 8
    box = draw.textbbox((0, 0), text, font=font)
    width = box[2] - box[0] + pad * 2
    height = box[3] - box[1] + pad * 2
    draw.rectangle((x, y, x + width, y + height), fill=(0, 0, 0, 150))
    draw.text((x + pad - box[0], y + pad - box[1]), text, font=font, fill=(255, 255, 255))


def compose_before_after(
    before: bytes,
    after: bytes,
    *,
    max_edge: int = BEFORE_AFTER_MAX_EDGE,
    max_bytes: int = BEFORE_AFTER_MAX_BYTES,
) -> ComposedPair:
    """把「前」「后」两张实拍拼成一张对照图（左前右后），输出 JPEG。

    两张的长宽比可以不同：按**同一个高度**缩放、各自保持比例，中间留一条缝 ——
    变形与怪异留白都出在这里。输出长边不超过 ``max_edge``；按质量阶梯重编码直到
    ≤ ``max_bytes``，仍超就抛 ``BeforeAfterTooLarge``（调用方记失败，不静默丢）。
    """
    left = _decode_rgb(before)
    right = _decode_rgb(after)
    height = _pair_height(left, right, max_edge)
    left = _fit_height(left, height)
    right = _fit_height(right, height)

    canvas = Image.new(
        "RGB", (left.width + BEFORE_AFTER_GAP + right.width, height), (255, 255, 255)
    )
    canvas.paste(left, (0, 0))
    canvas.paste(right, (left.width + BEFORE_AFTER_GAP, 0))

    font, labels = _label_font(max(16, height // 12))
    language = "cjk" if labels == BEFORE_AFTER_LABELS[0] else "latin"
    draw = ImageDraw.Draw(canvas, "RGBA")
    _draw_label(draw, font, labels[0], 0, 0)
    _draw_label(draw, font, labels[1], left.width + BEFORE_AFTER_GAP, 0)

    data = b""
    for quality in BEFORE_AFTER_QUALITY_LADDER:
        output = io.BytesIO()
        canvas.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
        data = output.getvalue()
        if len(data) <= max_bytes:
            return ComposedPair(
                data=data,
                width=canvas.width,
                height=canvas.height,
                label_language=language,
            )
    raise BeforeAfterTooLarge(
        f"前后对照图最小档仍有 {len(data)} 字节，超过 {max_bytes} 字节上限"
    )
