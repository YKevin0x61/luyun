#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
订单营业额口径（全系统统一）

优先使用 POS 写入的 total_amount；缺失或为 0 时回退 quantity * price。
"""

# 向后兼容：常量已迁至 config.py，避免 database ↔ services 循环导入
from config import ORDER_LINE_REVENUE_SQL  # noqa: F401
