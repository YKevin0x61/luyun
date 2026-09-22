#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库后端适配层。

后端只剩 PostgreSQL（:mod:`db_core.backend.pg`，SQLite 已随 ADR 0089 退场）；
`?` → `$n` 与 `rowid` 这类方言差异集中在 :mod:`db_core.backend.dialect`。

``pg`` 走延迟导入：它依赖 ``asyncpg``，延迟导入让不需要数据库的纯工具（例如
只跑方言转换的测试）不必在 import 期就加载驱动。
"""

from db_core.backend import dialect

__all__ = ["dialect", "pg"]


def __getattr__(name: str):
    if name == "pg":
        # 用 importlib 直接导入子模块：`from db_core.backend import pg` 会再次触发
        # __getattr__，写成 from ... import 会无限递归。导入完成后 Python 会把子模块
        # 挂到包属性上，后续访问不再走这里。
        import importlib

        return importlib.import_module("db_core.backend.pg")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
