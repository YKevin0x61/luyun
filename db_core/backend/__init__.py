#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库后端适配层。

现在有 SQLite（既有 aiosqlite 路径）与 PostgreSQL（:mod:`db_core.backend.pg`）
两种形态；方言差异集中在 :mod:`db_core.backend.dialect`。

``pg`` 走延迟导入：它依赖 ``asyncpg``，而默认的 SQLite 部署并不需要装它。
模块级 eager import 会让 asyncpg 变成硬依赖——CI 只装 ``requirements.txt``，
跑方言测试时就会 ImportError。
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
