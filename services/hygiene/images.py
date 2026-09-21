#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bounded JPEG derivatives for hygiene captures. Originals remain authoritative."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageOps, UnidentifiedImageError

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
