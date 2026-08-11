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
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from urllib.parse import quote
from fastapi.exceptions import RequestValidationError

from config import settings
from database import DatabaseManager
from api import orders, dishes, dish_stations, semi_rules, report_dishes, prep_plan, wecom_push
from api.admin import router as admin_router
from api.recipes import router as recipes_router
from api.credentials import router as credentials_router
from api.backup import router as backup_router
from api.release_update import router as release_update_router
from api.runtime_settings import router as runtime_settings_router
from api.logs import router as logs_router
from api.tables import router as tables_router
from api.analytics import router as analytics_router
from api.export_api import router as export_router
from api.security import authenticate_ws, warn_if_admin_open
from api.auth import router as auth_router
from services import auth_service
from services import backup_import_staging
from scraper.restaurant_scraper import create_restaurant_scraper
from services import credentials_store
from services.dish_catalog import DishCatalog
from services.app_runtime import AppRuntime, set_runtime
from services.memory_manager import memory_manager
from services.log_storage import log_storage, LogStorageHandler
from services.wecom_push_service import wecom_push_service
from services.scraper_failure_tracker import ScraperFailureTracker
from services.data_quality_scheduler import run_reconcile_scheduler, run_unmapped_dish_watchdog
from services.realtime.hub import realtime_hub
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

# 持久化 handler：把日志投递到 LogStorage 队列，由后台协程批量写入 SQLite。
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
reconcile_scheduler_task = None
unmapped_watchdog_task = None
recipe_store = None

def serialize_all(obj):
    if isinstance(obj, dict):
        return {k: serialize_all(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_all(i) for i in obj]
    elif hasattr(obj, 'isoformat'):
        return obj.isoformat()
    else:
        return obj

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global db_manager, dish_catalog, restaurant_scraper, scraper_task, wecom_push_task
    global reconcile_scheduler_task, unmapped_watchdog_task
    global recipe_store
    
    try:
        logger.info("🚀 启动订单数据采集系统...")
        warn_if_admin_open()
        backup_import_staging.cleanup_expired_staging()
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

        # 初始化配方库（独立 data/recipes.db，与 db_manager 解耦）
        from services.recipes.store import RecipeStore
        recipe_store = RecipeStore()
        if await recipe_store.connect():
            startup_results.append("配方库")

        if db_manager:
            wecom_push_task = asyncio.create_task(wecom_push_service.scheduler_loop(db_manager))
            startup_results.append("企微推送调度器")

        # 启动日志持久化（独立 logs.db）
        if await log_storage.start():
            startup_results.append("日志存储")

        # 启动内存管理器
        await memory_manager.start_background_tasks()
        startup_results.append("内存管理器")
        
        # 创建餐厅爬虫适配器
        restaurant_scraper = await create_restaurant_scraper(dish_catalog)
        if restaurant_scraper:
            # 从 app_settings 表加载运行配置（营业时段/轮询间隔/浏览器选项），覆盖内存默认值
            if db_manager and hasattr(restaurant_scraper, "reload_runtime_settings"):
                try:
                    await restaurant_scraper.reload_runtime_settings(db_manager)
                except Exception as exc:
                    logger.warning(f"⚠️ 加载运行配置失败，沿用默认值: {exc}")
            scraper_task = asyncio.create_task(run_restaurant_scraper())
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

        reconcile_scheduler_task = asyncio.create_task(
            run_reconcile_scheduler(_runtime_db, _runtime_scraper)
        )
        unmapped_watchdog_task = asyncio.create_task(run_unmapped_dish_watchdog(_runtime_db))
        startup_results.append("数据质量调度")

        # 统一输出启动结果
        logger.info(f"🎉 系统启动完成 - 已初始化: {', '.join(startup_results)}")
        
        yield
        
    except Exception as e:
        logger.error(f"❌ 应用启动失败: {e}")
        raise
    finally:
        set_runtime(None)
        # 关闭餐厅爬虫任务
        if scraper_task and not scraper_task.done():
            scraper_task.cancel()
            try:
                await scraper_task
            except asyncio.CancelledError:
                logger.info("✅ 餐厅爬虫任务已停止")

        if wecom_push_task and not wecom_push_task.done():
            wecom_push_task.cancel()
            try:
                await wecom_push_task
            except asyncio.CancelledError:
                logger.info("✅ 企微推送调度器已停止")

        for task_name, task in (
            ("日终对账调度", reconcile_scheduler_task),
            ("未映射菜品巡检", unmapped_watchdog_task),
        ):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.info("✅ %s已停止", task_name)
        
        # 关闭餐厅爬虫
        if restaurant_scraper:
            try:
                await restaurant_scraper.close()
                logger.info("✅ 餐厅爬虫适配器已关闭")
            except Exception as e:
                logger.error(f"❌ 关闭餐厅爬虫适配器失败: {e}")
        
        # 关闭数据库连接
        if db_manager:
            await db_manager.close()
            logger.info("✅ 数据库连接已关闭")

        # 关闭配方库
        if recipe_store:
            try:
                await recipe_store.close()
                logger.info("✅ 配方库已关闭")
            except Exception as e:
                logger.error(f"❌ 关闭配方库失败: {e}")
        
        # 🆕 停止内存管理器
        await memory_manager.stop_background_tasks()
        logger.info("✅ 内存管理器已停止")

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

HTML_AUTH_EXACT = {"/login", "/login.html"}
HTML_AUTH_PREFIXES = ("/api/auth/", "/vendor/")
HTML_AUTH_SUFFIXES = (".css", ".js", ".png", ".ico", ".woff", ".woff2")


def _is_html_auth_exempt(path: str) -> bool:
    if path in HTML_AUTH_EXACT:
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
        return RedirectResponse(url=f"/login?next={quote(path)}", status_code=302)


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


@app.websocket("/ws/realtime")
async def realtime_ws(websocket: WebSocket):
    """实时订阅通道：客户端按 topic + 过滤条件订阅，服务端只推送“有变”nudge
    （不带数据），页面收到后复用现有 HTTP API 拉取最新数据。
    鉴权支持 Session Cookie（网页端）或 ?token=<api_token>（KDS 等无 Cookie 客户端）。
    """
    await websocket.accept()
    auth = await authenticate_ws(websocket)
    if auth is None:
        await websocket.close(code=4401)
        return
    await realtime_hub.register(websocket, auth)
    await websocket.send_json({"type": "connected"})
    try:
        while True:
            raw = await websocket.receive_text()
            await realtime_hub.handle_message(websocket, raw)
    except WebSocketDisconnect:
        realtime_hub.unregister(websocket)
    except Exception:
        realtime_hub.unregister(websocket)

# 注册API路由
app.include_router(auth_router)
app.include_router(orders.router)
app.include_router(dishes.router)
app.include_router(dish_stations.router)
app.include_router(semi_rules.router)
app.include_router(report_dishes.router)
app.include_router(prep_plan.router)
app.include_router(wecom_push.router)
app.include_router(admin_router)
app.include_router(credentials_router)
app.include_router(backup_router)
app.include_router(release_update_router)
app.include_router(runtime_settings_router)
app.include_router(logs_router)
app.include_router(tables_router)
app.include_router(analytics_router)
app.include_router(export_router)
app.include_router(recipes_router)

# 静态文件（仪表盘 + 管理后台）
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

public_dir = os.path.join(os.path.dirname(__file__), "public")

spa_dir = os.path.join(os.path.dirname(__file__), "admin-web", "dist")
spa_index_path = os.path.join(spa_dir, "index.html")


def _spa_index():
    """返回 admin-web SPA 入口 index.html，由前端 vue-router 接管客户端路由。"""
    return FileResponse(spa_index_path)


# ---- admin-web SPA 页面路由（Phase 4.6：统一服务同一 SPA，登录/配置也走 SPA） ----
# 未登录访问由 HtmlAuthMiddleware 服务端重定向到 /login；/login 本身在中间件豁免列表内。
@app.get("/")
@app.get("/admin")
@app.get("/admin/")
@app.get("/login")
@app.get("/setup")
@app.get("/stations-speed")
@app.get("/sales-report")
@app.get("/prep-plan")
@app.get("/wecom-push")
@app.get("/recipe")
@app.get("/recipe/detail")
@app.get("/recipe/print")
@app.get("/recipe/manage")
@app.get("/recipe/qr")
@app.get("/logs")
async def spa_page():
    return _spa_index()

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
@app.get("/api/system/status")
async def get_system_status():
    """获取系统状态"""
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
        
        # 计算运行时间
        startup_time = getattr(app, 'startup_time', datetime.now(CHINA_TZ))
        uptime = int((datetime.now(CHINA_TZ) - startup_time).total_seconds())
        
        result = {
            "status": "running",
            "uptime": uptime,
            "version": settings.APP_VERSION,
            "database": db_stats,
            "memory": memory_stats,
            "last_update": datetime.now(CHINA_TZ)
        }
        return serialize_all(result)
        
    except Exception as e:
        logger.error(f"获取系统状态失败: {e}")
        raise HTTPException(status_code=500, detail="获取系统状态失败")

@app.get("/api/system/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "timestamp": datetime.now(CHINA_TZ)}


@app.get("/api/system/scraper-health")
async def get_public_scraper_health():
    """采集与对账健康（只读，供监控大屏）。"""
    from services.scraper_health import read_health, current_biz_date_str
    from services.reconcile_job import is_reconcile_running

    health = read_health()
    return {
        "success": True,
        "health": {
            "biz_date": health.get("biz_date") or current_biz_date_str(),
            "api_failures": health.get("api_failures", 0),
            "delivery_bills_pending": health.get("delivery_bills_pending"),
            "last_scrape_at": health.get("last_scrape_at"),
            "last_reconcile": health.get("last_reconcile"),
            "reconcile_running": is_reconcile_running(),
            "updated_at": health.get("updated_at"),
        },
    }


@app.get("/api/dashboard/summary")
async def get_dashboard_summary():
    """首页仪表盘聚合接口，减少前端轮询请求数量。"""
    try:
        if db_manager is None:
            raise HTTPException(status_code=500, detail="数据库未初始化")

        today_start = datetime.now(CHINA_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        orders_stats = await db_manager.orders.aggregate_orders_stats()
        hot_dishes = await db_manager.orders.aggregate_hot_dishes(limit_n=10)
        recent_orders = await db_manager.orders.get_orders(start_time=today_start, limit=30)
        station_stats = await db_manager.orders.aggregate_station_counts(today_start)
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
@app.get("/api/stations")
async def get_stations():
    """获取档口配置"""
    from config import KITCHEN_STATIONS
    return list(KITCHEN_STATIONS.values())

@app.get("/api/stations/{station_id}")
async def get_station_info(station_id: str):
    """获取档口信息"""
    from config import KITCHEN_STATIONS
    station = KITCHEN_STATIONS.get(station_id)
    if not station:
        raise HTTPException(status_code=404, detail="档口不存在")
    return station

# 餐厅爬虫控制API
@app.post("/api/scraper/start")
async def start_scraper():
    """启动餐厅数据爬取"""
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

@app.post("/api/scraper/stop")
async def stop_scraper():
    """停止餐厅数据爬取"""
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
    """获取爬虫状态"""
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


@app.get("/api/logs/recent")
async def get_recent_logs(
    limit: int = 200,
    after_id: int = 0,
    level: Optional[str] = None,
    logger_name: Optional[str] = None,
    q: Optional[str] = None,
):
    """获取近期日志，支持按日志 ID 增量拉取；可选 level/logger/关键词过滤。"""
    try:
        safe_limit = max(1, min(limit, 500))
        safe_after_id = max(0, after_id)
        if safe_after_id > 0:
            logs = in_memory_log_handler.after(safe_after_id, safe_limit * 3)
        else:
            logs = in_memory_log_handler.recent(safe_limit * 3)

        level_u = (level or "").strip().upper()
        logger_f = (logger_name or "").strip().lower()
        q_l = (q or "").strip().lower()
        if level_u or logger_f or q_l:
            filtered = []
            for item in logs:
                if level_u and (item.get("level") or "").upper() != level_u:
                    continue
                if logger_f and logger_f not in (item.get("logger") or "").lower():
                    continue
                if q_l and q_l not in (item.get("message") or "").lower():
                    continue
                filtered.append(item)
            logs = filtered[:safe_limit]
        else:
            logs = logs[:safe_limit]

        return {
            "success": True,
            "items": logs,
            "latest_id": in_memory_log_handler.latest_id,
            "count": len(logs),
        }
    except Exception as exc:
        logger.error("获取实时日志失败: %s", exc)
        raise HTTPException(status_code=500, detail="获取实时日志失败")

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

    failure_tracker = ScraperFailureTracker(alert_sender=_send_scraper_health_alert)

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
