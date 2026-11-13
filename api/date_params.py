#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查询参数的日期校验：形状由 FastAPI 的 pattern 挡（422），日历合法性在这里挡（400）。

销售报表、经营分析与导出三个入口共用。以前非法日期直接进报表层，strptime 的
原始异常会变成 500，甚至把异常文本回给前端，用户分不清「参数写错」和「系统坏了」
（本页审查 R18）。
"""

from datetime import datetime

from fastapi import HTTPException

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


def require_valid_date(value: str, name: str) -> None:
    """形状已由 ``Query(pattern=DATE_PATTERN)`` 挡过；这里挡日历上不存在的日期。"""
    if not value:
        raise HTTPException(status_code=422, detail=f"参数 {name} 不能为空")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail=f"参数 {name} 不是合法日期（YYYY-MM-DD）：{value!r}",
        )
