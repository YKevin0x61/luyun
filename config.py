#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
餐厅订单数据采集系统后端配置文件
"""

import os
from typing import Dict, List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """应用配置类"""

    # 基础配置
    APP_NAME: str = "LuyunOrder"
    APP_VERSION: str = "0.8.1"
    # 安全默认：不开 /docs、cookie 带 Secure。开发机在 .env 里显式写 DEBUG=true。
    DEBUG: bool = False
    # 关掉 lifespan 里的常驻后台循环（爬虫轮询、卫生调度、企微推送、数据质量调度）。
    # 给测试用：这些循环会长期占用与业务库同一条 PostgreSQL 连接，共享测试库上
    # 会互相干扰（TRUNCATE 撞锁、跨用例状态污染）。生产不要打开。
    DISABLE_BACKGROUND_TASKS: bool = False
    
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
    # 数据库配置 — PostgreSQL 是唯一后端（SQLite 已在 ADR 0089 退场）
    DATABASE_DIR: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    # 遗留（SQLite 时代）配置面：不进任何运行期数据访问路径，业务数据只在
    # PostgreSQL 里，data/app.db 仅作为老门店的迁移输入/只读回滚源存在。保留这两个
    # 名字是因为 DATABASE_PATHS 会展开 APP_DB_PATH，而 db_core/stats.py 把它当
    # db_paths 回显出去（纯诊断信息）。RecipeStore 当年那个「默认 db_path 参数」
    # 已在 2026-09-23 删掉——它的自建连接分支早已不可达，误用现在在构造期就报错。
    # 同名但独立的一份常量在 services/backup_import_staging.py（上传包里的旧 app.db
    # 成员，只用于识别并明确拒绝，见 api/backup.py）。不要删。
    APP_DB_FILENAME: str = "app.db"
    # 只接受 postgres。这一项保留是为了让老部署在启动时拿到明确指引，而不是静默
    # 回落：值不是 postgres 时 DatabaseManager.connect() 直接失败（见 ADR 0089）。
    DATABASE_BACKEND: str = "postgres"
    POSTGRES_DSN: str = "postgresql://localhost:5432/luyun"
    # 连接期超时（PG 会话级 GUC，单位毫秒）：**默认值即安全默认**。全进程只有一条
    # PgConnection、写事务全程持全局串行锁，没有超时的话一条挂起的写事务（长事务 /
    # 被锁的 DDL / 半死连接）会把 API、/api/healthz 与全部后台循环一起排住且无法
    # 打断，单 worker 架构下无法水平规避（PERF-01）。
    # 环境变量 LUYUN_PG_STATEMENT_TIMEOUT_MS / LUYUN_PG_LOCK_TIMEOUT_MS 优先于这两项
    # （写法同 POSTGRES_DSN），0 = 关闭该项、回到 PG 的无限等待。
    # 30s 的余量依据：21 万行 orders（生产 204,297 行）上最重的合法查询实测约 0.6s
    # （180 天区间报表，db_core/reports.py::aggregate_table_operations）。
    PG_STATEMENT_TIMEOUT_MS: int = 30000
    PG_LOCK_TIMEOUT_MS: int = 5000
    # 事务开着但连接空闲（持锁方卡在非 PG 的 await 上）时，由服务端掐掉这条会话。
    # statement_timeout / lock_timeout 在那种姿态下都不计时——它们是「语句在执行」
    # 才计时——只有这项能兜住，否则串行写锁被永久占住（PERF-08）。
    # env LUYUN_PG_IDLE_IN_TRANSACTION_SESSION_TIMEOUT_MS 优先，0 = 关闭该项。
    PG_IDLE_IN_TRANSACTION_TIMEOUT_MS: int = 60000
    # 应用侧「等串行写锁」的**排队**上限（毫秒）：不是 PG 的 GUC，而是
    # _TaskGuard 等待那条单连接的串行锁的上限。statement_timeout / lock_timeout
    # 管不到排队阶段（PERF-08 实测持写事务时读请求等 6.504s 无返回、且无报错），
    # 所以这一项必须由应用自己兜：超时抛 DatabaseBusy → HTTP 503 + 可重试。
    # 默认与 lock_timeout 同量级（5s）；env LUYUN_PG_WRITE_LOCK_TIMEOUT_MS 优先，
    # 0 = 无限等待（逃生门，不推荐）。
    PG_WRITE_LOCK_TIMEOUT_MS: int = 5000
    # 冷备输出根目录（宿主机定时任务的归档落点，仓库根下的 backups/）；
    # BACKUP_DIR 环境变量优先
    COLD_BACKUP_DIR: str = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "backups"
    )

    @property
    def APP_DB_PATH(self) -> str:
        """遗留单库路径（SQLite 时代的 data/app.db）：纯拼路径，不保证文件存在。

        现在唯一的读者是 :attr:`DATABASE_PATHS`（它被连接层取来给 ``db_core/stats.py``
        回显诊断信息）；冷备与运行期都不再读它，见上面 ``APP_DB_FILENAME`` 的说明。
        """
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
            "auth": app_db_path,
        }

    # realtime nudge 的跨进程广播总线（Redis pub/sub），**部署必需组件**（ADR 0090）。
    # 留空 = 部署不完整：启动期 `_require_startup_config()` 会当场 RuntimeError 并打印
    # 安装/配置指引，与 `DATABASE_BACKEND` 不是 postgres 时同款硬切——不存在"留空也能
    # 跑"的形态。**连不上**是另一回事：订阅任务退避重连，本地派发不经过总线，门店
    # 照常营业（见 services/realtime/redis_bus.py）。
    # `DISABLE_BACKGROUND_TASKS=true`（测试、一次性脚本）没有常驻循环，跳过这项要求。
    # 环境变量 `LUYUN_REDIS_URL` 优先于本项，写法同 POSTGRES_DSN。
    REDIS_URL: str = ""

    # 日志存储配置
    LOG_RETENTION_DAYS: int = 7  # 日志保留天数（0 = 永久保留）
    LOG_QUEUE_BATCH_SIZE: int = 200  # 异步写库批量大小
    LOG_QUEUE_FLUSH_INTERVAL: float = 1.0  # 异步写库刷新间隔（秒）
    # 运行期维护间隔：按保留天数清理过期日志。启动期清理只发生一次，长期不重启
    # 的实例必须靠这个循环把保留天数落到实处（空间回收交给 PG 的 autovacuum）。
    LOG_MAINTENANCE_INTERVAL_SECONDS: int = 6 * 3600

    # 统一出站（ADR 0095）：**每渠道**独立的发信节流与发送记录保留天数。
    # 阈值刻意做成配置项、不硬编官方数字——企微群机器人的每分钟上限没有从官方文档
    # 正文确认过（文档页是脚本渲染的，抓不到正文），社区与云厂商接入文档普遍转述为
    # 每分钟 20 条。0 = 关掉节流（阈值）/ 永久保留（保留天数）。
    WECOM_OUTBOX_RATE_LIMIT_PER_MINUTE: int = 20
    WECOM_OUTBOX_RETENTION_DAYS: int = 90
    # 「发送中」兜底：一行进入 sending 后超过这个秒数还没写终态，就当作发送它的进程
    # 已经退出（崩溃 / systemd 重启 / 更新作业重启应用），由调度循环捞回来——未达上限
    # 的回到待发，用尽的记失败。默认 300 秒，明显大于单次发送超时（10 秒）与节流窗口
    # （60 秒）：正常在发的行永远够不着这个阈值。0 = 关掉兜底（发送中的行永远不动）。
    WECOM_OUTBOX_SENDING_TIMEOUT_SECONDS: int = 300

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

    # 餐厅爬虫配置（敏感字段已迁移到 services/credentials_store.py，由 /settings 页面维护）
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
    # 爬虫主循环连续失败达到该次数后才开始告警（首次故障的门槛，避免偶发抖动）
    SCRAPER_ALERT_FAILURE_THRESHOLD: int = 3
    # 同一场故障里两条健康告警之间的最小间隔（秒）：持续挂死时**每小时最多一条**，
    # 而不是每累计满一个阈值就发一条（失败间隔约 60s 时那样会是每 3 分钟一条）。
    SCRAPER_ALERT_MIN_INTERVAL_SECONDS: int = 3600
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

    # `SettingsConfigDict` 与 `BaseSettings` 的默认 `model_config`（`extra="forbid"`
    # 等）**合并**，所以这里只写需要覆盖的两项，行为与旧的 class-based `Config` 一致。
    model_config = SettingsConfigDict(
        # 绝对路径：`.env` 在仓库根，而 uvicorn 之外的入口（scripts/start.py 等）
        # 的工作目录常常不是仓库根，相对路径会**静默读不到**配置——本机就因此出现
        # 「.env 写着 postgres，实例却连了另一个库」的错配。
        env_file=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        case_sensitive=True,
    )

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
