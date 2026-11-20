# -*- coding: utf-8 -*-
"""卫生域的错误类型。

单独一个模块，是因为判据（`judge`）要在不放权限的写口里抛同一个异常，而
`work` 反过来又要用判据 —— 两边都从这里取，就没有循环导入。
"""

from __future__ import annotations


class HygieneWorkError(ValueError):
    """带机器可读码的卫生域错误。`code` 同时是 HTTP 映射与前端文案表的键。"""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)
