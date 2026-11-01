#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「下一周排班表」画成一张 PNG（企微群机器人 image 消息）。

为什么是图不是文字：店里群聊里那张表是照着看的（谁哪天上班、谁休），文字版要靠每行
脑补对齐；一张图在手机上划一次就看完了，也不用担心 2048 字节要拆条。

**浅色**是刻意的：企微群聊是白底，深色卡片截图放进去会与上下文割裂；而这一张是给
员工在手机上看自己的班，不是后台的一部分。

字体：中文字体找不到时返回 ``None``（调用方退化成文字消息）—— 没有中文字体的机器上
画出来是一排方块，那比纯文字更糟。字体路径的探测在 `services/cjk_font.py`。
"""

from __future__ import annotations

import io
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

from services.cjk_font import load_cjk_font
from services.scheduling.week_view import (
    KIND_LEAVE,
    KIND_REST,
    KIND_SHIFT,
    KIND_UNSCHEDULED,
    cell_state,
)
from services.wecom_push_topics import WEEKDAY_LABELS

# ── 版式 ────────────────────────────────────────────────────────────────────
# 这张图在企业微信里会被**按宽度缩到手机屏宽**（约 390pt）再看。所以字够不够清楚不
# 取决于图片本身多大，而取决于「字号 / 图宽」这个比例：首版列宽 148、字号 21（14%），
# 缩完姓名只剩 8.8pt，群里看就是糊的。
#
# 但比例也不是越大越好：把字撑到 32 号时，块里的字几乎顶到格子边、姓名和班次一样重，
# 整张图看着像大字报（用户原话「有点大，看着怪怪的」）。所以停在这组折中值 —— 字号 26
# 配列宽 118，留白还在，缩到手机约 10pt；再往上就得靠减列（拆两张图）而不是放大字了。
MARGIN = 18
TITLE_HEIGHT = 92
HEADER_HEIGHT = 66
ROW_HEIGHT = 70
MIN_DAY_WIDTH = 118
MIN_NAME_WIDTH = 132
# 班次块里的区名用这个字号；表头「周一」下面那行日期更小一点。
SMALL_FONT = 18
HEADER_DATE_FONT = 15

# 浅色主题（见模块注释：截图会出现在白底的群聊里）。
COLOR_PAGE = "#FFFFFF"
COLOR_TITLE_BG = "#111827"
COLOR_TITLE_TEXT = "#FFFFFF"
COLOR_TITLE_SUB = "#D1D5DB"
COLOR_HEADER_BG = "#F3F4F6"
COLOR_HEADER_TEXT = "#1F2937"
COLOR_HEADER_SUB = "#6B7280"
COLOR_NAME_TEXT = "#0F172A"
# 姓名列铺一层极淡的底：左边一列人和右边七列班次天然分开，不用靠放大字号来做层级。
COLOR_NAME_COL = "#F4F7FB"
COLOR_ROW_ALT = "#FAFAFA"
COLOR_WEEKEND = "#EFF3F8"
COLOR_GRID = "#E5E7EB"
COLOR_REST = "#6B7789"
COLOR_LEAVE = "#B45309"
COLOR_UNSCHEDULED = "#8C97A8"
COLOR_FOOTER = "#7C8595"

# 班次底色按**班次名**分（同一个人一周里换班也能一眼看出分界）。色板用完就循环。
SHIFT_PALETTE: Tuple[Tuple[str, str], ...] = (
    # （底色不变，字色比常规调深一档：手机把图缩小后，浅色的细笔划会先糊掉。）
    ("#DBEAFE", "#1E40AF"),  # 蓝
    ("#EDE9FE", "#5B21B6"),  # 紫
    ("#DCFCE7", "#166534"),  # 绿
    ("#FFEDD5", "#9A3412"),  # 橙
    ("#FCE7F3", "#9D174D"),  # 粉
    ("#E0F2FE", "#075985"),  # 青
)

MAX_IMAGE_BYTES = 2 * 1024 * 1024


def _draw_half_rest_dot(draw: ImageDraw.ImageDraw, cell_right: int, bottom: int, color: str) -> None:
    """「半天」的标记：块右下角一个小实心圆点。

    文字里写「机动岗 · 半天」要 6 个字，列宽压到 104 时必然被截断成「机动岗 · 半…」；
    而列宽是清晰度的分母，不能再放宽。所以半天改用一个点，含义写在页脚。位置放在区名
    那一行的右侧空白里，不压班次名。
    """
    r = 3
    cx = cell_right - r - 6
    cy = bottom - 20
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


def _at(x: float, y: float) -> Tuple[int, int]:
    """文字锚点取整。

    Pillow 在 0.5 像素的位置上做抗锯齿：``(center_x, center_y)`` 是浮点时，小字会
    被摊在两个像素上、边缘发灰 —— 缩到手机屏宽之后就是「糊」。所有 draw.text 的坐标
    都经过这里，宁可偏半像素也不要虚。
    """
    return int(round(x)), int(round(y))


def _short_date(day: Optional[date]) -> str:
    return f"{day.month}/{day.day}" if day else ""


def _fit_font(size: int, text: str, max_width: int, draw: ImageDraw.ImageDraw):
    """把字号一路缩到能放下 ``text`` 为止；缩到 14 还放不下就由调用方截断。

    「早班·机动岗」这类名字比「白班」长一倍，固定字号在有的人身上会溢出格子 ——
    宁可这一格小一号，也不要它压到隔壁那一天。
    """
    size = max(14, size)
    while size > 14:
        font = load_cjk_font(size)
        if font is None:
            return None, size
        if draw.textlength(text, font=font) <= max_width:
            return font, size
        size -= 1
    return load_cjk_font(size), size


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    trimmed = text
    while trimmed and draw.textlength(trimmed + "…", font=font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + "…") if trimmed else ""


def _shift_colors(shift_name: str, order: Dict[str, int]) -> Tuple[str, str]:
    index = order.setdefault(shift_name, len(order)) % len(SHIFT_PALETTE)
    return SHIFT_PALETTE[index]


def _schedule_rows(grid: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """按行序（服务端算好的人序）取出每人的 7 格状态。"""
    days = list(grid.get("days") or [])
    rows: List[Mapping[str, Any]] = []
    for employee in grid.get("employees") or []:
        name = str(employee.get("name") or "").strip()
        if not name:
            continue
        cells = employee.get("cells") or {}
        states = [
            cell_state(cells.get(str(day.get("business_date") or "")))
            for day in days
        ]
        rows.append({"name": name, "states": states})
    return rows


def render_weekly_schedule_png(
    grid: Mapping[str, Any], *, generated_at: Optional[datetime] = None
) -> Optional[bytes]:
    """周表 → PNG 字节；本机没有中文字体时返回 ``None``（调用方退文字消息）。"""
    if load_cjk_font(20) is None:
        return None

    days = list(grid.get("days") or [])
    rows = _schedule_rows(grid)
    start = str(grid.get("start") or "")
    end = str(grid.get("end") or "")

    # 先按最小尺寸铺一遍量文字（姓名列宽取决于最长姓名，日期列固定下限就够）。
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    name_font, _ = _fit_font(26, "测", 10_000, probe)
    widest_name = max(
        [probe.textlength(str(row["name"]), font=name_font) for row in rows] or [0]
    )
    name_width = int(max(MIN_NAME_WIDTH, min(240, widest_name + 30)))
    day_width = MIN_DAY_WIDTH

    width = MARGIN * 2 + name_width + day_width * max(1, len(days))
    height = (
        TITLE_HEIGHT
        + HEADER_HEIGHT
        + max(1, len(rows)) * ROW_HEIGHT
        + MARGIN
        + 26
    )

    image = Image.new("RGB", (width, height), COLOR_PAGE)
    draw = ImageDraw.Draw(image)

    # ── 标题条 ──────────────────────────────────────────────────────────
    draw.rectangle([0, 0, width, TITLE_HEIGHT], fill=COLOR_TITLE_BG)
    title_font = load_cjk_font(34)
    draw.text(
        _at(MARGIN, TITLE_HEIGHT / 2 - 2),
        "下周排班",
        font=title_font,
        fill=COLOR_TITLE_TEXT,
        anchor="lm",
        stroke_width=1,
        stroke_fill=COLOR_TITLE_TEXT,
    )
    if start and end:
        range_text = (
            f"{_short_date(date.fromisoformat(start))} – {_short_date(date.fromisoformat(end))}"
        )
        range_font = load_cjk_font(20)
        draw.text(
            _at(width - MARGIN, TITLE_HEIGHT / 2),
            range_text,
            font=range_font,
            fill=COLOR_TITLE_SUB,
            anchor="rm",
        )

    # ── 表头 ────────────────────────────────────────────────────────────
    header_top = TITLE_HEIGHT
    header_bottom = header_top + HEADER_HEIGHT
    draw.rectangle([0, header_top, width, header_bottom], fill=COLOR_HEADER_BG)
    # 周末两列从表头就开始着色：只看行内淡底时，一眼扫过去分不清「周六」和「周日」。
    for index, day in enumerate(days):
        if int(day.get("weekday", 0)) >= 5:
            left = MARGIN + name_width + index * day_width
            draw.rectangle([left, header_top, left + day_width, header_bottom], fill=COLOR_WEEKEND)
    weekday_font = load_cjk_font(21)
    day_date_font = load_cjk_font(HEADER_DATE_FONT)
    for index, day in enumerate(days):
        left = MARGIN + name_width + index * day_width
        center = left + day_width / 2
        weekday = WEEKDAY_LABELS.get(int(day.get("weekday", 0)), "")
        draw.text(
            _at(center, header_top + 24),
            weekday,
            font=weekday_font,
            fill=COLOR_HEADER_TEXT,
            anchor="mm",
        )
        draw.text(
            _at(center, header_top + 46),
            _short_date(
                date.fromisoformat(str(day.get("business_date"))) if day.get("business_date") else None
            ),
            font=day_date_font,
            fill=COLOR_HEADER_SUB,
            anchor="mm",
        )

    # ── 行 ──────────────────────────────────────────────────────────────
    shift_order: Dict[str, int] = {}
    name_font = load_cjk_font(26)
    shift_font = load_cjk_font(26)
    zone_font = load_cjk_font(SMALL_FONT)

    for row_index, row in enumerate(rows):
        top = header_bottom + row_index * ROW_HEIGHT
        bottom = top + ROW_HEIGHT
        if row_index % 2 == 1:
            draw.rectangle([0, top, width, bottom], fill=COLOR_ROW_ALT)
        # 周末两列淡底：一眼看出哪两天是周末，不用去数格子。
        for index, day in enumerate(days):
            if int(day.get("weekday", 0)) >= 5:
                left = MARGIN + name_width + index * day_width
                draw.rectangle([left, top, left + day_width, bottom], fill=COLOR_WEEKEND)

        draw.rectangle([MARGIN, top, MARGIN + name_width, bottom], fill=COLOR_NAME_COL)
        draw.line([MARGIN, bottom, width - MARGIN, bottom], fill=COLOR_GRID, width=1)
        draw.text(
            _at(MARGIN + 8, (top + bottom) / 2),
            _truncate(draw, str(row["name"]), name_font, name_width - 24),
            font=name_font,
            fill=COLOR_NAME_TEXT,
            anchor="lm",
        )

        for index, state in enumerate(row["states"]):
            left = MARGIN + name_width + index * day_width
            cell_left = left + 10
            cell_right = left + day_width - 10
            center_x = (cell_left + cell_right) / 2
            center_y = (top + bottom) / 2
            max_text_width = cell_right - cell_left - 12

            if state.kind == KIND_SHIFT:
                fill, text_color = _shift_colors(state.shift_name, shift_order)
                draw.rounded_rectangle(
                    [cell_left, top + 9, cell_right, bottom - 9],
                    radius=10,
                    fill=fill,
                )
                if state.zone_name:
                    font, _ = _fit_font(26, state.shift_name, max_text_width, draw)
                    draw.text(
                        _at(center_x, center_y - 12),
                        _truncate(draw, state.shift_name, font, max_text_width),
                        font=font,
                        fill=text_color,
                        anchor="mm",
                    )
                    zone_font_fit, _ = _fit_font(
                        SMALL_FONT, state.zone_name, max_text_width, draw
                    )
                    draw.text(
                        _at(center_x, center_y + 15),
                        _truncate(draw, state.zone_name, zone_font_fit, max_text_width),
                        font=zone_font_fit,
                        fill=text_color,
                        anchor="mm",
                    )
                    if state.half_rest:
                        _draw_half_rest_dot(draw, cell_right, bottom, text_color)
                else:
                    font, _ = _fit_font(26, state.shift_name, max_text_width, draw)
                    draw.text(
                        _at(center_x, center_y),
                        _truncate(draw, state.shift_name, font, max_text_width),
                        font=font,
                        fill=text_color,
                        anchor="mm",
                    )
                    if state.half_rest:
                        _draw_half_rest_dot(draw, cell_right, bottom, text_color)
                continue

            if state.kind == KIND_LEAVE:
                label, color, font = "请假", COLOR_LEAVE, shift_font
            elif state.kind == KIND_REST:
                label, color, font = "休", COLOR_REST, shift_font
            elif state.kind == KIND_UNSCHEDULED:
                # 一个破折号在缩到手机宽度后基本看不见，直接写「未排」。
                label, color, font = "未排", COLOR_UNSCHEDULED, shift_font
            else:  # pragma: no cover - KIND_* 已穷举
                label, color, font = "", COLOR_REST, shift_font
            draw.text(_at(center_x, center_y), label, font=font, fill=color, anchor="mm")

    # ── 页脚 ────────────────────────────────────────────────────────────
    footer_font = load_cjk_font(15)
    stamp = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    has_half_rest = any(state.half_rest for row in rows for state in row["states"])
    legend = " · ● 为半天" if has_half_rest else ""
    footer = f"共 {len(rows)} 人{legend} · 排班以「工作台 → 班次表」为准 · 生成于 {stamp}"
    draw.text(
        _at(MARGIN, height - MARGIN + 6),
        footer,
        font=footer_font,
        fill=COLOR_FOOTER,
        anchor="lm",
    )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    data = buffer.getvalue()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"排班图 {len(data)} 字节，超过企业微信 image 限制 {MAX_IMAGE_BYTES} 字节"
        )
    return data
