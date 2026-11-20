# -*- coding: utf-8 -*-
"""卫生域里「一行现在是什么状态」的词表。

日常/专项/整改与仪容各有一套历史命名（中文与英文），但它们都是同一件事的
标签，而且判据要按状态判断「这一行还有没有可判的」。放在一处，免得判据再抄
一份字面量。

改动这里等于改库里的存量值（`business_date` 一样是写入时落定的字符串），
不要为了好看改名。
"""

from __future__ import annotations

# 日常 / 专项 / 整改单（库里的中文状态）
STATUS_TODO = "待拍"
STATUS_PENDING = "待验收"
STATUS_PASSED = "已通过"
STATUS_FIX_TODO = "待回拍"

# 整改单是谁开的
OPENER_STAFF = "staff"
OPENER_SUPER = "super"

# 仪容仪表（历史遗留的英文状态）
ATTIRE_STATUS_TODO = "todo"
ATTIRE_STATUS_PENDING = "pending"
ATTIRE_STATUS_PASSED = "passed"
ATTIRE_STATUS_REJECTED = "rejected"
