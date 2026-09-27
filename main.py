#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
餐厅订单数据采集与查询系统
"""

import asyncio
import logging
import uvicorn
from contextlib import asynccontextmanager
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from urllib.parse import quote
from fastapi.exceptions import RequestValidationError

from config import settings
from database import DatabaseManager
from db_core.backend.pg import DatabaseUnavailable
from db_core.business_day import business_day_window
from api import orders, dishes, dish_stations, semi_rules, report_dishes, prep_plan, wecom_push
from api.admin import router as admin_router
from api.recipes import public_router as recipes_public_router
from api.recipes import router as recipes_router
from api.credentials import router as credentials_router
from api.db_credentials import router as db_credentials_router
from api.backup import router as backup_router
from api.release_update import router as release_update_router
from api.db_migrations import router as db_migrations_router
from api.runtime_settings import router as runtime_settings_router
from api.logs import router as logs_router
from api.scheduling import router as scheduling_router
from api.tables import router as tables_router
from api.analytics import router as analytics_router
from api.export_api import router as export_router
from api.security import (
    csrf_origin_rejected,
    identify_ws,
    verify_admin_token,
    warn_if_admin_open,
)
from api.auth import router as auth_router
from api.hygiene import router as hygiene_router
from services import auth_service
from services import backup_import_staging
from services import backup_points, backup_retention, backup_service
from scraper.restaurant_scraper import create_restaurant_scraper
from services import credentials_store
from services.dish_catalog import DishCatalog
from services.app_runtime import AppRuntime, set_runtime
from services.memory_manager import memory_manager
from services.log_storage import log_storage, LogStorageHandler
from services.disk_guard import disk_guard, min_free_mb
from services.wecom_push_service import wecom_push_service
from services.scraper_failure_tracker import ScraperFailureTracker
from services.data_quality_scheduler import run_reconcile_scheduler, run_unmapped_dish_watchdog
from services.realtime.hub import realtime_hub
from services.realtime.redis_bus import require_redis_url
from services.realtime.logs_bridge import LogsNudgeScheduler

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class InMemoryLogHandler(logging.Handler):
    """把近期日志保存在内存中，供前端实时查看。"""

    def __init__(self, capacity: int = 1200, nudge_scheduler: Optional[LogsNudgeScheduler] = None):
        super().__init__()
        self.capacity = capacity
        self._records = deque(maxlen=capacity)
        self._next_id = 1
        self._nudge_scheduler = nudge_scheduler

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            timestamp = datetime.fromtimestamp(
                record.created,
                tz=timezone(timedelta(hours=8))
            ).isoformat()
            self._records.append({
                "id": self._next_id,
                "timestamp": timestamp,
                "level": record.levelname,
                "logger": record.name,
                "message": message,
            })
            self._next_id += 1
            if self._nudge_scheduler is not None:
                self._nudge_scheduler.notify()
        except Exception:
            self.handleError(record)

    def recent(self, limit: int = 200) -> list[dict]:
        if limit <= 0:
            return []
        if limit >= len(self._records):
            return list(self._records)
        return list(self._records)[-limit:]

    def after(self, after_id: int, limit: int = 200) -> list[dict]:
        matched_records = [record for record in self._records if record["id"] > after_id]
        if limit <= 0 or len(matched_records) <= limit:
            return matched_records
        return matched_records[:limit]

    @property
    def latest_id(self) -> int:
        if not self._records:
            return 0
        return self._records[-1]["id"]


logs_nudge_scheduler = LogsNudgeScheduler(realtime_hub)
in_memory_log_handler = InMemoryLogHandler(nudge_scheduler=logs_nudge_scheduler)
in_memory_log_handler.setLevel(logging.INFO)
in_memory_log_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logging.getLogger().addHandler(in_memory_log_handler)

# 持久化 handler：把日志投递到 LogStorage 队列，由后台协程批量写入 PostgreSQL。
# 注意：handler 实例在 logging 模块层面注册，等到 lifespan 内 log_storage.start() 后
# 第一条入队的日志才会被消费（队列在线程间安全共享）。
log_storage_handler = LogStorageHandler(log_storage, level=logging.INFO)
logging.getLogger().addHandler(log_storage_handler)

# 定义北京时区
CHINA_TZ = timezone(timedelta(hours=8))

# 全局变量（db / dish_catalog / scraper 为 AppRuntime 薄别名，见 lifespan）
db_manager = None
dish_catalog = None
restaurant_scraper = None
scraper_task = None
wecom_push_task = None
hygiene_overdue_task = None
hygiene_variant_task = None
hygiene_maintenance_task = None
reconcile_scheduler_task = None
unmapped_watchdog_task = None
recipe_store = None
employee_accounts = None
hygiene_work = None
hygiene_attire = None
# 管理端数据与照片视图（ADR-0087）：只读，与 hygiene_work 一起在 lifespan 里建。
hygiene_archive = None

# ---------------------------------------------------------------------------
# 常驻后台 task 的显式清单（PERF-06）
# ---------------------------------------------------------------------------
# 「必须单 worker」这条硬约束的论据是"这些循环没有分布式选主"，所以数目必须能被核对，
# 而不是文档里各写各的。数量 = 本清单里的 7 个业务循环 + 5 个辅助 task，两处都在下面
# 列明白；`_register_resident_task()` 是唯一入口，启动日志按清单长度输出。
#
# 业务循环（**有副作用，多 worker 会重复采集/重复推送**）：
#   1 卫生逾期调度  2 卫生图片补全  3 卫生图片维护  4 企微推送调度
#   5 餐厅爬虫      6 日终对账调度  7 未映射菜品巡检
# 辅助 task（幂等或本身可多实例，但仍是常驻协程）：
#   8 内存监控  9 内存清理  10 磁盘守护  11 realtime Redis 订阅  12 日志落库消费者
#   —— 这份名单（含标签）的唯一来源是下面的 `_AUXILIARY_RESIDENT_TASKS`，
#   `AUXILIARY_RESIDENT_TASK_COUNT` 必须与它等长（有测试钉着）。
#
# 8/9/10/12 由各自的组件在内部创建（`memory_manager.start_background_tasks()`、
# `disk_guard.start()`、`log_storage.start()`），11 由 `realtime_hub.start_bus()` 起；
# 它们都登记进 `_resident_tasks` 供计数，句柄由各组件自己管（见
# `_register_auxiliary_resident_tasks`）。
BUSINESS_LOOP_TASK_COUNT = 7
AUXILIARY_RESIDENT_TASK_COUNT = 5
RESIDENT_TASK_TOTAL = BUSINESS_LOOP_TASK_COUNT + AUXILIARY_RESIDENT_TASK_COUNT

# 本次 lifespan 实际注册的常驻 task，元素是 (label, task)。启动日志按它计数，
# 关闭按它统一 cancel（辅助 task 除外，见 `_AUXILIARY_TASK_LABELS`）。
_resident_tasks: "list[tuple[str, asyncio.Task]]" = []


def _register_resident_task(label: str, task) -> None:
    """把常驻 task 登记进显式清单；只做记账，不起 task、不改句柄归属。"""
    _resident_tasks.append((label, task))


def _start_resident_task(label: str, coro):
    """起一个常驻 task 并登记。返回 task 方便调用方继续持有自己的全局句柄。"""
    task = asyncio.create_task(coro)
    _register_resident_task(label, task)
    return task


# 辅助常驻 task 的 `(label, 句柄访问器)` 清单：**标签的唯一来源**。
# 注册（`_register_auxiliary_resident_tasks`）与关闭时的跳过判定
# （`_AUXILIARY_TASK_LABELS`）都从它派生——同一组字面量抄三遍时，漏改任何一处都会
# 静默失配：标签对不上的辅助 task 会在 `finally` 里被当业务循环 cancel，打断它的收尾
# （日志 flush、断 Redis）。
# 存访问器而不是句柄：这些组件在 lifespan 里才起 task，import 时取不到值。
_AUXILIARY_RESIDENT_TASKS: "tuple[tuple[str, Callable[[], object]], ...]" = (
    ("内存监控", lambda: memory_manager.monitoring_task),
    ("内存清理", lambda: memory_manager.cleanup_task),
    ("磁盘守护", lambda: disk_guard.task),
    ("realtime Redis 订阅", lambda: realtime_hub.bus and realtime_hub.bus.task),
    ("日志落库消费者", lambda: log_storage.consumer_task),
)

# 辅助 task 的标签集合：关闭时由各组件自己收尾（要 flush 日志、要断 Redis），
# `finally` 里的统一循环跳过它们，避免重复 cancel 打断收尾逻辑。
_AUXILIARY_TASK_LABELS = frozenset(label for label, _ in _AUXILIARY_RESIDENT_TASKS)


def _register_auxiliary_resident_tasks() -> None:
    """把辅助组件内部起的常驻 task 也登记进清单（句柄仍由各组件自己管）。

    这些 task 由 `memory_manager.start_background_tasks()` / `disk_guard.start()` /
    `log_storage.start()` / `realtime_hub.start_bus()` 在内部 `create_task`，main.py
    拿不到返回值——但"常驻 task 有几个"必须能被核对（PERF-06），所以按各组件自己的
    只读访问器记账；清单见 `_AUXILIARY_RESIDENT_TASKS`。
    """
    for label, handle in _AUXILIARY_RESIDENT_TASKS:
        task = handle()
        if task is not None:
            _register_resident_task(label, task)


def serialize_all(obj):
    if isinstance(obj, dict):
        return {k: serialize_all(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_all(i) for i in obj]
    elif hasattr(obj, 'isoformat'):
        return obj.isoformat()
    else:
        return obj

def _require_startup_config() -> None:
    """启动期硬前置：Redis 是部署必需组件（ADR 0090）。

    没配 `REDIS_URL` 就是部署没做完——`require_redis_url()` 抛错，应用启动失败
    并打印安装/配置指引，与 `DATABASE_BACKEND` 不是 postgres 时同款硬切。抽成
    函数是为了能在不起整个 lifespan（会拉起爬虫）的前提下测这条契约。

    `DISABLE_BACKGROUND_TASKS=true`（测试、一次性工具）没有常驻后台任务，也就
    不需要 nudge 的总线，直接跳过。
    """
    if not getattr(settings, "DISABLE_BACKGROUND_TASKS", False):
        require_redis_url()


def _sweep_stale_restore_dumps_at_startup() -> None:
    """启动期清一次残留的恢复/导出临时 dump（SEC-08 / T2-V2）。

    恢复入口与冷备脚本各自会扫一遍，但进程被强杀后留下的 dump 要等到下一次恢复
    才消失。启动期补一次——但**绝不能**把启动变成可失败点：任何异常只记 warning，
    清理失败不影响应用起来。
    """
    try:
        removed = backup_service.sweep_stale_restore_dumps()
    except Exception as exc:  # noqa: BLE001 - 清理失败不阻断启动
        logger.warning(f"⚠️ 启动期清理残留临时 dump 失败（不影响启动）: {exc}")
        return
    if removed:
        logger.info(f"🧹 启动期清理了 {len(removed)} 份上次残留的临时 dump")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global db_manager, dish_catalog, restaurant_scraper, scraper_task, wecom_push_task
    global reconcile_scheduler_task, unmapped_watchdog_task
    global recipe_store, employee_accounts, hygiene_work, hygiene_attire, hygiene_overdue_task
    global hygiene_archive
    global hygiene_variant_task, hygiene_maintenance_task

    # 每次 lifespan 从空清单开始：uvicorn --reload 会重置全局，旧清单里的 task 已随
    # 上一个进程/上一个 loop 结束，留着只会让计数失真。
    _resident_tasks.clear()
    
    try:
        logger.info("🚀 启动订单数据采集系统...")

        # Redis 是部署必需组件（realtime nudge 的跨进程广播，ADR 0090）：没配
        # REDIS_URL 就当场失败，别等建了半个运行时再炸——已经起来的循环会挂在
        # 进程里。放在最前面是为了失败信息干净。
        _require_startup_config()

        warn_if_admin_open()
        backup_import_staging.cleanup_expired_staging()
        # SEC-08 / T2-V2：上次进程被强杀留下的恢复/导出 dump 在这里清掉（尽力而为，
        # 失败只记日志；放在任何常驻业务循环之前，清完再对外服务）。
        _sweep_stale_restore_dumps_at_startup()
        app.startup_time = datetime.now(CHINA_TZ)

        # 捕获运行中的事件循环引用，供 InMemoryLogHandler.emit()（同步、可能
        # 在任意线程被调用）安全地把 logs nudge 调度回这个循环。
        logs_nudge_scheduler.bind_loop(asyncio.get_running_loop())
        
        # 初始化核心组件
        startup_results = []

        # 一次性迁移旧版 config.json 中的登录信息到加密凭据文件
        try:
            migrated = credentials_store.migrate_legacy_config()
            if migrated:
                startup_results.append("凭据迁移")
        except Exception as exc:
            logger.warning(f"⚠️ 凭据迁移过程异常（不影响启动）: {exc}")

        # 初始化数据库连接
        db_manager = DatabaseManager()
        if await db_manager.connect():
            startup_results.append("数据库")
            # Auth and get_db() read AppRuntime; set early, fill scraper later.
            set_runtime(AppRuntime(db=db_manager, dish_catalog=None, scraper=None))
            dish_catalog = DishCatalog(db_manager)
            set_runtime(AppRuntime(db=db_manager, dish_catalog=dish_catalog, scraper=None))

        # 初始化配方库（注入业务库连接：配方表与业务表同库，SQLite 独立库已退场）
        from services.recipes.store import RecipeStore
        if db_manager and db_manager.is_connected():
            recipe_store = RecipeStore(conn=db_manager._conn)
            await recipe_store.prepare()
            startup_results.append("配方库")

        from services.hygiene.accounts import EmployeeAccounts
        from services.hygiene.archive import HygieneDataArchive
        from services.hygiene.captures import FileCaptureStore
        from services.hygiene.images import ImageVariantGenerator
        from services.hygiene.notifier import WeComGroupTextNotifier
        from services.hygiene.attire import HygieneAttire
        from services.hygiene.work import HygieneWork
        from pathlib import Path
        if db_manager and db_manager.is_connected():
            employee_accounts = EmployeeAccounts(db_manager)
            await employee_accounts.prepare()
            startup_results.append("员工账号")
            capture_root = Path(settings.DATABASE_DIR) / "hygiene-captures"
            hygiene_work = HygieneWork(
                db_manager,
                captures=FileCaptureStore(capture_root),
                notifier=WeComGroupTextNotifier(db_manager),
                image_variants=ImageVariantGenerator(),
                on_change=broadcast_hygiene_change,
            )
            await hygiene_work.prepare()
            startup_results.append("卫生待办")
            # 仪容仪表（按人按天的一张自拍）：存图与现场拍摄校验借 hygiene_work 那两个
            # 公开入口，不自己再接一遍 capture store。
            hygiene_attire = HygieneAttire(hygiene_work)
            # 排班是独立系统：班次表空的时候放默认两条（白班 / 夜班）。
            #
            # 表还不存在（0005 没应用）时 prepare() 返回 False，只记日志 —— 不能在这里
            # 抛出去：补迁移的唯一入口是 Admin 的「系统更新 → 数据库迁移」面板，服务器
            # 起不来就进不去，成了死循环。同样的口径见 services/log_storage.py（0004 缺表
            # 时降级）。PG 不在启动期改结构（ADR 0089）。
            from services.scheduling.store import SchedulingStore
            if await SchedulingStore(db_manager).prepare():
                startup_results.append("排班班次")
            else:
                startup_results.append("排班班次(待迁移)")
            # 同一份 capture 目录，各持有自己的 store 实例（FileCaptureStore 无状态）。
            hygiene_archive = HygieneDataArchive(
                db_manager, captures=FileCaptureStore(capture_root)
            )
            startup_results.append("卫生数据视图")
            # 常驻后台循环在测试里关掉（见 settings.DISABLE_BACKGROUND_TASKS）
            background_enabled = not getattr(settings, "DISABLE_BACKGROUND_TASKS", False)
            if background_enabled:
                hygiene_overdue_task = _start_resident_task(
                    "卫生逾期调度", hygiene_work.overdue_scheduler_loop()
                )
                startup_results.append("卫生逾期调度器")
                hygiene_variant_task = _start_resident_task(
                    "卫生图片补全", hygiene_work.variant_backfill_loop()
                )
                hygiene_maintenance_task = _start_resident_task(
                    "卫生图片维护", hygiene_work.capture_maintenance_loop()
                )
                startup_results.append("卫生图片后台任务")

        background_enabled = not getattr(settings, "DISABLE_BACKGROUND_TASKS", False)
        if db_manager and background_enabled:
            wecom_push_task = _start_resident_task(
                "企微推送调度", wecom_push_service.scheduler_loop(db_manager)
            )
            startup_results.append("企微推送调度器")

        # 启动日志持久化（PostgreSQL logs 表，与业务表同库）
        if background_enabled and await log_storage.start():
            startup_results.append("日志存储")

        # 启动内存管理器
        if background_enabled:
            await memory_manager.start_background_tasks()
            startup_results.append("内存管理器")

        # 启动磁盘守护（阈值告警 + /api/healthz 的数据源）
        if background_enabled:
            disk_guard.start()
            startup_results.append("磁盘守护")

        # 启动 realtime 跨进程 nudge 总线（Redis pub/sub）。Redis 是部署必需
        # 组件，配置在前面已经校验过；这里只是把订阅任务起起来。**连不上不拦
        # 启动**：订阅任务在后台退避重连，本地派发不经过总线，门店照常跑。
        # 整段与那些常驻循环一样受 DISABLE_BACKGROUND_TASKS 管——测试不连 Redis。
        if background_enabled:
            await realtime_hub.start_bus()
            # `start_bus()` 刻意不 await 连接，所以这里只能说「订阅任务已起」，
            # 不能说「总线已连上」：连接状态看 /api/healthz 的 redis 段或日志。
            startup_results.append("realtime 订阅任务（连接状态见 /api/healthz 与日志）")
        else:
            logger.info("⏭️ 已跳过 realtime 总线（DISABLE_BACKGROUND_TASKS=true），nudge 仅进程内派发")
        
        # 创建餐厅爬虫适配器（测试里关掉：后台循环会在共享测试库上长期驻留）
        if background_enabled:
            restaurant_scraper = await create_restaurant_scraper(dish_catalog)
        if restaurant_scraper:
            # 从 app_settings 表加载运行配置（营业时段/轮询间隔/浏览器选项），覆盖内存默认值
            if db_manager and hasattr(restaurant_scraper, "reload_runtime_settings"):
                try:
                    await restaurant_scraper.reload_runtime_settings(db_manager)
                except Exception as exc:
                    logger.warning(f"⚠️ 加载运行配置失败，沿用默认值: {exc}")
            scraper_task = _start_resident_task("餐厅爬虫采集", run_restaurant_scraper())
            startup_results.append("餐厅爬虫")
        else:
            logger.warning("⚠️ 餐厅爬虫适配器创建失败")

        set_runtime(AppRuntime(
            db=db_manager,
            dish_catalog=dish_catalog,
            scraper=restaurant_scraper,
        ))

        def _runtime_db():
            return db_manager

        def _runtime_scraper():
            return restaurant_scraper

        if background_enabled:
            reconcile_scheduler_task = _start_resident_task(
                "日终对账调度", run_reconcile_scheduler(_runtime_db, _runtime_scraper)
            )
            unmapped_watchdog_task = _start_resident_task(
                "未映射菜品巡检", run_unmapped_dish_watchdog(_runtime_db)
            )
            startup_results.append("数据质量调度")

        # 备份点：加载保留配置，并在启动时算一次备份健康
        if db_manager:
            try:
                await backup_retention.load_retention(db_manager)
                backup_points.refresh_backup_health()
                startup_results.append("备份健康")
            except Exception as exc:
                logger.warning(f"⚠️ 备份健康计算失败（不影响启动）: {exc}")

        # 启动标识：更新健康确认据此判断「当前进程是否晚于本次重启」。
        # migrations_complete 由「待应用迁移数 == 0」驱动（PERF-11），不再是
        # 「连接已建立」；读不出来就按未完成处理。
        from services.release_update.readiness import runtime_readiness

        migrations_ok = False
        if db_manager:
            try:
                migrations_ok = await db_manager.migrations_complete()
            except Exception as exc:
                logger.warning(f"⚠️ 迁移状态判定失败（就绪按未完成处理）: {exc}")
        runtime_readiness.mark_started(migrations_complete=migrations_ok)

        # 常驻 task 清单收口后再计数：下面的数字必须能被 `_resident_tasks` 逐条核对
        # （PERF-06）。业务循环由本文件起、辅助 task 由各组件内部起，两类都登记。
        _register_auxiliary_resident_tasks()
        _registered = len(_resident_tasks)
        if _registered:
            logger.info(
                "🧵 常驻后台 task：业务循环 %d 个 + 辅助 %d 个 = 实际注册 %d 个"
                "（清单见 main.py 顶部 RESIDENT_TASK 说明）；"
                "「必须单 worker」的原因就是它们没有分布式选主",
                BUSINESS_LOOP_TASK_COUNT,
                AUXILIARY_RESIDENT_TASK_COUNT,
                _registered,
            )

        # 统一输出启动结果
        logger.info(f"🎉 系统启动完成 - 已初始化: {', '.join(startup_results)}")
        
        yield
        
    except Exception as e:
        logger.error(f"❌ 应用启动失败: {e}")
        raise
    finally:
        set_runtime(None)
        # 常驻 task 统一按 `_resident_tasks` 清单收口（PERF-06）：清单之外的循环没被
        # 取消的话，进程会留着它们，所以让"清单"与"关闭"是同一份东西。
        # 只处理**本进程起的**业务循环；辅助 task（内存/磁盘/总线/日志）各自的组件在
        # 下面有自己的收尾（要 flush 残余日志、要断开 Redis），不在这里抢着 cancel。
        for label, task in _resident_tasks:
            if label in _AUXILIARY_TASK_LABELS:
                continue
            if task is None or task.done():
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.info("✅ %s已停止", label)
        # 关闭餐厅爬虫
        if restaurant_scraper:
            try:
                await restaurant_scraper.close()
                logger.info("✅ 餐厅爬虫适配器已关闭")
            except Exception as e:
                logger.error(f"❌ 关闭餐厅爬虫适配器失败: {e}")
        
        # 关闭配方库（生产借连接时 close 是 no-op）再关主库
        if recipe_store:
            try:
                await recipe_store.close()
                logger.info("✅ 配方库已关闭")
            except Exception as e:
                logger.error(f"❌ 关闭配方库失败: {e}")

        if db_manager:
            await db_manager.close()
            logger.info("✅ 数据库连接已关闭")
        
        # 🆕 停止内存管理器
        await memory_manager.stop_background_tasks()
        logger.info("✅ 内存管理器已停止")

        # 停止磁盘守护
        await disk_guard.stop()
        logger.info("✅ 磁盘守护已停止")

        # 停止 realtime 跨进程总线（未配置 / 未启动时是 no-op；Redis 抖动不得
        # 影响关闭流程）
        try:
            await realtime_hub.stop_bus()
            if realtime_hub.bus is not None and realtime_hub.bus.enabled:
                logger.info("✅ realtime 总线已停止")
        except Exception as e:
            logger.error(f"❌ 关闭 realtime 总线失败: {e}")

        # 停止日志持久化（flush 残余 + 关闭连接）
        try:
            await log_storage.stop()
            logger.info("✅ 日志存储已停止")
        except Exception as e:
            logger.error(f"❌ 关闭日志存储失败: {e}")

        # 解绑事件循环，避免关闭后 emit() 仍尝试调度到已关闭的循环。
        logs_nudge_scheduler.bind_loop(None)

        logger.info("👋 系统已安全关闭")

# 创建FastAPI应用
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="LuckIn 订单数据采集与查询系统",
    lifespan=lifespan
)


@app.get("/api/healthz", include_in_schema=False)
async def healthz():
    """只读健康探针：进程存活 + 数据库可读 + 磁盘水位 + realtime 总线状态。

    不加鉴权（Docker HEALTHCHECK 与反向代理探针要能直接打），因此刻意不返回
    路径等环境细节，只给聚合水位。磁盘水位高**不**改状态码：重启容器腾不出
    空间，只会变成重启循环；真正该做的是告警和宿主侧清理。

    `redis` 段同理：只读总线上的现成状态位（`RedisBus.enabled` / `connected`），
    **不做主动 ping**——探针必须轻量、免鉴权、只读。总线掉线也**不**改状态码：
    本地派发不经过总线，重启进程既腾不出 Redis，又会打断门店的采集与打印；
    跨进程 nudge 失效是「降级可见」，不是「进程该被重启」。
    """
    db_status = "uninitialized"
    current = db_manager
    if current is not None:
        if _database_is_reconnecting():
            # 与中间件同一份判据：重连窗口内探针也必须报 503（healthy=False），
            # 而不是等 health_check 抛异常再拼一个 "error: ..."。
            db_status = "reconnecting"
        else:
            try:
                result = await current.health_check()
                db_status = str(result.get("status", "unknown"))
            except Exception as exc:
                db_status = f"error: {exc}"
    healthy = db_status == "healthy"
    level = disk_guard.worst_level()
    free_mb = min_free_mb()
    bus = realtime_hub.bus
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy and level == "ok" else "degraded",
            "db": db_status,
            "disk": {
                "level": level,
                "free_mb": None if free_mb is None else round(free_mb, 1),
            },
            # bus 为 None = 本进程没起订阅任务（未配 REDIS_URL 时启动已 fail-fast，
            # 只剩 DISABLE_BACKGROUND_TASKS 这类形态），跨进程 nudge 整体不可用，
            # 所以 configured 也报 False；反过来它不代表 URL 配没配。
            "redis": {
                "configured": bool(bus is not None and bus.enabled),
                "connected": bool(bus is not None and bus.connected),
            },
        },
    )


_LEGACY_EVENT_TOPIC_MAP = {
    "tables_updated": "tables",
    "orders_updated": "orders",
    "scraper_status_changed": "scraper",
    "admin_data_changed": "admin",
}


async def broadcast_realtime_event(event_type: str, **payload):
    """薄包装：把旧事件名映射为 nudge topic，委托 realtime_hub 派发。

    仅保留 `station`（用于 scope 过滤匹配）；`admin` topic 额外保留 `table`
    （前端按表刷新需要区分是哪张表变了）。其余 payload 字段对 nudge 模型
    无意义（客户端收到 nudge 后自行拉取 HTTP API），故忽略。
    """
    topic = _LEGACY_EVENT_TOPIC_MAP.get(event_type, event_type)
    scope = {}
    if "station" in payload:
        scope["station"] = payload["station"]
    if topic == "admin" and "table" in payload:
        scope["table"] = payload["table"]
    await realtime_hub.broadcast_nudge(topic, scope)


async def broadcast_hygiene_change(scope: dict) -> None:
    """Data-less hygiene nudge. Consumers re-fetch through HTTP."""
    await realtime_hub.broadcast_nudge("hygiene", scope)

# 添加自定义验证错误处理器
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """处理422验证错误，提供详细的错误信息"""
    logger.error(f"🚨 422验证错误 - {request.method} {request.url}")

    # 记录详细的验证错误
    error_details = []
    for error in exc.errors():
        error_msg = f"字段: {' -> '.join(str(loc) for loc in error['loc'])}, 错误: {error['msg']}"
        error_details.append(error_msg)
        logger.error(f"验证错误: {error_msg}")
    
    return JSONResponse(
        status_code=422,
        content={
            "detail": "数据验证失败",
            "errors": error_details,
            "error_summary": error_details
        }
    )

import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# The SPA shell is public (it only boots the client router); protected pages are
# still enforced by the router/API. The Service Worker also needs to precache it.
# 员工页面也在里面：手机上没有管理端会话，拦在服务端就永远进不去（员工会话由客户端
# 守卫查那个 cookie）。`/hygiene` 是卫生那块的入口，`/today` 是排班的「今天」页。
HTML_AUTH_EXACT = {"/login", "/login.html", "/index.html", "/hygiene", "/today"}
# Keep in lockstep with admin-web/src/utils/loginNext.js RECIPE_READER_PATHS.
# Do not use a /recipe prefix — /recipe/manage still requires a session.
# Staff-phone lives under /hygiene, /hygiene/... and /today ; /hygiene-roster is Admin SPA.
HTML_AUTH_PUBLIC_PAGES = frozenset({
    "/recipe",
    "/recipe/detail",
    "/recipe/print",
    "/recipe/qr",
})
HTML_AUTH_PREFIXES = (
    "/api/auth/",
    "/vendor/",
    "/kds",
    "/assets/",
    "/pwa/",
    "/hygiene/",
    # 员工页带尾斜杠时也放行：`/today/` 精确表兜不住，被甩到 /login 的话员工
    # 在管理端登录页登不进去。放行之后照 SPA 那套 307 回不带尾斜杠的那条。
    "/today/",
)
HTML_AUTH_SUFFIXES = (
    ".css",
    ".js",
    ".png",
    ".ico",
    ".woff",
    ".woff2",
    ".webmanifest",
)


def _is_html_auth_exempt(path: str) -> bool:
    if path in HTML_AUTH_EXACT or path in HTML_AUTH_PUBLIC_PAGES:
        return True
    for prefix in HTML_AUTH_PREFIXES:
        if path.startswith(prefix):
            return True
    for suffix in HTML_AUTH_SUFFIXES:
        if path.endswith(suffix):
            return True
    if path.startswith("/api/") or path.startswith("/ws/"):
        return True
    if settings.DEBUG and path in ("/docs", "/openapi.json", "/redoc"):
        return True
    return False


def _html_login_redirect(request: Request) -> RedirectResponse:
    path = request.url.path
    query = request.url.query
    next_path = f"{path}?{query}" if query else path
    if path in HTML_AUTH_EXACT:
        return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(
        url=f"/login?next={quote(next_path, safe='')}",
        status_code=302,
    )


class HtmlAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if _is_html_auth_exempt(path):
            return await call_next(request)
        accept = request.headers.get("accept", "")
        looks_like_page = (
            path.endswith(".html")
            or path in {"/", "/admin", "/admin/"}
            or accept.startswith("text/html")
        )
        if not looks_like_page:
            return await call_next(request)
        session_id = request.cookies.get(settings.SESSION_COOKIE_NAME)
        if await auth_service.validate_session_id(session_id):
            return await call_next(request)
        return _html_login_redirect(request)


# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 🆕 添加响应压缩中间件（性能优化）
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(
    GZipMiddleware, 
    minimum_size=1000,  # 只压缩大于1KB的响应
    compresslevel=6     # 压缩级别（1-9，6是平衡点）
)

# 🆕 添加自定义性能监控中间件
class PerformanceMiddleware(BaseHTTPMiddleware):
    """性能监控中间件"""
    
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        # 记录请求信息
        method = request.method
        url = str(request.url)
        
        try:
            response = await call_next(request)
            
            # 计算处理时间
            process_time = time.time() - start_time
            
            # 添加性能头信息
            response.headers["X-Process-Time"] = str(round(process_time * 1000, 2))
            response.headers["X-Server-Version"] = settings.APP_VERSION

            if request.url.path.startswith("/assets/") or request.url.path.startswith(
                "/kds/assets/"
            ):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif request.url.path.startswith("/pwa/icons/") or request.url.path.startswith(
                "/kds/static/pwa/"
            ):
                response.headers["Cache-Control"] = "public, max-age=86400"
            elif (
                request.url.path in {"/sw.js", "/kds/sw.js"}
                or request.url.path.endswith(".webmanifest")
                or response.headers.get("content-type", "").startswith("text/html")
            ):
                response.headers["Cache-Control"] = "no-cache"
            
            # 只记录真正的慢请求
            if process_time > 3.0:  # 超过3秒的请求
                logger.warning(f"🐌 慢请求: {method} {url} - 耗时 {process_time*1000:.2f}ms")
            
            return response
            
        except Exception as e:
            process_time = time.time() - start_time
            logger.error(f"❌ 请求处理失败: {method} {url} - 耗时 {process_time*1000:.2f}ms, 错误: {e}")
            raise

# 添加性能监控中间件
app.add_middleware(PerformanceMiddleware)

app.add_middleware(HtmlAuthMiddleware)


@app.middleware("http")
async def csrf_origin_guard(request: Request, call_next):
    """跨站写请求拦截（CSRF 纵深，判定逻辑见 ``api.security.csrf_origin_rejected``）。

    主要屏障仍是会话 cookie 的 ``SameSite=Lax``；这一层是为了「哪天为了跨站嵌入把
    cookie 放宽成 SameSite=None」时不至于写接口裸奔。
    """
    if csrf_origin_rejected(request):
        logger.warning(
            "拒绝跨站写请求 %s %s origin=%s sec-fetch-site=%s",
            request.method,
            request.url.path,
            request.headers.get("origin"),
            request.headers.get("sec-fetch-site"),
        )
        return JSONResponse(status_code=403, content={"detail": "跨站请求被拒绝"})
    return await call_next(request)


# ── 数据库不可用时的体面出口（DATA-02） ──────────────────────────────────
# 恢复/重连窗口里，连接层抛的是领域异常 DatabaseUnavailable（含子类
# DatabaseReconnecting / DatabaseBusy）。这里给它两条出路：中间件在窗口内让业务
# 路由**快速**回 503 + Retry-After，全局处理器兜住任何漏网的领域异常；两者都不再
# 逐请求打 traceback，一个窗口只留一条结构化日志。
_DB_STATUS_PATHS = frozenset({"/api/healthz", "/api/system/health"})
_DB_RETRY_AFTER_SECONDS = "1"
_db_unavailable_logged = False


def _database_is_reconnecting() -> bool:
    """连接层是否正处在重连窗口（``/api/healthz`` 与中间件共用这一份判据）。"""
    if db_manager is None:
        return False
    probe = getattr(db_manager, "is_reconnecting", None)
    if not callable(probe):
        return False
    try:
        return bool(probe())
    except Exception:  # pragma: no cover - 判据本身不该把请求变成 500
        return False


def _log_database_unavailable(detail: str, *, state: str = "reconnecting") -> None:
    """每个重连窗口只记一条结构化记录（原先每个请求一条 traceback）。

    ``state`` 带上连接层给出的原因（``reconnecting`` / ``unavailable`` /
    ``write_lock_timeout``），否则运维只看到「有人 503 了」而看不出是哪一种。
    """
    global _db_unavailable_logged
    if _db_unavailable_logged:
        return
    _db_unavailable_logged = True
    logger.warning(
        "数据库不可用（%s）：业务请求快速返回 503（同一窗口后续请求不再重复记录） %s",
        state,
        detail,
    )


def _database_unavailable_response(exc: Optional[Exception] = None) -> JSONResponse:
    """503 + ``Retry-After`` + 响应体 ``retryable: true``：调用方可以原样重试。"""
    detail = (
        str(exc)
        if exc is not None
        else "数据库暂时不可用（正在重连或恢复），请稍后重试"
    )
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": _DB_RETRY_AFTER_SECONDS},
        content={
            "error": "数据库暂时不可用",
            "detail": detail,
            "retryable": True,
            "reason": getattr(exc, "reason", "reconnecting"),
            "timestamp": datetime.now(CHINA_TZ).isoformat(),
        },
    )


@app.middleware("http")
async def database_unavailable_guard(request: Request, call_next):
    """数据库处于重连/恢复窗口时，业务路由快速回 503（可重试）。

    判据是连接层自己开的那扇窗（``PgConnection._reconnect_raw`` 开窗、新连接建好
    关窗），与 ``/api/healthz`` 用的是同一份。探针路由不拦：它们的语义就是「库不
    可用时也给出自己的 503 诊断」。
    """
    global _db_unavailable_logged
    if request.url.path not in _DB_STATUS_PATHS and _database_is_reconnecting():
        _log_database_unavailable(f"{request.method} {request.url.path}")
        return _database_unavailable_response()
    response = await call_next(request)
    if not _database_is_reconnecting():
        # 窗口关掉后，下一个窗口重新记一条。
        _db_unavailable_logged = False
    return response


@app.websocket("/ws/realtime")
async def realtime_ws(websocket: WebSocket):
    """实时订阅通道：客户端按 topic + 过滤条件订阅，服务端只推送“有变”nudge
    （不带数据），页面收到后复用现有 HTTP API 拉取最新数据。
    鉴权支持 Session Cookie（网页端）或 ?token=<api_token>（KDS 等无 Cookie 客户端），
    以及员工（卫生/排班手机端）的 Session Cookie。

    员工连接要把 employee_id 交给 hub：员工只该收到全店级事件和自己的个人事件，
    scope 里带同事 employee_id 的 nudge 在派发侧直接跳过（`_staff_owns_scope`）。
    身份只在这里定一次，subscribe 里客户端自报的 filters 不作数。
    """
    await websocket.accept()
    identity = await identify_ws(websocket)
    if identity is None:
        await websocket.close(code=4401)
        return
    await realtime_hub.register(websocket, identity.auth, employee_id=identity.employee_id)
    await websocket.send_json({"type": "connected"})
    try:
        while True:
            raw = await websocket.receive_text()
            await realtime_hub.handle_message(websocket, raw)
    except WebSocketDisconnect:
        realtime_hub.unregister(websocket)
    except Exception:
        realtime_hub.unregister(websocket)

# ==================== API 鉴权边界（SEC-02） ====================
# 登录墙（HtmlAuthMiddleware）只拦「看起来像页面」的请求，API 请求天然绕过它。
# 于是业务读接口以前依赖树里只有 get_db：门店局域网里任何未登录设备都能拉走
# 订单明细、营业额、桌台占用、档口映射、备货计划与配方内容。
#
# 现在的规则：**业务/管理面在注册处统一挂 verify_admin_token，公开面必须登记在
# 下面这份清单里**。清单是唯一的例外入口——要开洞先回答三个问题（谁在读、带什么
# 凭据、漏了会怎样），再把它写进来；别在各路由文件里零散开洞，那样没人看得全。
# tests/test_api_read_auth.py 会拿这份清单去核对真实路由表：清单外的 /api 路由
# 必须带守卫，否则用例变红。
PUBLIC_API_SURFACE: tuple[tuple[str, str], ...] = (
    # 探针：Docker HEALTHCHECK / 反代 / 部署冒烟脚本，刻意免鉴权且只给聚合水位
    ("GET", "/api/healthz"),
    # 就绪探针：KDS 设置页「测试连接」在还没配 Token 时就要能打（换地址先探活），
    # 更新作业与部署验收清单也 curl 它
    ("GET", "/api/system/health"),
    # SEC-01 的决定：只读的采集状态供运维探针，保持开放
    ("GET", "/api/scraper/status"),
    # 登录墙自己的入口：不开放就没人能登录 / 初始化
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/init"),
    ("POST", "/api/auth/login"),
    # 卫生员工端入口：员工手机没有管理端会话，这里靠按 IP / 手机号的登录限流兜底
    ("POST", "/api/hygiene/staff/login"),
    ("POST", "/api/hygiene/staff/register"),
    # 配方阅读面：/recipe、/recipe/detail、/recipe/print、/recipe/qr 是扫码即看的
    # 免登录页面（RECIPE_READER_META.public），这些读接口断了后厨就白屏。
    # 同模块的管理面（岗位增删改、全量行、导出、历史）不在此列，见 api/recipes.py。
    ("GET", "/api/recipes/search"),
    ("GET", "/api/recipes/stations"),
    ("GET", "/api/recipes/stations/{slug}"),
    # API 信息页：只有版本号与 docs 链接
    ("GET", "/api"),
)

# 注册API路由
# —— 公开面（清单见 PUBLIC_API_SURFACE）——
app.include_router(auth_router)          # 登录 / 初始化
app.include_router(hygiene_router)       # 员工端走 require_staff_session，管理端走 require_session
app.include_router(recipes_public_router)  # 扫码即看的配方阅读面
# —— 业务面：统一挂管理员凭据（会话 cookie / X-Admin-Token / Bearer）——
app.include_router(orders.router, dependencies=[Depends(verify_admin_token)])
app.include_router(dishes.router, dependencies=[Depends(verify_admin_token)])
app.include_router(dish_stations.router, dependencies=[Depends(verify_admin_token)])
app.include_router(semi_rules.router, dependencies=[Depends(verify_admin_token)])
app.include_router(report_dishes.router, dependencies=[Depends(verify_admin_token)])
app.include_router(prep_plan.router, dependencies=[Depends(verify_admin_token)])
app.include_router(tables_router, dependencies=[Depends(verify_admin_token)])
app.include_router(analytics_router, dependencies=[Depends(verify_admin_token)])
app.include_router(export_router, dependencies=[Depends(verify_admin_token)])
app.include_router(recipes_router, dependencies=[Depends(verify_admin_token)])
# —— 管理面：各自在 APIRouter(dependencies=...) 里已带凭据 ——
app.include_router(wecom_push.router)
app.include_router(admin_router)
app.include_router(credentials_router)
app.include_router(db_credentials_router)
app.include_router(backup_router)
app.include_router(release_update_router)
app.include_router(db_migrations_router)
app.include_router(runtime_settings_router)
app.include_router(logs_router)
# 排班自己带 require_session（管理端页面用），不挂 verify_admin_token：
# 店长用的是浏览器会话，不是 API token。
app.include_router(scheduling_router)

# 静态文件（仪表盘 + 管理后台）
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

public_dir = os.path.join(os.path.dirname(__file__), "public")

spa_dir = os.path.join(os.path.dirname(__file__), "admin-web", "dist")
spa_index_path = os.path.join(spa_dir, "index.html")


def _spa_index():
    """Return admin-web SPA index.html; vue-router owns client routes."""
    if not os.path.isfile(spa_index_path):
        raise HTTPException(status_code=404, detail="管理后台前端未构建")
    return FileResponse(spa_index_path, headers={"Cache-Control": "no-cache"})


def _spa_asset(relative_path: str, media_type: str):
    target = os.path.join(spa_dir, relative_path)
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="管理后台 PWA 资源未构建")
    return FileResponse(
        target,
        media_type=media_type,
        headers={"Cache-Control": "no-cache"},
    )


def _kds_asset(relative_path: str, media_type: str):
    target = os.path.join(public_dir, "kds", relative_path)
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail="KDS PWA 资源未构建")
    return FileResponse(
        target,
        media_type=media_type,
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/sw.js", include_in_schema=False)
async def admin_service_worker():
    return _spa_asset("sw.js", "application/javascript")


@app.get("/pwa/manifests/{manifest_name}", include_in_schema=False)
async def admin_manifest(manifest_name: str):
    if manifest_name not in {
        "admin.webmanifest",
        "hygiene.webmanifest",
        "recipe.webmanifest",
    }:
        raise HTTPException(status_code=404, detail="PWA manifest 不存在")
    return _spa_asset(
        f"pwa/manifests/{manifest_name}",
        "application/manifest+json",
    )


@app.get("/pwa/icons/{icon_name}", include_in_schema=False)
async def admin_pwa_icon(icon_name: str):
    if not icon_name.endswith(".png"):
        raise HTTPException(status_code=404, detail="PWA 图标不存在")
    return _spa_asset(f"pwa/icons/{icon_name}", "image/png")


@app.get("/kds/sw.js", include_in_schema=False)
async def kds_service_worker():
    return _kds_asset("sw.js", "application/javascript")


@app.get("/kds/manifest.webmanifest", include_in_schema=False)
async def kds_manifest():
    return _kds_asset("manifest.webmanifest", "application/manifest+json")


# ---- admin-web SPA 页面路由（Phase 4.6：统一服务同一 SPA，登录/配置也走 SPA） ----
# 未登录访问由 HtmlAuthMiddleware 服务端重定向到 /login（配方阅读面、KDS、/login 豁免）。
#
# 清单是模块级常量：注册与 tests/test_spa_page_routes.py 的前后端契约测试共用同一份，
# 新增 vue-router 页面时必须同步补这里。main.py 没有 catch-all，反代的
# `try_files … /index.html` 也只写在 admin|sales-report|logs|prep-plan|wecom-push|recipe
# 六个前缀的白名单块里（deploy/nginx.conf、deploy/Caddyfile），hygiene 页面一律落到
# 反代兜底转发 —— 漏一条就是直连/反代硬导航 404（DOC-01 的 /hygiene-data 就是这么漏的）。
SPA_PAGE_ROUTES = (
    "/",
    "/index.html",
    "/admin",
    "/admin/",
    "/login",
    "/setup",
    "/sales-report",
    "/prep-plan",
    "/wecom-push",
    "/recipe",
    "/recipe/detail",
    "/recipe/print",
    "/recipe/manage",
    "/recipe/qr",
    "/logs",
    "/hygiene",
    "/hygiene/login",
    "/hygiene/register",
    "/today",
    "/today/month",
    "/hygiene-roster",
    "/hygiene-zones",
    "/hygiene-daily",
    "/hygiene-deep-clean",
    "/hygiene-fix",
    "/hygiene-boards",
    "/hygiene-data",
    "/hygiene-attire",
    "/scheduling",
    "/scheduling/inbox",
    "/scheduling/shifts",
)


async def spa_page():
    return _spa_index()


for _spa_page_path in SPA_PAGE_ROUTES:
    app.get(_spa_page_path)(spa_page)

@app.get("/README.md")
async def readme_page():
    readme_path = os.path.join(os.path.dirname(__file__), "README.md")
    return FileResponse(readme_path, media_type="text/markdown")

# SPA 配方页作用域样式（useScopedStylesheet 加载 /recipe.css）：优先取 dist 内构建产物。
@app.get("/recipe.css")
async def recipe_css():
    spa_css = os.path.join(spa_dir, "recipe.css")
    target = spa_css if os.path.exists(spa_css) else os.path.join(public_dir, "recipe.css")
    return FileResponse(target, media_type="text/css")

@app.get("/hygiene-admin.css")
async def hygiene_admin_css():
    spa_css = os.path.join(spa_dir, "hygiene-admin.css")
    if os.path.exists(spa_css):
        return FileResponse(spa_css, media_type="text/css")
    # admin-web/dist 还没构建时回落到仓库根 public/ 的同名副本（内容由
    # admin-web 的契约测试强制与 canonical 一致）。这条回落路径以前是静默的：
    # 两份文件分叉成相反主题，落到这里就是浅底浅字、拍照页几乎不可读，所以留日志。
    logger.warning(
        "admin-web/dist 未构建，/hygiene-admin.css 回落到 public/hygiene-admin.css"
    )
    return FileResponse(
        os.path.join(public_dir, "hygiene-admin.css"),
        media_type="text/css",
    )

vendor_dir = os.path.join(public_dir, "vendor")
if os.path.isdir(vendor_dir):
    app.mount("/vendor", StaticFiles(directory=vendor_dir), name="vendor")

# admin-web SPA 构建产物（JS/CSS chunk）
spa_assets_dir = os.path.join(spa_dir, "assets")
if os.path.isdir(spa_assets_dir):
    app.mount("/assets", StaticFiles(directory=spa_assets_dir), name="spa-assets")

kds_dir = os.path.join(public_dir, "kds")
if os.path.isdir(kds_dir):
    app.mount("/kds", StaticFiles(directory=kds_dir, html=True), name="kds")


@app.get("/kds")
async def kds_root():
    return RedirectResponse("/kds/", status_code=307)

# 系统状态API
@app.get("/api/system/status", dependencies=[Depends(verify_admin_token)])
async def get_system_status():
    """获取系统状态（含库表行数、内存与磁盘路径，故需管理员凭据）。"""
    try:
        # 获取数据库统计
        db_stats = {}
        if db_manager:
            try:
                collection_stats = await db_manager.get_collection_stats()
                db_stats = collection_stats
            except:
                db_stats = {"error": "无法获取数据库统计"}
        
        # 🆕 获取内存统计
        memory_stats = memory_manager.get_memory_stats()

        # 🆕 磁盘水位：健康页要画水位图，需要分母（total/used_pct）。healthz 按设计
        # 只给聚合 free_mb（它服务探针），所以放在这里。探测失败不能让整个接口 500。
        try:
            disk_items = disk_guard.snapshot()
            worst = min(disk_items, key=lambda item: item["free_mb"]) if disk_items else None
            disk_stats = {
                "level": disk_guard.worst_level(),
                "threshold_free_mb": settings.UPDATE_MIN_FREE_MB,
                "worst": worst,
                "paths": disk_items,
            }
        except Exception as exc:
            logger.warning(f"读取磁盘水位失败: {exc}")
            disk_stats = {"error": "无法读取磁盘水位"}
        
        # 计算运行时间
        startup_time = getattr(app, 'startup_time', datetime.now(CHINA_TZ))
        uptime = int((datetime.now(CHINA_TZ) - startup_time).total_seconds())
        
        result = {
            "status": "running",
            "uptime": uptime,
            "version": settings.APP_VERSION,
            "database": db_stats,
            "memory": memory_stats,
            "disk": disk_stats,
            "last_update": datetime.now(CHINA_TZ)
        }
        return serialize_all(result)
        
    except Exception as e:
        logger.error(f"获取系统状态失败: {e}")
        raise HTTPException(status_code=500, detail="获取系统状态失败")

@app.get("/api/system/health")
async def health_check():
    """就绪口径的健康检查：数据层可用 + 可比较的启动标识。

    Update Job 只负责「已切换发行包并发出重启」，更新是否成功由管理后台用这里的
    结论回写。KDS 等外部客户端只依赖 200 状态码，因此未就绪时仍返回 200。

    免鉴权（见 PUBLIC_API_SURFACE）：KDS 设置页的「测试连接」要在**还没配 Token**
    时探活，更新作业与部署验收清单也要 curl 它；这里只回就绪口径，不含业务数据。
    """
    from services.release_update.readiness import AppReadinessAdapter

    adapter = AppReadinessAdapter(lambda: db_manager)
    readiness = await adapter.inspect_readiness()
    return {
        "status": "healthy" if readiness.ready else "unhealthy",
        **adapter.readiness_payload(readiness),
        "version": settings.APP_VERSION,
        "timestamp": datetime.now(CHINA_TZ),
    }


@app.get("/api/system/scraper-health", dependencies=[Depends(verify_admin_token)])
async def get_scraper_health():
    """采集与对账健康（只读，供后台健康页 / 监控大屏）。"""
    from services.scraper_health import read_health, current_biz_date_str
    from services.reconcile_job import is_reconcile_running

    health = read_health()
    return {
        "success": True,
        "health": {
            "biz_date": health.get("biz_date") or current_biz_date_str(),
            "api_failures": health.get("api_failures", 0),
            # 阈值一并给出：健康页要在图里画阈值线，前端自己硬编码会与配置漂移
            "api_failures_threshold": settings.SCRAPER_ALERT_FAILURE_THRESHOLD,
            "delivery_bills_pending": health.get("delivery_bills_pending"),
            "last_scrape_at": health.get("last_scrape_at"),
            "last_reconcile": health.get("last_reconcile"),
            "reconcile_running": is_reconcile_running(),
            "updated_at": health.get("updated_at"),
        },
    }


@app.get("/api/dashboard/summary", dependencies=[Depends(verify_admin_token)])
async def get_dashboard_summary():
    """首页仪表盘聚合接口，减少前端轮询请求数量（含营业额与近期订单）。"""
    try:
        if db_manager is None:
            raise HTTPException(status_code=500, detail="数据库未初始化")

        # 仪表盘口径 = 当前营业日半开区间 [06:00, 次日 06:00)（票 22）：
        # 近期订单与档口计数共用同一个起点，档口计数同时传右端。
        business_day_start, business_day_end = business_day_window(datetime.now(CHINA_TZ))
        orders_stats = await db_manager.orders.aggregate_orders_stats()
        hot_dishes = await db_manager.orders.aggregate_hot_dishes(limit_n=10)
        recent_orders = await db_manager.orders.get_orders(start_time=business_day_start, limit=30)
        station_stats = await db_manager.orders.aggregate_station_counts(business_day_start, business_day_end)
        dashboard_extras = await db_manager.reports.aggregate_dashboard_extras()
        kds_backlog = await db_manager.reports.aggregate_kds_backlog()

        scraper_status = "not_started"
        if scraper_task:
            scraper_status = "running" if not scraper_task.done() else "completed"
            if scraper_task.done() and scraper_task.cancelled():
                scraper_status = "cancelled"

        from services.scraper_health import read_health, current_biz_date_str
        from services.reconcile_job import is_reconcile_running

        health = read_health()
        data_quality = {
            "biz_date": health.get("biz_date") or current_biz_date_str(),
            "api_failures": health.get("api_failures", 0),
            "last_scrape_at": health.get("last_scrape_at"),
            "last_reconcile": health.get("last_reconcile"),
            "reconcile_running": is_reconcile_running(),
        }

        db_stats = {}
        try:
            db_stats = await db_manager.get_collection_stats()
        except Exception as exc:
            logger.warning(f"仪表盘数据库统计失败: {exc}")

        result = {
            "success": True,
            "orders": orders_stats,
            "stations": station_stats,
            "hot_dishes": hot_dishes,
            "recent_orders": recent_orders[:20],
            "dashboard": dashboard_extras,
            "kds_backlog": kds_backlog,
            "system": {
                "version": settings.APP_VERSION,
                "database": db_stats,
                "memory": memory_manager.get_memory_stats(),
                "uptime": int((datetime.now(CHINA_TZ) - getattr(app, 'startup_time', datetime.now(CHINA_TZ))).total_seconds()),
            },
            "scraper": {
                "status": scraper_status,
                "paused": bool(getattr(restaurant_scraper, "paused", False)),
            },
            "data_quality": data_quality,
            "timestamp": datetime.now(CHINA_TZ),
        }
        return serialize_all(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取仪表盘聚合数据失败: {e}")
        raise HTTPException(status_code=500, detail="获取仪表盘聚合数据失败")

# 档口配置API
@app.get("/api/stations", dependencies=[Depends(verify_admin_token)])
async def get_stations():
    """获取档口配置（admin-web 走会话 cookie，KDS 走 X-Admin-Token）"""
    from config import KITCHEN_STATIONS
    return list(KITCHEN_STATIONS.values())

@app.get("/api/stations/{station_id}", dependencies=[Depends(verify_admin_token)])
async def get_station_info(station_id: str):
    """获取档口信息"""
    from config import KITCHEN_STATIONS
    station = KITCHEN_STATIONS.get(station_id)
    if not station:
        raise HTTPException(status_code=404, detail="档口不存在")
    return station

# 餐厅爬虫控制API
@app.post("/api/scraper/start", dependencies=[Depends(verify_admin_token)])
async def start_scraper():
    """启动餐厅数据爬取（需管理员凭据，见 SEC-01）"""
    global scraper_task
    
    try:
        if scraper_task and not scraper_task.done():
            return {"message": "爬虫任务已在运行中"}
        
        scraper_task = asyncio.create_task(run_restaurant_scraper())
        await broadcast_realtime_event("scraper_status_changed", status="running")
        return {"message": "餐厅数据爬取已启动"}
        
    except Exception as e:
        logger.error(f"启动爬虫失败: {e}")
        raise HTTPException(status_code=500, detail="启动爬虫失败")

@app.post("/api/scraper/stop", dependencies=[Depends(verify_admin_token)])
async def stop_scraper():
    """停止餐厅数据爬取（需管理员凭据，见 SEC-01）"""
    global scraper_task
    
    try:
        if scraper_task and not scraper_task.done():
            scraper_task.cancel()
            try:
                await scraper_task
            except asyncio.CancelledError:
                logger.info("✅ 餐厅爬虫任务已停止")
            await broadcast_realtime_event("scraper_status_changed", status="cancelled")
            return {"message": "餐厅数据爬取已停止"}
        else:
            return {"message": "爬虫任务未在运行"}
            
    except Exception as e:
        logger.error(f"停止爬虫失败: {e}")
        raise HTTPException(status_code=500, detail="停止爬虫失败")

@app.get("/api/scraper/status")
async def get_scraper_status():
    """获取爬虫状态（免鉴权，见 PUBLIC_API_SURFACE：SEC-01 保留的运维探针）"""
    global scraper_task, restaurant_scraper
    
    try:
        if scraper_task:
            if scraper_task.done():
                if scraper_task.cancelled():
                    status = "cancelled"
                else:
                    status = "completed"
            else:
                status = "running"
        else:
            status = "not_started"
        
        status_dto = restaurant_scraper.get_status() if restaurant_scraper else {}
        paused = bool(status_dto.get("paused", False))
        has_credentials = bool(status_dto.get("has_credentials", False))
        initialized = bool(status_dto.get("initialized", False))
        no_credentials = bool(status_dto.get("no_credentials", not has_credentials))
        last_login_failed_at = status_dto.get("last_login_failed_at")
        retry_cooldown_seconds = int(status_dto.get("login_retry_cooldown_seconds", 0) or 0)
        retry_wait_seconds = 0
        if last_login_failed_at is not None and retry_cooldown_seconds > 0:
            elapsed_seconds = (datetime.now(CHINA_TZ) - last_login_failed_at).total_seconds()
            retry_wait_seconds = max(0, int(retry_cooldown_seconds - elapsed_seconds))

        if no_credentials:
            login_state = "no_credentials"
        elif initialized:
            login_state = "logged_in"
        elif last_login_failed_at is not None and retry_wait_seconds > 0:
            login_state = "login_failed_cooldown"
        else:
            login_state = "not_logged_in"

        result = {
            "status": status,
            "paused": paused,
            "login_ok": bool(initialized and not paused),
            "login_state": login_state,
            "has_credentials": has_credentials,
            "last_login_failed_at": last_login_failed_at,
            "retry_wait_seconds": retry_wait_seconds,
            "business_hours": {
                "work_start": "07:30",
                "work_end": "21:30", 
                "rest_start": "21:30",
                "rest_end": "07:30",
                "description": "营业时间: 07:30-21:30，休息时间: 21:30-次日07:30"
            },
            "last_update": datetime.now(CHINA_TZ)
        }
        return serialize_all(result)
        
    except Exception as e:
        logger.error(f"获取爬虫状态失败: {e}")
        raise HTTPException(status_code=500, detail="获取爬虫状态失败")


# `/api/logs/recent` 定义在 api/logs.py（router 级 `dependencies=[Depends(verify_admin_token)]`，
# 见 main.py 上方的 include_router）。这里原来**另有一条同路径的无鉴权 handler**：两条各注册
# 一次，生效者取决于声明顺序（include_router 在前，所以安全的那条当时胜出）。留着它就是
# 一个静默降级开关——把这条 `@app.get` 挪到 include_router 之前，同一 URL 立刻匿名可读，
# 而没有任何测试会红。已删除；契约由 tests/test_logs_recent_route_contract.py 钉住
# （路径唯一 + 依赖树含 verify_admin_token）。

async def _send_scraper_health_alert(message: str) -> None:
    """经既有企微告警通道推送爬虫健康告警。

    在调用时（而非任务启动时）解析全局 `db_manager`，以兼容 uvicorn --reload
    重置全局变量的情况，与本文件其它路由/任务保持一致的解析时机模式。
    """
    if db_manager is None:
        logger.warning("db_manager 未初始化，跳过爬虫健康告警推送")
        return
    from services.data_quality_alerts import send_to_enabled_webhooks

    result = await send_to_enabled_webhooks(db_manager, message)
    if result.get("sent", 0) == 0:
        logger.warning(f"⚠️ 爬虫健康告警未发送成功: {result}")
    else:
        logger.warning(f"⚠️ 爬虫健康告警已发送: {message}")


# 餐厅数据爬取任务
async def run_restaurant_scraper():
    """运行餐厅数据爬取任务"""
    global restaurant_scraper

    # 只看「有没有抛异常」会把 run_cycle 吞掉的明细接口硬失败记成成功（CORR-03），
    # 所以把进程内的 POS 失败计数交给 tracker：本轮增量为 0 才算干净的一轮。
    failure_tracker = ScraperFailureTracker(
        alert_sender=_send_scraper_health_alert,
        api_failures_fn=lambda: restaurant_scraper.settled_api_failures,
    )

    try:
        logger.info("🔄 开始餐厅数据爬取任务")
        
        while True:
            try:
                # 没有登录凭据时进入待机，等待用户在 /setup 配置后由 reload_credentials 唤醒
                if restaurant_scraper.no_credentials:
                    logger.info("⏸️  无登录凭据，请访问 /setup 页面配置后再启动爬取")
                    await asyncio.sleep(60)
                    restaurant_scraper.load_credentials_sync()
                    continue

                # 检查营业时间并更新暂停状态
                restaurant_scraper.refresh_business_hours()

                if restaurant_scraper.paused:
                    logger.info("⏸️  爬虫已暂停（非营业时间），等待营业时间...")
                    # 打烊后一挂就是十来个小时，不释放的话 headless Chromium 会一直占着内存
                    if await restaurant_scraper.release_browser():
                        logger.info("🧹 非营业时间，已释放浏览器资源")
                    await asyncio.sleep(300)  # 暂停时每5分钟检查一次
                    continue

                await failure_tracker.run_once(
                    lambda: restaurant_scraper.run_cycle(db_manager)
                )

                interval = restaurant_scraper.poll_interval_seconds()
                logger.debug(f"⏳ 等待{interval}秒后进行下一次采集...")
                await asyncio.sleep(interval)
                
            except asyncio.CancelledError:
                logger.info("🛑 餐厅数据爬取任务被取消")
                break
            except Exception as e:
                logger.error(f"❌ 餐厅数据爬取失败: {e}")
                await asyncio.sleep(30)  # 出错后等待30秒再重试
                
    except Exception as e:
        logger.error(f"❌ 餐厅数据爬取任务异常: {e}")

# 错误处理
@app.exception_handler(DatabaseUnavailable)
async def database_unavailable_handler(request: Request, exc: DatabaseUnavailable):
    """连接层的领域异常 → 503 + ``Retry-After`` + ``retryable: true``（DATA-02）。

    没有这一层时，恢复窗口里的 ``RuntimeError`` 会落进下面的全局处理器，被压成
    500「服务器内部错误」——调用方看不出这只是「稍后重试」。
    """
    _log_database_unavailable(
        f"{request.method} {request.url.path}",
        state=getattr(exc, "reason", "unavailable"),
    )
    return _database_unavailable_response(exc)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """全局异常处理"""
    logger.exception(f"全局异常: {request.method} {request.url}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "服务器内部错误",
            "detail": "服务处理请求时发生异常",
            "timestamp": datetime.now(CHINA_TZ).isoformat()
        }
    )

@app.get("/api")
async def api_root():
    """API 信息"""
    return {
        "message": "LuckIn 订单数据采集与查询系统",
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "status": "running"
    }

if __name__ == "__main__":
    # 启动服务器
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        workers=settings.WORKERS
    ) 
