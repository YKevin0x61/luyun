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
    APP_VERSION: str = "0.6.10"
    # 安全默认：不开 /docs、cookie 带 Secure。开发机在 .env 里显式写 DEBUG=true。
    DEBUG: bool = False
    
    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 1
    ADMIN_API_KEY: Optional[str] = None
    ALLOW_UNAUTH_SETUP_FROM_LOCALHOST: bool = False
    SESSION_COOKIE_NAME: str = "luyun_session"
    STAFF_SESSION_COOKIE_NAME: str = "luyun_staff_session"
    SESSION_TTL_HOURS: int = 8
    SESSION_REMEMBER_DAYS: int = 30
    # 卫生端员工的闲置上限：last_seen_at 超过这个时长就作废会话（0 = 不启用）。
    # 勾了「记住密码」的会话有效期 30 天，这条把「手机一直挂着没人用」的窗口收窄；
    # 在用的会话每个请求都会刷新 last_seen_at，正常排班碰不到这条线。
    SESSION_IDLE_HOURS: int = 336
    # 会话 cookie 是否带 Secure。留空 = 跟随 DEBUG（开发机跑明文 http 时不带，
    # 否则浏览器不回传 cookie、登录表现为"登不进"）。内网明文 http 部署要显式
    # 写 SESSION_COOKIE_SECURE=false，别靠改 DEBUG 来达成。
    SESSION_COOKIE_SECURE: Optional[bool] = None
    AUTH_MIN_PASSWORD_LENGTH: int = 8
    AUTH_MAX_PASSWORD_BYTES: int = 1024
    # 数据库配置 — 单库 app.db（WAL），仅 logs 因写入量大保持独立文件
    DATABASE_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    APP_DB_FILENAME: str = "app.db"
    # 数据库后端：sqlite（默认，单店部署形态）| postgres（多店，见 ADR 0084）。
    # 切到 postgres 时表的建立由 migrations/pg/ 负责，不在启动期建表。
    DATABASE_BACKEND: str = "sqlite"
    POSTGRES_DSN: str = "postgresql://localhost:5432/luyun"
    # 冷备输出根目录（宿主机定时任务的归档落点，仓库根下的 backups/）；
    # BACKUP_DIR 环境变量优先
    COLD_BACKUP_DIR: str = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "backups"
    )

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
    # 运行期维护间隔：清理过期日志 + WAL checkpoint。启动期清理只发生一次，
    # 长期不重启的实例必须靠这个循环把 logs.db 的大小控制住。
    LOG_MAINTENANCE_INTERVAL_SECONDS: int = 6 * 3600
    # 损坏日志库（quarantine 副本）保留份数上限，0 = 不限制。
    # 每份是几百 MB 的快照，无上限保留会反过来加剧磁盘满。
    LOG_CORRUPT_KEEP: int = 2
    # 启动期 SQLite quick_check：logs.db 不通过则隔离重建，app.db 不通过只告警
    # （业务库是订单/结算数据，绝不自动搬走）。
    SQLITE_QUICK_CHECK_ON_START: bool = True

    # 磁盘守护（进程内）：阈值告警 + 健康端点暴露
    DISK_GUARD_ENABLED: bool = True
    DISK_GUARD_INTERVAL_SECONDS: int = 300
    DISK_WARN_PCT: int = 85
    DISK_CRITICAL_PCT: int = 92
    # 应用更新前要求的最小可用空间（MB）。低于它直接拒绝更新，避免在满盘时
    # 执行 pip sync 把 .venv 写坏（现场复合故障的成因之一）。
    UPDATE_MIN_FREE_MB: int = 2048

    # 爬虫浏览器自愈：launch 报 "Executable doesn't exist" 时自动执行
    # `python -m playwright install chromium` 并重试一次。
    SCRAPER_BROWSER_AUTO_INSTALL: bool = True
    PLAYWRIGHT_INSTALL_TIMEOUT_SECONDS: int = 900

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
    
    @property
    def session_cookie_secure(self) -> bool:
        """会话 cookie 的 Secure 属性：显式配置优先，否则跟随 DEBUG。

        与 DEBUG 解耦是为了不让"内网明文 http 部署"逼着人把 DEBUG 打开——
        DEBUG 还管着 /docs 与错误详情，两件事不该绑在一起。
        """
        if self.SESSION_COOKIE_SECURE is not None:
            return bool(self.SESSION_COOKIE_SECURE)
        return not self.DEBUG

    class Config:
        env_file = ".env"
        case_sensitive = True

# 创建全局配置实例
settings = Settings()

# 导出配置常量
KITCHEN_STATIONS = settings.KITCHEN_STATIONS
PRIORITY_LEVELS = settings.PRIORITY_LEVELS

# 订单行营业额 SQL 表达式（全系统统一口径，见 docs/pos/DATA_REVENUE.md）
ORDER_LINE_REVENUE_SQL = (
    "CASE WHEN total_amount IS NOT NULL AND total_amount != 0 "
    "THEN total_amount ELSE quantity * COALESCE(price, 0) END"
) 
