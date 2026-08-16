#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
餐厅订单数据采集系统后端配置文件
"""

import os
from typing import Dict, List, Optional
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """应用配置类"""

    # 基础配置
    APP_NAME: str = "LuyunOrder"
    APP_VERSION: str = "0.5.8"
    DEBUG: bool = True
    
    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 1
    ADMIN_API_KEY: Optional[str] = None
    ALLOW_UNAUTH_SETUP_FROM_LOCALHOST: bool = False
    SESSION_COOKIE_NAME: str = "luyun_session"
    SESSION_TTL_HOURS: int = 8
    SESSION_REMEMBER_DAYS: int = 30
    AUTH_MIN_PASSWORD_LENGTH: int = 8
    AUTH_MAX_PASSWORD_BYTES: int = 1024
    # 数据库配置 — 单库 app.db（WAL），仅 logs 因写入量大保持独立文件
    DATABASE_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    APP_DB_FILENAME: str = "app.db"

    @property
    def APP_DB_PATH(self) -> str:
        """单库路径：17 张业务表 + auth 统一存放于此（WAL 模式）。"""
        return os.path.join(self.DATABASE_DIR, self.APP_DB_FILENAME)

    @property
    def DATABASE_PATHS(self) -> Dict[str, str]:
        app_db_path = self.APP_DB_PATH
        return {
            "orders": app_db_path,
            "tables": app_db_path,
            "stations": app_db_path,
            "dish_stations": app_db_path,
            "semi_finished_rules": app_db_path,
            "report_dishes": app_db_path,
            # 备货计划相关表
            "prep_items": app_db_path,
            "prep_batches": app_db_path,
            "prep_stock_movements": app_db_path,
            "prep_plan_runs": app_db_path,
            "prep_plan_items": app_db_path,
            "prep_plan_item_slots": app_db_path,
            "wecom_push_webhooks": app_db_path,
            "wecom_push_jobs": app_db_path,
            "wecom_push_logs": app_db_path,
            # 应用运行配置（营业时段 / 轮询间隔等）
            "app_settings": app_db_path,
            # 日志写入量大，独立文件，不并入 app.db
            "logs": os.path.join(self.DATABASE_DIR, "logs.db"),
            "auth": app_db_path,
        }

    # 日志存储配置
    LOG_RETENTION_DAYS: int = 7  # 日志保留天数（0 = 永久保留）
    LOG_QUEUE_BATCH_SIZE: int = 200  # 异步写库批量大小
    LOG_QUEUE_FLUSH_INTERVAL: float = 1.0  # 异步写库刷新间隔（秒）

    # 兼容旧属性
    @property
    def DATABASE_PATH(self) -> str:
        """返回 orders 表路径（向后兼容）"""
        return self.DATABASE_PATHS["orders"]

    # 餐厅爬虫配置（敏感字段已迁移到 services/credentials_store.py，由 /setup 页面维护）
    # 营业时段 / 轮询间隔 / headless / 重试等运行期配置改由 app_settings 表持久化，
    # 见 services/runtime_settings.py，可在「配置 → 运行配置」页面在线修改并热生效。
    RESTAURANT_BASE_URL: str = "https://restaurant.sealosgzg.site"

    # 已结账单 / 对账 API
    SETTLED_BILL_API_TIMEOUT_MS: int = 30000
    SETTLED_BILL_API_MAX_RETRIES: int = 3
    SETTLED_BILL_API_RETRY_BACKOFF_S: float = 1.5
    RECONCILE_MISS_RATE_ALERT_PCT: float = 0.5
    RECONCILE_MISS_QTY_ALERT: float = 10.0
    UNMAPPED_DISH_ALERT_ENABLED: bool = True
    UNMAPPED_ALERT_INTERVAL_HOURS: int = 2
    # 爬虫主循环连续失败达到该次数（或其整数倍）时触发一次企微健康告警，避免静默挂死
    SCRAPER_ALERT_FAILURE_THRESHOLD: int = 3
    RECONCILE_SCHEDULE_ENABLED: bool = False
    RECONCILE_SCHEDULE_TIME: str = "22:05"
    RECONCILE_AUTO_FIX: bool = True
    RECONCILE_AUTO_NOTIFY: bool = True

    # GitHub Release Version Check / Update Job (ADR 0011)
    # Repo is fixed and public; Releases PAT is optional (anonymous download
    # works; token only raises API rate limits). May be set via env bootstrap
    # or Admin「系统更新」→ data/github_release.enc.
    GITHUB_REPO: str = "YKevin0x61/luyun"
    GITHUB_RELEASES_TOKEN: Optional[str] = None
    # Empty → project root (directory containing main.py).
    RELEASE_UPDATE_REPO_DIR: str = ""

    # 档口配置
    KITCHEN_STATIONS: Dict[str, Dict] = {
        "xibing": {
            "id": "xibing",
            "name": "西饼档",
            "color": "#FF6B6B"
        },
        "changfen": {
            "id": "changfen", 
            "name": "肠粉档",
            "color": "#4ECDC4"
        },
        "shulong": {
            "id": "shulong",
            "name": "熟笼档",
            "color": "#45B7D1",
            "steamer_layout": {
                "steamers": [
                    {"id": "1", "port_count": 6},
                    {"id": "2", "port_count": 6},
                ],
                "port_capacity": 10,
                "awaiting_cancel_notice_seconds": 180,
            },
        },
        "mingdang1": {
            "id": "mingdang1",
            "name": "明档1",
            "color": "#96CEB4"
        },
        "mingdang2": {
            "id": "mingdang2",
            "name": "明档2",
            "color": "#FECA57"
        },
        "jianzha": {
            "id": "jianzha",
            "name": "煎炸档",
            "color": "#FF9FF3"
        },
        "loumian": {
            "id": "loumian",
            "name": "楼面",
            "color": "#A78BFA"
        }
    }

    # 优先级配置
    PRIORITY_LEVELS: Dict[str, Dict] = {
        "urgent": {
            "value": "urgent",
            "label": "紧急",
            "color": "#F5222D",
            "threshold": 20 * 60 * 1000  # 20分钟
        },
        "high": {
            "value": "high",
            "label": "高",
            "color": "#FAAD14",
            "threshold": 15 * 60 * 1000  # 15分钟
        },
        "normal": {
            "value": "normal",
            "label": "普通",
            "color": "#13C2C2",
            "threshold": 0
        }
    }
    
    class Config:
        env_file = ".env"
        case_sensitive = True

# 创建全局配置实例
settings = Settings()

# 导出配置常量
KITCHEN_STATIONS = settings.KITCHEN_STATIONS
PRIORITY_LEVELS = settings.PRIORITY_LEVELS

# 订单行营业额 SQL 表达式（全系统统一口径，见 docs/DATA_REVENUE.md）
ORDER_LINE_REVENUE_SQL = (
    "CASE WHEN total_amount IS NOT NULL AND total_amount != 0 "
    "THEN total_amount ELSE quantity * COALESCE(price, 0) END"
) 