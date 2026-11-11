#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""加班与补钟、考勤与存假两个域共用的**字面量契约**（`docs/adr/0139`）。

本文件**零 import**：它只放两个域都要念同一份的值 —— 加班状态机的五个状态、一个工作日
的半小时数、一次最多抵几天。`services/overtime/ledger.py` 从这里 import 并继续把名字
导出（`__all__` 与模块级名字都不变），`services/attendance/store.py` 也从这里取状态，
两个域因此不再为了两个字符串互相 import。

值本身是数据面契约（库里的 `status` 列、`days × 17` 的折算），改之前先看 ADR 0100 / 0124。
"""

# 状态机（`docs/adr/0100`）：本票只用得到 `pending` 与 `cancelled`，另外三个一次到位 ——
# 状态取值是数据面契约，票 02 的审批不该再改一次列的定义。
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"
STATUS_VOIDED = "voided"

# 加班费的分母：一个工作日按 8.5 小时算（用户给的口径，见 `docs/adr/0100` 与票 03）。
# 它与 `half_hours` 的单位无关 —— 金额那一步才把「半小时数」折成小时。
HOURS_PER_DAY = 8.5

# 一个工作日的半小时数（8.5 小时 = 17 个半小时）：抵扣只能整天，`days × 17` 就是它
# 从补钟账上注销的半小时数。与 `HOURS_PER_DAY` 同源，改一个必须改另一个。
HALF_HOURS_PER_LEAVE_DAY = 17

# 一次最多抵几天：一个月最多 31 天，再大就是手滑（防的不是业务，是「抵 100 天」）。
MAX_DEDUCTION_DAYS = 31
