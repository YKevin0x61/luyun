#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把当前标准图连同标注烘焙成图片，按责任区打包成 zip。

导出是给人看的成品：圆圈、箭头、批注不能再是前端叠的一层 DOM，必须画进像素里。
渲染口径对着 ``admin-web/src/components/hygiene/HygieneMarkupOverlay.vue`` 抄 ——
归一化坐标（0-1）、圈直径 ``max(16px, r*2*短边)``、箭头描边 1.6px、批注在锚点
上方居中、主色 ``#3fe0b0``。前端那套 px 是按 ~640px 的展示宽度定的，导出图通常
1600px 起，所以这里用 ``BASE_EDGE`` 把 px 换算成与图片尺寸成比例的值，观感才一致。

批注文字要用中文字体；找不到时退回 PIL 位图字体并告警——不因此让整包导出失败。
"""

from __future__ import annotations

import io
import logging
import math
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps

logger = logging.getLogger(__name__)

# 前端标注所在的典型展示宽度。用它把「前端 px」换算成按图片尺寸成比例的值。
BASE_EDGE = 640.0
MARK_RGB = (63, 224, 176)
CAPTION_BG = (8, 22, 20, 235)
CAPTION_LINE = (63, 224, 176, 160)
GLOW_ALPHA = 60
JPEG_QUALITY = 90
# optimize=True 会让 libjpeg 跑多轮霍夫曼表优化，单张多花约四成时间；导出是几十张
# 的批量活儿，这点体积收益不值得。实测 1254px 的图 4ms → 2ms。
JPEG_OPTIMIZE = False
# 存进去的本来就是压过的 JPEG，deflate 的体积收益接近 0，CPU 却照烧——几十 MB
# 就是一两秒。直接 STORED。
ARCHIVE_COMPRESSION = zipfile.ZIP_STORED
# 并发渲染张数。PIL 的解码/编码在 C 层会放掉 GIL，所以多线程是真并行；但门店机器
# 通常 2-4 核，而且要留出余量给同一进程里的 KDS 与爬虫，所以封顶 4。
MAX_RENDER_WORKERS = 4

# 中文字体候选：先 Linux（生产/Docker），再 macOS（开发机）。都找不到就只能
# 用默认位图字体，中文会失真——日志里会说明，不让整包导出失败。
FONT_CANDIDATES: Sequence[str] = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
)

_UNSAFE_PATH_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_font_cache: dict[int, ImageFont.ImageFont] = {}
_font_warned = False


def _load_font(size: int) -> ImageFont.ImageFont:
    global _font_warned
    cached = _font_cache.get(size)
    if cached is not None:
        return cached
    for path in FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(path, size)
        except (OSError, ValueError):
            continue
        _font_cache[size] = font
        return font
    if not _font_warned:
        _font_warned = True
        logger.warning(
            "导出标准图：本机没有可用的中文字体（已试 %s 个路径），批注文字会失真；"
            "Linux 上装 fonts-noto-cjk 即可",
            len(FONT_CANDIDATES),
        )
    font = ImageFont.load_default()
    _font_cache[size] = font
    return font


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _draw_circle(draw: ImageDraw.ImageDraw, mark: dict, width: int, height: int,
                 edge: int, scale: float, line_w: int) -> None:
    cx = _float(mark.get("x")) * width
    cy = _float(mark.get("y")) * height
    diameter = max(16 * scale, _float(mark.get("r"), 0.08) * 2 * edge)
    radius = diameter / 2
    box = [cx - radius, cy - radius, cx + radius, cy + radius]
    # 前端靠 drop-shadow 让圈从杂乱的背景里浮出来；这里用一圈半透明描边近似。
    glow = max(2.0, line_w * 2.0)
    draw.ellipse(
        [box[0] - glow, box[1] - glow, box[2] + glow, box[3] + glow],
        outline=(*MARK_RGB, GLOW_ALPHA),
        width=line_w * 3,
    )
    draw.ellipse(box, outline=MARK_RGB, width=line_w)


def _draw_arrow(draw: ImageDraw.ImageDraw, mark: dict, width: int, height: int,
                scale: float, line_w: int) -> None:
    x1, y1 = _float(mark.get("x1")) * width, _float(mark.get("y1")) * height
    x2, y2 = _float(mark.get("x2")) * width, _float(mark.get("y2")) * height
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 1:
        return
    ux, uy = dx / length, dy / length
    head = max(8.0, 12 * scale)
    # 线画到箭头底部：画到尖端会让线从三角里戳出来。
    base_x, base_y = x2 - ux * head, y2 - uy * head
    draw.line(
        [(x1, y1), (base_x, base_y)],
        fill=(*MARK_RGB, GLOW_ALPHA),
        width=line_w * 3,
    )
    draw.line([(x1, y1), (base_x, base_y)], fill=MARK_RGB, width=line_w)
    half = head * 0.42
    draw.polygon(
        [(x2, y2), (base_x - uy * half, base_y + ux * half), (base_x + uy * half, base_y - ux * half)],
        fill=MARK_RGB,
    )


def _draw_caption(draw: ImageDraw.ImageDraw, mark: dict, width: int, height: int,
                  scale: float, line_w: int) -> None:
    text = str(mark.get("text") or "").strip()
    if not text:
        return
    font_size = max(14, round(15 * scale))
    font = _load_font(font_size)
    text_box = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = text_box[2] - text_box[0], text_box[3] - text_box[1]
    pad_x = max(6, round(font_size * 0.6))
    pad_y = max(3, round(font_size * 0.28))
    box_w, box_h = text_w + pad_x * 2, text_h + pad_y * 2
    anchor_x = _float(mark.get("x")) * width
    anchor_y = _float(mark.get("y")) * height
    # 对应前端的 transform: translate(-50%, -110%)：水平居中，整体浮在锚点上方。
    left = anchor_x - box_w / 2
    bottom = anchor_y - box_h * 0.1
    top = bottom - box_h
    draw.rounded_rectangle(
        [left, top, left + box_w, bottom],
        radius=max(4, round(6 * scale)),
        fill=CAPTION_BG,
        outline=CAPTION_LINE,
        width=max(1, line_w // 2),
    )
    draw.text(
        (left + pad_x - text_box[0], top + pad_y - text_box[1]),
        text,
        font=font,
        fill=MARK_RGB,
    )


def render_markup(data: bytes, markup: Optional[Iterable[dict]]) -> bytes:
    """把标注画进图片，返回 JPEG 字节；标注为空时等于重新编码原图。"""
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        # 手机直拍带 EXIF 方向，不摆正的话标注会画到错的位置。
        image = ImageOps.exif_transpose(source).convert("RGB")
    width, height = image.size
    edge = min(width, height)
    scale = edge / BASE_EDGE
    line_w = max(2, round(2 * scale))
    draw = ImageDraw.Draw(image, "RGBA")
    for mark in markup or []:
        if not isinstance(mark, dict):
            continue
        kind = mark.get("kind")
        try:
            if kind == "circle":
                _draw_circle(draw, mark, width, height, edge, scale, line_w)
            elif kind == "arrow":
                _draw_arrow(draw, mark, width, height, scale, line_w)
            elif kind == "caption":
                _draw_caption(draw, mark, width, height, scale, line_w)
        except (TypeError, ValueError, OSError):
            # 一条画不出来的标注不该毁掉整包导出。
            logger.debug("导出标准图：跳过一条画不出来的标注 %r", mark, exc_info=True)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=JPEG_OPTIMIZE)
    return buffer.getvalue()


def safe_component(raw: str, fallback: str) -> str:
    """清洗 zip 内的路径分量：不留分隔符、控制字符，也不留首尾的点与空格。"""
    cleaned = _UNSAFE_PATH_CHARS.sub("_", str(raw or "")).strip().strip(".")
    cleaned = cleaned.strip()
    return cleaned[:80] or fallback


def render_workers() -> int:
    return max(1, min(MAX_RENDER_WORKERS, os.cpu_count() or 1))


def write_archive(
    entries: Iterable[tuple[str, str, Path, Optional[list]]],
    target: Path,
    on_progress: Optional[Callable[[int, int], None]] = None,
    workers: Optional[int] = None,
) -> tuple[int, int]:
    """把 (责任区, 检查项, 原图路径, 标注) 逐张烘焙后写进 zip。

    逐张读盘再写，不把整包图片同时留在内存里——门店标准图上百张时那是好几百 MB。
    渲染走线程池（`ThreadPoolExecutor.map` 保序，zip 只能顺序写），比串行快数倍；
    完成后立刻落进 zip，不驻留整包。

    ``on_progress(已完成, 总数)`` 每张调一次，供调用方报进度。

    返回 ``(写入张数, 读取失败张数)``；单张读不出来只跳过它，不让整包失败。
    """
    items = list(entries)
    total = len(items)
    written = 0
    failed = 0
    used: set[str] = set()

    def render_one(item):
        zone_name, item_name, path, markup = item
        try:
            return zone_name, item_name, render_markup(Path(path).read_bytes(), markup)
        except (OSError, ValueError) as exc:
            logger.warning(
                "导出标准图：读不出或解不开这张图，已跳过 zone=%s item=%s: %s",
                zone_name, item_name, exc,
            )
            return zone_name, item_name, None

    pool_size = max(1, workers or render_workers())
    with zipfile.ZipFile(target, "w", ARCHIVE_COMPRESSION) as archive:
        with ThreadPoolExecutor(max_workers=pool_size) as pool:
            for zone_name, item_name, blob in pool.map(render_one, items, chunksize=1):
                if blob is None:
                    failed += 1
                else:
                    written += 1
                    folder = safe_component(zone_name, "未命名责任区")
                    stem = safe_component(item_name, f"标准图-{written}")
                    member = f"{folder}/{stem}.jpg"
                    if member in used:
                        member = f"{folder}/{stem}-{written}.jpg"
                    used.add(member)
                    archive.writestr(member, blob)
                if on_progress:
                    on_progress(written + failed, total)
    return written, failed

