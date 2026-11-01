#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中文字体探测：服务端画图（Pillow）时用哪一份字体。

门店机器与开发机不是同一套字体：macOS 有 PingFang / STHeiti，Debian 装了 Noto CJK 或
文泉驿才画得出汉字。**装不上不要紧**（返回 ``None``，调用方自己决定退化成文字还是
拉丁标签），要紧的是别让某一台机器上的路径写死在业务代码里。

卫生的前后对照图也用这一份探测：CJK 候选路径只有这一处声明，两台机器两套字体、
两边表现一致。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from PIL import ImageFont

# 常见中文字体位置（macOS / Debian 系 / Alpine）。门店服务器不一定装了。
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


@lru_cache(maxsize=8)
def cjk_font_path() -> Optional[str]:
    """第一份**能真的加载**的中文字体路径；一份都没有就返回 ``None``。

    只判断文件存在不够：``.ttc`` 里没有可用字体、或者文件被截断时 ``truetype()`` 会
    抛 ``OSError``，所以这里真加载一次再收下。
    """
    for path in CJK_FONT_CANDIDATES:
        if not os.path.isfile(path):
            continue
        try:
            ImageFont.truetype(path, 20)
        except OSError:
            continue
        return path
    return None


def load_cjk_font(size: int) -> Optional[ImageFont.FreeTypeFont]:
    """指定字号的中文字体；本机没有中文字体时返回 ``None``（**不**退到拉丁字体）。"""
    path = cjk_font_path()
    if not path:
        return None
    try:
        return ImageFont.truetype(path, size)
    except OSError:  # pragma: no cover - cjk_font_path() 已经真加载过
        return None
