#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工档案字段：身份证校验、日期/底薪归一、健康证到期派生与「待补」判定。

**健康证到期只有这一份实现**：花名册（管理端）、`/api/hygiene/staff/me`（员工端）与
超管首页待办三处都调 :func:`health_cert_status`。到期日**不落库**（办理日期 + 12 个月
是派生值，见 ADR 0098 的口径），所以三处不可能算出两个答案，改规则也只改这里。

口径（2026-10-07 用户裁定，`.scratch/roster-redesign/design.md` §0.2 / §13）：

* 有效期 = 办理日期 + 12 个月（同日）；
* **到期日当天算临期最后一天**，过期从到期日**次日**起算（等价于 ``到期日 < 今天``）——
  这样「有效期至 X」与「当天仍可用」不打架；
* 临期 = ``0 ≤ 到期日 − 今天 ≤ 30`` 天，其余为 ``ok``，没有办理日期为 ``none``；
* 一律按**北京时自然日**算（不是营业日：证件上的有效期是日历日，06:00 那个切点是
  POS 的营业日口径）。

校验失败的形状：一律抛 :class:`ProfileError`，``code`` 就是 ``api/hygiene.py`` 的
``_ERROR_DETAILS`` 键 —— 文案在那一处（注册、PATCH、导出共用），这一层只说"错在哪"。

**不碰敏感数据的落点**：这里不做任何日志，也不往异常消息里塞字段值。
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime
from typing import Any, Mapping, Optional

# 健康证：办理之日起有效期一年；到期前 30 天开始提醒。
HEALTH_CERT_VALID_MONTHS = 12
HEALTH_CERT_SOON_DAYS = 30

# 底薪：整数元/月（界面 type=number、min=0、step=1、max=999999）。
BASE_SALARY_MAX = 999999

# 「待补」看的是这四项，顺序 = 抽屉里那四个字段的顺序（只影响报错措辞，不影响判定）。
PROFILE_FIELDS = ("id_card_no", "health_cert_date", "base_salary", "hire_date")

HEALTH_CERT_STATE_NONE = "none"
HEALTH_CERT_STATE_OK = "ok"
HEALTH_CERT_STATE_SOON = "soon"
HEALTH_CERT_STATE_EXPIRED = "expired"
HEALTH_CERT_STATES = frozenset(
    {
        HEALTH_CERT_STATE_NONE,
        HEALTH_CERT_STATE_OK,
        HEALTH_CERT_STATE_SOON,
        HEALTH_CERT_STATE_EXPIRED,
    }
)

# GB 11643 的加权因子与校验码（末位可为 X，入库前归一成大写）。
_ID_CARD_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CARD_CHECK_CODES = "10X98765432"
_ID_CARD_LAST_RE = re.compile(r"^[0-9X]$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ProfileError(ValueError):
    """档案字段校验失败。

    ``code`` 是给 ``api/hygiene.py`` 的 ``_ERROR_DETAILS`` 查文案用的键；``str(exc)``
    就是那个 code，所以接口层既有的 ``except ValueError as exc: _error_detail(str(exc))``
    不用为它改一行。**消息里只有 code，没有字段值**（身份证号不进异常消息）。
    """

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def is_blank(value: Any) -> bool:
    """「没填」的唯一判据：None 或全是空白的字符串。``0`` 与 ``False`` 都算填了。

    底薪 ``0`` 是合法值（不待补），所以这里**不能**用 ``if not value``。
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def id_card_check_digit(body17: str) -> str:
    """前 17 位 → 第 18 位（校验位）。"""
    total = sum(int(ch) * weight for ch, weight in zip(body17, _ID_CARD_WEIGHTS))
    return _ID_CARD_CHECK_CODES[total % 11]


def normalize_id_card(raw: Any) -> Optional[str]:
    """身份证号归一：空 → ``None``；形状/校验位不过 → :class:`ProfileError`。

    只做**形状与校验位**两件事（票面要求），不查地址码与出生日期的真实性：库里存的是
    别人报上来的号，核对证件是人的事，机器只挡"打字打错了"。
    """
    value = _text(raw)
    if not value:
        return None
    upper = value.upper()
    if len(upper) != 18 or not upper[:17].isdigit():
        raise ProfileError("id_card_length")
    if not _ID_CARD_LAST_RE.match(upper[17]):
        raise ProfileError("id_card_checksum")
    if upper[17] != id_card_check_digit(upper[:17]):
        raise ProfileError("id_card_checksum")
    return upper


def normalize_date(raw: Any) -> Optional[str]:
    """``YYYY-MM-DD`` 归一：空 → ``None``；形状/日历不过 → ``bad_date``。"""
    value = _text(raw)
    if not value:
        return None
    if not _DATE_RE.match(value):
        raise ProfileError("bad_date")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        # 形状对、日历上没有这一天（2026-02-30）。
        raise ProfileError("bad_date") from exc


def normalize_health_cert_date(raw: Any, today: date) -> Optional[str]:
    """健康证办理日期：日期形状 + **不得晚于今天**（北京时自然日）。

    注册与 PATCH 共用；将来日期是笔误而不是提前建档（入职日期允许将来，这一项不允许）。
    """
    value = normalize_date(raw)
    if value is not None and date.fromisoformat(value) > today:
        raise ProfileError("health_cert_future")
    return value


def normalize_base_salary(raw: Any) -> Optional[int]:
    """底薪：空 → ``None``；否则必须是 0–999999 的**整数元**。

    ``0`` 是合法值（不是"没填"）；``12.5``、``"abc"``、负数、超上限各有各的 code。
    """
    if is_blank(raw):
        return None
    if isinstance(raw, bool):
        raise ProfileError("base_salary_not_integer")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        if raw != raw or raw in (float("inf"), float("-inf")) or raw != int(raw):
            raise ProfileError("base_salary_not_integer")
        value = int(raw)
    else:
        text = _text(raw)
        if not re.match(r"^-?\d+$", text):
            raise ProfileError("base_salary_not_integer")
        value = int(text)
    if value < 0:
        raise ProfileError("base_salary_negative")
    if value > BASE_SALARY_MAX:
        raise ProfileError("base_salary_too_large")
    return value


def add_months(value: str, months: int) -> str:
    """``YYYY-MM-DD`` + N 个月（同日；该月没有这一天时取月末）。

    只处理本模块用到的形状：坏值抛 ``ValueError``，由调用方决定降级。
    """
    day = date.fromisoformat(value)
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1])).isoformat()


def health_cert_expires_on(health_cert_date: Any) -> Optional[str]:
    """办理日期 → 有效期至（+12 个月）。空值或坏值都回 ``None``。

    坏值（手改过的库、导入的历史数据）按"没填"渲染，不抛：花名册整页不该因为一行
    坏日期打不开。写入侧已经在 :func:`normalize_health_cert_date` 挡过。
    """
    value = _text(health_cert_date)
    if not value:
        return None
    try:
        return add_months(value, HEALTH_CERT_VALID_MONTHS)
    except ValueError:
        return None


def as_date(value: Any) -> date:
    """``date`` / ``datetime`` / ``YYYY-MM-DD`` 统一成 ``date``（供调用方传"今天"）。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(_text(value))


def health_cert_status(health_cert_date: Any, today: Any) -> dict:
    """健康证的三件事：有效期至、状态、还剩几天（**派生值的唯一实现**）。

    ``today`` 传 ``date`` / ``datetime`` / ``YYYY-MM-DD``；调用方给的是**北京时自然日**
    （``EmployeeAccounts`` 的 ``_now_dt().date()``）。
    """
    expires_on = health_cert_expires_on(health_cert_date)
    if expires_on is None:
        return {
            "expires_on": None,
            "state": HEALTH_CERT_STATE_NONE,
            "days_left": None,
        }
    days_left = (date.fromisoformat(expires_on) - as_date(today)).days
    if days_left < 0:
        state = HEALTH_CERT_STATE_EXPIRED
    elif days_left <= HEALTH_CERT_SOON_DAYS:
        state = HEALTH_CERT_STATE_SOON
    else:
        state = HEALTH_CERT_STATE_OK
    return {"expires_on": expires_on, "state": state, "days_left": days_left}


def profile_incomplete(row: Mapping[str, Any]) -> bool:
    """「待补」= 四项（身份证号 / 健康证办理日期 / 底薪 / 入职日期）任一为空。

    底薪 ``0`` 不算空；停止使用的员工也算（调用方自己决定要不要过滤停用的人）。
    """
    return any(is_blank(row.get(field)) for field in PROFILE_FIELDS)


def approve_gate(row: Mapping[str, Any]) -> Optional[str]:
    """批准门槛：``base_salary`` 与 ``hire_date`` 齐了才回 ``None``，否则回错误 code。

    返回 code（而不是文案）是为了让"缺哪几项"只有一处判据：接口层按 code 查文案，
    也顺便让 callers 能对 code 断言。``/enable``（重新启用）**不走这里** —— 恢复不是新入职。
    """
    missing_salary = is_blank(row.get("base_salary"))
    missing_hire = is_blank(row.get("hire_date"))
    if missing_salary and missing_hire:
        return "approve_missing_both"
    if missing_salary:
        return "approve_missing_base_salary"
    if missing_hire:
        return "approve_missing_hire_date"
    return None


__all__ = [
    "BASE_SALARY_MAX",
    "HEALTH_CERT_SOON_DAYS",
    "HEALTH_CERT_STATES",
    "HEALTH_CERT_STATE_EXPIRED",
    "HEALTH_CERT_STATE_NONE",
    "HEALTH_CERT_STATE_OK",
    "HEALTH_CERT_STATE_SOON",
    "HEALTH_CERT_VALID_MONTHS",
    "PROFILE_FIELDS",
    "ProfileError",
    "add_months",
    "approve_gate",
    "as_date",
    "health_cert_expires_on",
    "health_cert_status",
    "id_card_check_digit",
    "is_blank",
    "normalize_base_salary",
    "normalize_date",
    "normalize_health_cert_date",
    "normalize_id_card",
    "profile_incomplete",
]
