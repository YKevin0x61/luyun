#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工注册的测试夹具：合法身份证号 + 注册请求体（2026-10-07 起两项必填）。

`design.md` §8：注册页必填**身份证号**与**健康证办理日期**。本仓多处 HTTP 用例要造
注册请求，这里给一份可复用、而且互相不撞号的写法（身份证号在库里有部分唯一索引：
一个用例里注册两个人就必须用不同的 serial）。

校验位在这里**独立**算一遍，刻意不 import 被测实现（`services/identity/profile.py`）：
那份若把加权因子写错，这里造的"合法号"会被它拒绝，用例就会红 —— 两处同源则一点
保护都没有。
"""

from __future__ import annotations

_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_CHECK_CODES = "10X98765432"

# 健康证办理日期的默认值：**足够早**，早于测试里注入的固定时刻（2026-09-13）也早于
# 真实时钟 —— 「不得晚于今天」这条守卫不会在这些用例里误伤。
DEFAULT_HEALTH_CERT_DATE = "2020-01-01"


def id_card(serial: int = 1, *, region: str = "110105", birth: str = "19900307") -> str:
    """按序号造一个**校验位合法**的 18 位身份证号。"""
    body = f"{region}{birth}{serial % 1000:03d}"
    assert len(body) == 17, body
    check = _CHECK_CODES[sum(int(ch) * w for ch, w in zip(body, _WEIGHTS)) % 11]
    return body + check


def register_body(
    *,
    name: str,
    phone: str,
    password: str,
    serial: int = 1,
    health_cert_date: str = DEFAULT_HEALTH_CERT_DATE,
) -> dict:
    """注册请求体（两项必填都在）。"""
    return {
        "name": name,
        "phone": phone,
        "password": password,
        "id_card_no": id_card(serial),
        "health_cert_date": health_cert_date,
    }


def approve_body(*, base_salary: int = 6000, hire_date: str = "2025-01-01") -> dict:
    """批准门槛（底薪 + 入职日期）要用的一小段 PATCH 体。"""
    return {"base_salary": base_salary, "hire_date": hire_date}
