#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bounded JPEG derivatives for hygiene captures. Originals remain authoritative."""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

THUMB_MAX_EDGE = 480
THUMB_QUALITY = 78
PREVIEW_MAX_EDGE = 1600
PREVIEW_QUALITY = 85
MAX_DECODE_PIXELS = 40_000_000


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
