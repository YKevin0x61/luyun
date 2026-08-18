#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
表结构定义（每个表单独一份 CREATE TABLE 脚本）与索引定义。
"""

_TABLE_SCHEMAS = {
    "orders": """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_flow_id TEXT,
            table_number TEXT NOT NULL,
            dish_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            order_time TEXT NOT NULL,
            price REAL DEFAULT 0.0,
            total_amount REAL DEFAULT 0.0,
            status TEXT DEFAULT '未结',
            category TEXT DEFAULT '',
            station TEXT DEFAULT '',
            priority TEXT DEFAULT 'normal',
            notes TEXT,
            source TEXT DEFAULT '',
            dish_status TEXT DEFAULT '待出餐',
            ready_time TEXT,
            steamer_id TEXT,
            port_index INTEGER,
            stack_order INTEGER,
            loaded_at TEXT,
            is_hold INTEGER DEFAULT 0,
            is_rushed INTEGER DEFAULT 0,
            fired_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """,
    "tables": """
        CREATE TABLE IF NOT EXISTS tables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_number TEXT NOT NULL UNIQUE,
            amount REAL DEFAULT 0.0,
            people INTEGER DEFAULT 0,
            duration INTEGER DEFAULT 0,
            status TEXT DEFAULT 'empty',
            updated_at TEXT NOT NULL
        )
    """,
    "stations": """
        CREATE TABLE IF NOT EXISTS stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            station_id TEXT NOT NULL UNIQUE,
            name TEXT,
            color TEXT,
            config TEXT
        )
    """,
    "dish_stations": """
        CREATE TABLE IF NOT EXISTS dish_stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dish_name TEXT NOT NULL UNIQUE,
            station_id TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """,
    "semi_finished_rules": """
        CREATE TABLE IF NOT EXISTS semi_finished_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dish_name TEXT NOT NULL,
            semi_name TEXT NOT NULL,
            position TEXT NOT NULL DEFAULT '',
            factor REAL NOT NULL DEFAULT 1,
            unit TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """,
    "report_dishes": """
        CREATE TABLE IF NOT EXISTS report_dishes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dish_name TEXT NOT NULL UNIQUE,
            display_order INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT NOT NULL
        )
    """,
    "prep_items": """
        CREATE TABLE IF NOT EXISTS prep_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT NOT NULL,
            station TEXT NOT NULL DEFAULT '',
            position TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            unit TEXT NOT NULL DEFAULT '',
            shelf_life_hours REAL NOT NULL DEFAULT 24,
            lead_time_hours REAL NOT NULL DEFAULT 0,
            min_batch_qty REAL NOT NULL DEFAULT 0,
            safety_stock_ratio REAL NOT NULL DEFAULT 0.15,
            active INTEGER NOT NULL DEFAULT 1,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(item_name, unit)
        )
    """,
    "prep_batches": """
        CREATE TABLE IF NOT EXISTS prep_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prep_item_id INTEGER,
            item_name TEXT NOT NULL,
            produced_qty REAL NOT NULL,
            remaining_qty REAL NOT NULL,
            unit TEXT NOT NULL DEFAULT '',
            produced_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            operator TEXT NOT NULL DEFAULT '',
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """,
    "prep_stock_movements": """
        CREATE TABLE IF NOT EXISTS prep_stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER,
            prep_item_id INTEGER,
            item_name TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT '',
            movement_type TEXT NOT NULL,
            qty_delta REAL NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            operator TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """,
    "prep_plan_runs": """
        CREATE TABLE IF NOT EXISTS prep_plan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_date TEXT NOT NULL,
            target_start TEXT NOT NULL,
            target_end TEXT NOT NULL,
            method TEXT NOT NULL DEFAULT 'weighted_history',
            created_by TEXT NOT NULL DEFAULT '',
            item_count INTEGER NOT NULL DEFAULT 0,
            missing_rule_count INTEGER NOT NULL DEFAULT 0,
            high_risk_count INTEGER NOT NULL DEFAULT 0,
            expiry_risk_count INTEGER NOT NULL DEFAULT 0,
            waste_risk_count INTEGER NOT NULL DEFAULT 0,
            summary_json TEXT,
            created_at TEXT NOT NULL
        )
    """,
    "prep_plan_items": """
        CREATE TABLE IF NOT EXISTS prep_plan_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            prep_item_id INTEGER,
            item_name TEXT NOT NULL,
            station TEXT NOT NULL DEFAULT '',
            position TEXT NOT NULL DEFAULT '',
            unit TEXT NOT NULL DEFAULT '',
            forecast_qty REAL NOT NULL DEFAULT 0,
            safety_qty REAL NOT NULL DEFAULT 0,
            available_qty REAL NOT NULL DEFAULT 0,
            recommended_qty REAL NOT NULL DEFAULT 0,
            risk_level TEXT NOT NULL DEFAULT 'normal',
            confidence TEXT NOT NULL DEFAULT 'none',
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """,
    "prep_plan_item_slots": """
        CREATE TABLE IF NOT EXISTS prep_plan_item_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            plan_item_id INTEGER NOT NULL,
            prep_item_id INTEGER,
            item_name TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT '',
            slot_start TEXT NOT NULL,
            slot_end TEXT NOT NULL,
            forecast_qty REAL NOT NULL DEFAULT 0,
            available_qty REAL NOT NULL DEFAULT 0,
            recommended_qty REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """,
    "wecom_push_webhooks": """
        CREATE TABLE IF NOT EXISTS wecom_push_webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            webhook_url_encrypted TEXT NOT NULL,
            webhook_url_masked TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """,
    "wecom_push_jobs": """
        CREATE TABLE IF NOT EXISTS wecom_push_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            webhook_id INTEGER NOT NULL,
            push_type TEXT NOT NULL DEFAULT 'sales_report_text',
            schedule_time TEXT NOT NULL,
            date_range_mode TEXT NOT NULL DEFAULT 'today',
            station TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            last_sent_date TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """,
    "wecom_push_logs": """
        CREATE TABLE IF NOT EXISTS wecom_push_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            webhook_id INTEGER,
            webhook_name TEXT NOT NULL DEFAULT '',
            push_type TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            message_bytes INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            response_text TEXT NOT NULL DEFAULT '',
            sent_at TEXT NOT NULL
        )
    """,
    "app_settings": """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """,
    "auth": """
        CREATE TABLE IF NOT EXISTS admin_user (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_tokens (
            token_hash TEXT PRIMARY KEY,
            label TEXT DEFAULT '',
            expires_at TEXT,
            created_at TEXT NOT NULL,
            revoked_at TEXT
        );
    """,
}

_INDEX_DEFINITIONS = {
    "orders": [
        "CREATE INDEX IF NOT EXISTS idx_orders_station ON orders(station)",
        "CREATE INDEX IF NOT EXISTS idx_orders_table ON orders(table_number)",
        "CREATE INDEX IF NOT EXISTS idx_orders_order_time ON orders(order_time)",
        "CREATE INDEX IF NOT EXISTS idx_orders_dish_name ON orders(dish_name)",
        "CREATE INDEX IF NOT EXISTS idx_orders_business_flow_id ON orders(business_flow_id)",
        "CREATE INDEX IF NOT EXISTS idx_orders_source ON orders(source)",
        "CREATE INDEX IF NOT EXISTS idx_orders_time_station ON orders(order_time, station)",
        "CREATE INDEX IF NOT EXISTS idx_orders_station_time ON orders(station, order_time)",
    ],
    "report_dishes": [
        "CREATE INDEX IF NOT EXISTS idx_report_dishes_order ON report_dishes(display_order)",
    ],
    "semi_finished_rules": [
        "CREATE INDEX IF NOT EXISTS idx_semi_dish ON semi_finished_rules(dish_name)",
    ],
    "prep_items": [
        "CREATE INDEX IF NOT EXISTS idx_prep_items_active ON prep_items(active)",
        "CREATE INDEX IF NOT EXISTS idx_prep_items_name ON prep_items(item_name)",
        "CREATE INDEX IF NOT EXISTS idx_prep_items_station ON prep_items(station)",
    ],
    "prep_batches": [
        "CREATE INDEX IF NOT EXISTS idx_prep_batches_item_status ON prep_batches(item_name, status)",
        "CREATE INDEX IF NOT EXISTS idx_prep_batches_expires_at ON prep_batches(expires_at)",
        "CREATE INDEX IF NOT EXISTS idx_prep_batches_item_unit ON prep_batches(item_name, unit)",
    ],
    "prep_stock_movements": [
        "CREATE INDEX IF NOT EXISTS idx_prep_movements_batch ON prep_stock_movements(batch_id)",
        "CREATE INDEX IF NOT EXISTS idx_prep_movements_item_time ON prep_stock_movements(item_name, created_at)",
    ],
    "prep_plan_runs": [
        "CREATE INDEX IF NOT EXISTS idx_prep_plan_runs_date ON prep_plan_runs(plan_date)",
        "CREATE INDEX IF NOT EXISTS idx_prep_plan_runs_created_at ON prep_plan_runs(created_at)",
    ],
    "prep_plan_items": [
        "CREATE INDEX IF NOT EXISTS idx_prep_plan_items_run ON prep_plan_items(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_prep_plan_items_item ON prep_plan_items(item_name, unit)",
    ],
    "prep_plan_item_slots": [
        "CREATE INDEX IF NOT EXISTS idx_prep_plan_slots_run ON prep_plan_item_slots(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_prep_plan_slots_item_time ON prep_plan_item_slots(item_name, slot_start)",
    ],
    "wecom_push_webhooks": [
        "CREATE INDEX IF NOT EXISTS idx_wecom_webhooks_enabled ON wecom_push_webhooks(enabled)",
    ],
    "wecom_push_jobs": [
        "CREATE INDEX IF NOT EXISTS idx_wecom_jobs_enabled_time ON wecom_push_jobs(enabled, schedule_time)",
        "CREATE INDEX IF NOT EXISTS idx_wecom_jobs_webhook ON wecom_push_jobs(webhook_id)",
    ],
    "wecom_push_logs": [
        "CREATE INDEX IF NOT EXISTS idx_wecom_logs_sent_at ON wecom_push_logs(sent_at)",
        "CREATE INDEX IF NOT EXISTS idx_wecom_logs_job ON wecom_push_logs(job_id)",
    ],
}

# 所有表名（按数据量从小到大排列）
ALL_TABLES = list(_TABLE_SCHEMAS.keys())
