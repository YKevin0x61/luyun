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
    "prep_batches",
    "prep_stock_movements",
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
)

# Scheduling tables: `staff_shifts` / `staff_assignments` are the shared vocabulary
# (what shifts exist; who works which day) that the public layer and every downstream
# reader agree on, `scheduling_rules` and `scheduling_overrides` are the scheduling
# system's own two inputs (where the rotation says someone works; the single days the
# manager changed by hand), and `scheduling_zone_defaults` is "who × which shift →
# which zone" (the zone names themselves stay in `hygiene_zones`, read through the
# public layer). All five are written only through `services/scheduling/store.py`;
# the Admin data browser lists them read-only through its separate catalog, and the
# generic business-table write paths must not touch them.
SCHEDULING_TABLES = (
    "staff_shifts",
    "staff_assignments",
    "scheduling_rules",
    "scheduling_overrides",
    "scheduling_zone_defaults",
)

AUTH_PHYSICAL_TABLES = ("admin_user", "sessions", "api_tokens")

# Admin DataTable exposes these tables read-only; their owning feature pages
# remain the only supported write paths.
ADMIN_READ_ONLY_TABLES = (
    "dish_stations",
    *AUTH_PHYSICAL_TABLES,
    *RECIPE_TABLES,
    *HYGIENE_TABLES,
    *SCHEDULING_TABLES,
)
