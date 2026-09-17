#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库后端适配层。

现在只有 SQLite（既有 aiosqlite 路径）与 PostgreSQL（:mod:`db_core.backend.pg`）
两种形态；方言差异集中在 :mod:`db_core.backend.dialect`。
"""

from db_core.backend import dialect, pg

__all__ = ["dialect", "pg"]
