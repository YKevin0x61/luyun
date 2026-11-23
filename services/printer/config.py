#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""商鹏云打印机的配置：存在 app_settings 的 purchase_printer 键里。

**密钥进加密字段**：appsecret（签名用）和 pkey（绑设备用）都能直接驱动这台
打印机，按项目惯例走 credentials_store 的 Fernet（与企微 webhook 同一套），
不以明文进 app_settings；读出来只在内存里传给 SpyunClient。

**空值 = 不改**：设置页不回显密钥，保存时字段留空就保留原值，免得每次调
纸宽都把密钥清掉。要清空得显式传 clear_secret=true。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any, Mapping, Optional

from cryptography.fernet import InvalidToken

from database import CHINA_TZ
from services.credentials_store import _fernet
from services.printer.ticket import DEFAULT_FONT_SCALE, FONT_SCALES, normalize_scale

logger = logging.getLogger(__name__)

__all__ = [
    "SETTINGS_KEY",
    "PrinterConfig",
    "decrypt_secret",
    "encrypt_secret",
    "load_config",
    "mark_bound",
    "save_config",
]

SETTINGS_KEY = "purchase_printer"

#: 纸宽白名单：平台型号里只有这两种热敏票纸会用到我们的排版。
PAPER_WIDTHS = (58, 80)
#: 同一张票重复出几张（接口 times，文档给的范围就是 1–5）。
MAX_TIMES = 5


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(str(value).encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(str(value).encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        # 换过 LUYUN_CRED_KEY 就会解不开，这时只能重新填一次。
        logger.error("商鹏云打印密钥解密失败，需要重新填写：%s", exc)
        return ""


@dataclass(frozen=True)
class PrinterConfig:
    """一台商鹏云打印机 + 一个开发者账号的配置。"""

    enabled: bool = False
    appid: str = ""
    sn: str = ""
    appsecret: str = ""
    pkey: str = ""
    paper_width: int = 58
    times: int = 1
    line_break: str = "br"
    #: 字号档（normal / tall / big / huge），见 services/printer/ticket.py。
    #: 58mm 上正常字号店员反馈太小，默认放大一倍。
    font_scale: str = DEFAULT_FONT_SCALE
    bound_at: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self.appid and self.appsecret and self.sn)

    def public(self) -> dict:
        """给前端的视图：密钥只回「有没有填」，不回原值。"""
        return {
            "enabled": self.enabled,
            "appid": self.appid,
            "sn": self.sn,
            "paper_width": self.paper_width,
            "times": self.times,
            "line_break": self.line_break,
            "font_scale": self.font_scale,
            "bound": bool(self.bound_at),
            "bound_at": self.bound_at,
            "has_appsecret": bool(self.appsecret),
            "has_pkey": bool(self.pkey),
            "configured": self.configured,
        }


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _as_scale(value: Any) -> str:
    """认不出来的字号退回默认档，不把脏配置带进排版。"""
    text = str(value or "").strip().lower()
    return text if text in FONT_SCALES else normalize_scale(text)


def _from_stored(raw: Mapping[str, Any]) -> PrinterConfig:
    paper_width = _as_int(raw.get("paper_width"), 58)
    if paper_width not in PAPER_WIDTHS:
        paper_width = 58
    times = _as_int(raw.get("times"), 1)
    if times < 1 or times > MAX_TIMES:
        times = 1
    return PrinterConfig(
        enabled=_as_bool(raw.get("enabled")),
        appid=str(raw.get("appid") or "").strip(),
        sn=str(raw.get("sn") or "").strip(),
        appsecret=decrypt_secret(str(raw.get("appsecret_enc") or "")),
        pkey=decrypt_secret(str(raw.get("pkey_enc") or "")),
        paper_width=paper_width,
        times=times,
        line_break=str(raw.get("line_break") or "br").strip().lower() or "br",
        font_scale=_as_scale(raw.get("font_scale")),
        bound_at=raw.get("bound_at") or None,
    )


def _to_stored(config: PrinterConfig) -> dict:
    return {
        "enabled": config.enabled,
        "appid": config.appid,
        "sn": config.sn,
        "paper_width": config.paper_width,
        "times": config.times,
        "line_break": config.line_break,
        "font_scale": config.font_scale,
        "appsecret_enc": encrypt_secret(config.appsecret) if config.appsecret else "",
        "pkey_enc": encrypt_secret(config.pkey) if config.pkey else "",
        "bound_at": config.bound_at,
        "saved_at": datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
    }


async def load_config(db) -> PrinterConfig:
    raw = await db.settings_get_json(SETTINGS_KEY, None)
    if not isinstance(raw, Mapping):
        return PrinterConfig()
    try:
        return _from_stored(raw)
    except Exception as exc:  # noqa: BLE001 - 配置脏了不该让打印接口 500
        logger.error("读取商鹏云打印配置失败：%s", exc)
        return PrinterConfig()


async def save_config(db, payload: Mapping[str, Any]) -> PrinterConfig:
    """按 payload 局部更新；密钥字段留空 = 保留原值。"""
    current = await load_config(db)
    updates: dict[str, Any] = {}

    if "enabled" in payload:
        updates["enabled"] = _as_bool(payload.get("enabled"))
    if "appid" in payload:
        updates["appid"] = str(payload.get("appid") or "").strip()
    if "sn" in payload:
        updates["sn"] = str(payload.get("sn") or "").strip()
    if "paper_width" in payload:
        width = _as_int(payload.get("paper_width"), current.paper_width)
        updates["paper_width"] = width if width in PAPER_WIDTHS else current.paper_width
    if "times" in payload:
        times = _as_int(payload.get("times"), current.times)
        updates["times"] = times if 1 <= times <= MAX_TIMES else current.times
    if "line_break" in payload:
        token = str(payload.get("line_break") or "").strip().lower()
        updates["line_break"] = "lf" if token in ("lf", "newline", "0") else "br"
    if "font_scale" in payload:
        updates["font_scale"] = _as_scale(payload.get("font_scale"))

    clear_secret = _as_bool(payload.get("clear_secret"))
    secret = str(payload.get("appsecret") or "").strip()
    if clear_secret:
        updates["appsecret"] = ""
    elif secret:
        updates["appsecret"] = secret
    pkey = str(payload.get("pkey") or "").strip()
    if clear_secret:
        updates["pkey"] = ""
    elif pkey:
        updates["pkey"] = pkey

    # 账号或设备换了，之前的绑定记录就作废，下次打印会重新 add。
    if updates.get("sn") not in (None, current.sn) or updates.get("appid") not in (None, current.appid):
        updates["bound_at"] = None

    merged = replace(current, **updates)
    if not await db.settings_set_json(SETTINGS_KEY, _to_stored(merged)):
        raise RuntimeError("打印配置没写进数据库")
    return merged


async def mark_bound(db, config: PrinterConfig, stamp: str) -> PrinterConfig:
    """记住这台设备已经绑过 appid，下次打印不再走 /printer/add。"""
    merged = replace(config, bound_at=stamp)
    if not await db.settings_set_json(SETTINGS_KEY, _to_stored(merged)):
        raise RuntimeError("打印配置没写进数据库")
    return merged


def describe(config: PrinterConfig) -> dict:
    """给日志/接口复用的、不含密钥的摘要。"""
    data = asdict(config)
    data.pop("appsecret", None)
    data.pop("pkey", None)
    return data
