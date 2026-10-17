#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""表清单常量。

SQLite DDL 已随 ADR 0089 退场：结构定义现在只有一处——PostgreSQL 的
``migrations/pg/*.sql``（``0001_initial_schema.sql`` 是 bootstrap-only 且被 Admin
迁移面板永久排除，之后一律走 ``000N`` 加成性增量）。启动期不再建表、不再补列。

这里保留的只是「有哪些表」：admin 数据浏览、备份口径与契约测试要按类别遍历。
"""

# 所有业务表名（admin CRUD 与备份口径用；auth 三张物理表也在其中）
ALL_TABLES = [
    "orders",
    "tables",
    "stations",
    "dish_stations",
    "semi_finished_rules",
    "report_dishes",
    "prep_items",
    "prep_closed_days",
    "prep_plan_runs",
    "prep_plan_items",
    "prep_plan_item_slots",
    "wecom_push_webhooks",
    "wecom_push_jobs",
    "wecom_push_logs",
    "app_settings",
    "auth",
]

# Recipe tables live in the same database but stay out of ALL_TABLES: generic Admin
# writes and business-table overwrite/merge must not treat them as business tables.
# The Admin data browser lists them read-only through its separate catalog.
RECIPE_TABLES = ("sop_stations", "sop_recipes", "sop_recipes_history")

# Hygiene tables: the identity rows (hygiene_employees / hygiene_staff_sessions) are
# written by the public-layer services/identity/accounts.py; the rest by
# services/hygiene/accounts.py (HygieneEmployeeAccounts) / HygieneWork, which are the
# only write paths. The Admin data browser lists them read-only through its separate
# catalog.
HYGIENE_TABLES = (
    "hygiene_employees",
    "hygiene_staff_sessions",
    "hygiene_shift_picks",
    "hygiene_zones",
    "hygiene_daily_items",
    "hygiene_standards",
    "hygiene_daily_instances",
    "hygiene_daily_submissions",
    "hygiene_settings",
    "hygiene_overdue_notices",
    "hygiene_board_events",
    "hygiene_deep_clean_items",
    "hygiene_deep_clean_instances",
    "hygiene_deep_clean_submissions",
    "hygiene_deep_clean_overdue_notices",
    "hygiene_fix_tickets",
    "hygiene_fix_reshoots",
    "hygiene_fix_overdue_notices",
    "hygiene_teaching_examples",
    "hygiene_capture_variants",
    "hygiene_wecom_shares",
)

# Scheduling tables: `staff_shifts` / `staff_assignments` are the shared vocabulary
# (what shifts exist; who works which day) that the public layer and every downstream
# reader agree on, `scheduling_rules` and `scheduling_overrides` are the scheduling
# system's own two inputs (where the rotation says someone works; the single days the
# manager changed by hand), `scheduling_requests` is what employees ask for (leave
# requests waiting on the manager; approving one writes `scheduling_overrides` rows),
# and `scheduling_zone_defaults` is "who × which shift →
# which zone" (the zone names themselves stay in `hygiene_zones`, read through the
# public layer). All six are written only through `services/scheduling/store.py`;
# the Admin data browser lists them read-only through its separate catalog, and the
# generic business-table write paths must not touch them.
SCHEDULING_TABLES = (
    "staff_shifts",
    "staff_assignments",
    "scheduling_rules",
    "scheduling_overrides",
    "scheduling_requests",
    "scheduling_zone_defaults",
)

AUTH_PHYSICAL_TABLES = ("admin_user", "sessions", "api_tokens")

# 企微推送的新模型表（迁移 0016）：订阅 / 渠道群组 / 群组成员 / 出站记录；
# 外加迁移 0019 的配置变更历史 `wecom_push_audit`（只追加、无通用写入口）。
# 与 SCHEDULING_TABLES 同一个理由不进 ALL_TABLES —— 它们由订阅 / 出站 / 审计的 repo
# 方法（db_core/wecom_subscriptions_repo.py、db_core/wecom_audit_repo.py）写入，
# 不该出现在 Admin 的通用业务表写入口。
# 但连接建立时**必须**给它们绑 TableView：``db.table(name)`` 只认已绑定的表，漏了
# 就是运行期「未知表」。
WECOM_SUBSCRIPTION_TABLES = (
    "wecom_push_subscriptions",
    "wecom_channel_groups",
    "wecom_channel_group_members",
    "wecom_push_outbox",
    "wecom_push_audit",
)

# 加班与补钟台账（迁移 0021 起，`.scratch/overtime-and-reminders/`）：员工申报的
# 一笔加班 / 补钟；每个月的计费底薪快照（迁移 0022，票 03）；以及工龄奖的变更留痕
# （迁移 0023，票 05）—— 最后一张是钱的台账，刻意不写进会被保留期清掉的 `logs` 表。
# 与 SCHEDULING_TABLES 同一个理由不进 ALL_TABLES —— 它们由
# `services/overtime/ledger.py`（OvertimeLedger）与 `services/identity/accounts.py`
# （工龄奖那一栏）写入，不该出现在 Admin 的通用业务表写入口；数据浏览器里只读列出
# （分组「人事」）。
OVERTIME_TABLES = (
    "overtime_entries",
    "overtime_salary_snapshots",
    "seniority_bonus_changes",
)

# 考勤面的表（迁移 0026 起，`.scratch/考勤表导出/`）：
#
# * **法定节假日日历** `statutory_holidays` —— 超级管理员逐日标记「这天是法定节假日」
#   （日期唯一 + 名称），它是「法定节假日补休」（ADR 0113）的唯一依据，代码里没有写死的
#   年度表。写入只有 `services/holiday_calendar.py` 一个口（管理端「节假日日历」页）。
# * **月度考勤台账** `attendance_ledger`（迁移 0027，票 03）—— 员工 × 自然月一行：假期
#   期初余额、封账后的本月结余（存假）与四个手填列。封账写它的是票 04、手填列写它的是
#   票 05；票 03 只**读**上一月已封账的结余来算「上月存假」，当月只算不落库。
#
# 与 SCHEDULING_TABLES 同一个理由不进 ALL_TABLES —— 它们不该出现在 Admin 的通用业务表
# 写入口；数据浏览器里只读列出（分组「考勤」）。
ATTENDANCE_TABLES = ("statutory_holidays", "attendance_ledger")

# 日常损耗登记（迁移 0029，从 worktool2 那个小程序搬过来）：登记台账、现场照片的
# 元数据、每人的常用模板。与 SCHEDULING_TABLES 同一个理由不进 ALL_TABLES —— 三张表
# 只有 `services/wastage/store.py`（WastageStore）一个写入口，不该出现在 Admin 的
# 通用业务表写入口；数据浏览器里只读列出（分组「损耗」）。
WASTAGE_TABLES = ("wastage_records", "wastage_images", "wastage_templates")

# Admin DataTable exposes these tables read-only; their owning feature pages
# remain the only supported write paths.
ADMIN_READ_ONLY_TABLES = (
    "dish_stations",
    *AUTH_PHYSICAL_TABLES,
    *RECIPE_TABLES,
    *HYGIENE_TABLES,
    *SCHEDULING_TABLES,
    *WECOM_SUBSCRIPTION_TABLES,
    *OVERTIME_TABLES,
    *ATTENDANCE_TABLES,
    *WASTAGE_TABLES,
)
