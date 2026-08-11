#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper 包内部共享常量：北京时区、data 目录路径、会话异常类型。

供 PosSession / DeliveryBillTracker / RestaurantScraper 等模块复用。
"""

from datetime import timezone, timedelta
from pathlib import Path

CHINA_TZ = timezone(timedelta(hours=8))

# 基于文件位置计算 data 目录，确保不受 cwd 影响
DATA_DIR = Path(__file__).parent.parent / "data"


class ScraperSessionError(RuntimeError):
    """POS 会话不可用且无法自动恢复。

    刻意设计成异常而非返回空列表：主循环的 ``ScraperFailureTracker`` 只按异常计数，
    静默返回 ``[]`` 会被当成"抓取成功"，导致会话挂死时健康状态仍是 ok、且不发告警。
    """

# 仅收录实测/明确指向鉴权的文案。刻意不含 "token"/"用户名" 这类宽泛子串——
# 它们会把普通业务错误误判成会话失效，触发无谓的重新登录（一次约 25~48s）。
_POS_AUTH_FAILURE_HINTS = (
    "未登录",
    "请登录",
    "本系统登录",
    "登录超时",
    "重新登录",
    "登录失效",
    "令牌",
    "鉴权",
    "无权",
    "没有权限",
    "密码错误",
)

# cy7mm 业务 API 顶层 code 恒为 null（走 success 字段），sly 2.0 侧成功码为 2000。
# 182 份抓包中从未出现 1000，故不再把它当作鉴权失败。
_POS_AUTH_FAILURE_CODES = (401, 403, 40101, "401", "403", "40101")


def pos_response_indicates_auth_failure(payload: dict) -> bool:
    """根据 POS JSON 响应体判断是否未登录 / 无权。"""
    if payload.get("code") in _POS_AUTH_FAILURE_CODES:
        return True
    combined = " ".join(
        str(payload.get(key) or "")
        for key in ("message", "msg", "errorMessage", "errorMsg", "errMsg", "error")
    )
    return any(hint in combined for hint in _POS_AUTH_FAILURE_HINTS)


def pos_session_lost(status: int, body) -> bool:
    """POS 响应是否表明 cy7mm 会话已失效（需要重新登录）。

    会话过期时 cy7mm 可能不返回 JSON，而是重定向到登录页的 HTML，
    此时 ``body`` 是字符串——只判 dict 会漏掉这种情况。
    """
    if status in (401, 403):
        return True
    if isinstance(body, str):
        lowered = body.lower()
        return "<html" in lowered or "/login" in lowered
    if isinstance(body, dict):
        return pos_response_indicates_auth_failure(body)
    return False
