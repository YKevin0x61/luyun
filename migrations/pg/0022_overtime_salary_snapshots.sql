-- 0022：加班费的计费底薪快照（票 03）
--
-- 背景：加班费 =（底薪 ÷ 该月日历天数 ÷ 8.5）× 该月净时长。底薪在员工档案里是**会变的**
-- （花名册改版补录、调薪），而「上个月的钱」不该因为今天调了薪就跟着变 —— 所以每个月每人
-- 记一版**计费底薪**：该月**第一次审批通过**那一刻的档案值，之后该月所有审批都用它。
--
-- 粒度是「每月每人一个」（2026-10-07 用户明确选的，不是每条登记一个、也不是每次审批一个）。
-- 归月看的是**登记日期**（`overtime_entries.entry_date` 的自然月），不是审批时间 ——
-- 所以 9 月批的 8 月单子，写的是 `month = '2026-08'` 那一行。
--
-- 写入口只有一处：`OvertimeLedger.approve` 在批准一笔待审批登记时
-- `INSERT ... ON CONFLICT DO NOTHING`，与那次状态变更同一个事务（见 `docs/adr/0100`）。
-- 底薪待补的人批不了，所以这张表里不会出现 `base_salary = 0` 这种「其实没填」的行。
--
-- 幂等：IF NOT EXISTS，可重复执行。时间戳 TEXT（ISO 字符串，带 +08:00），沿用仓库口径。

CREATE TABLE IF NOT EXISTS overtime_salary_snapshots (
    tenant_id BIGINT NOT NULL DEFAULT 1 REFERENCES tenants(id),
    "employee_id" BIGINT NOT NULL,
    "month" TEXT NOT NULL,
    "base_salary" BIGINT NOT NULL,
    "created_at" TEXT NOT NULL,
    PRIMARY KEY (tenant_id, "employee_id", "month")
);
