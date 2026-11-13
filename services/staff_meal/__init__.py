#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工餐采购域（ADR 0135，迁移 0053，`.scratch/staff-meal/`）。

模块划分：

- ``store.py`` —— 四张表的数据访问（SQL 与行转换），无业务规则。
- ``menu_import.py`` —— 月度员工餐 Excel → 每日菜单（票 02）。
- ``service.py`` —— 业务规则：导入建单、单据读写权限、提交与重推（票 03）。
- ``push.py`` —— 采购汇总文本渲染与企微入队（票 03/04）。
"""
