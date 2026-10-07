# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Restaurant order data collection and query system for **厨务管家**. Scrapes POS system data via Playwright, stores it in PostgreSQL (the only backend since ADR 0089), and exposes REST APIs. Also includes a sales report crawler, a Vue3 admin SPA (`admin-web/`), and a uni-app KDS kitchen display (`kds/`).

**Tech stack:** FastAPI + PostgreSQL (asyncpg) + Playwright + Pandas · Admin frontend: Vite + Vue3 + Pinia + vue-router · KDS: uni-app (H5 build)

---

## Commands

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Start the backend (uvicorn auto-reload only when DEBUG=true)
# 用仓库 `.venv` 的解释器：系统 `python3` 也能起（仍然支持），但三方库版本与
# 测试/生产差一个大版本（PERF-04），`scripts/start.py` 会就此打印解释器漂移告警。
.venv/bin/python scripts/start.py
# or
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Admin SPA — dev server (Vite :5173, proxies /api and /ws to :8000)
cd admin-web && npm run dev

# Admin SPA — production build (output: admin-web/dist, served by FastAPI)
cd admin-web && npm run build

# Admin SPA
open http://localhost:8000/admin/

# API docs
open http://localhost:8000/docs
```

---

## Architecture

### Startup & Lifecycle (`main.py`)

The app uses `lifespan` context manager to initialize: `db_manager`, `restaurant_scraper`, and `memory_manager` on startup, then clean them up on shutdown.

**Critical pattern — resolve the database per request, never at import time:**
```python
# WRONG — captures db_manager value at import time (broken on uvicorn reload)
db = get_db()  # from database.py

# CORRECT — routes depend on get_db (database.py). It resolves the runtime
# database first (services/app_runtime.get_runtime().db) and only falls back
# to `from main import db_manager` when no runtime is registered.
from database import get_db

@router.get("/tables/{name}/rows", db=Depends(get_db)):
async def get_rows(table_name: str, db): ...
```

### Database (`database.py` + `db_core/`)

**Single-database architecture, PostgreSQL only (ADR 0089)** — the 17 names in `db_core/schema.py`'s `ALL_TABLES` (16 business tables + `auth`, i.e. admin_user/sessions/api_tokens), the hygiene tables (`hygiene_*`), the recipe tables (`sop_*`) and the log table (`logs`) all live in **one PostgreSQL database**. Structure is no longer defined in Python: `db_core/schema.py` keeps only the table-name lists, and the DDL lives in `migrations/pg/*.sql` (`0001_initial_schema.sql` is the frozen bootstrap, later changes are additive `000N` increments) — nothing is created or altered at startup. Cross-table queries are plain SQL joins in the same connection. `DATABASE_BACKEND` defaults to `postgres`, and any other value makes `connect()` fail with migration guidance instead of falling back — see `deploy/README.md` §10 and `migrations/pg/`.

- `database.py` is now a thin (~130-line) facade: `DatabaseManager` is composed from mixins in `db_core/`, re-exporting the same public names (`DatabaseManager`, `get_db`, `CHINA_TZ`, `ALL_TABLES`, `ensure_beijing_datetime`) so callers are unaffected.
- `db_core/` module layout:
  - `database_connection.py` — `DatabaseConnection`: the **owner** of the business-DB connection — create / replace / close it, and answer "is it still usable?" (`connect` / `close` / `alive` / `table`). Holders (`TableView`, `RecipeStore`, the hygiene services) keep a reference to **this object**, and the object survives a reconnect in place (see `CONTEXT.md`「数据库连接 (Database Connection)」and `rebind()` below).
  - `connection.py` — `_ConnectionMixin`: the **facade** onto `DatabaseManager` — it holds one `DatabaseConnection`, exposes `is_connected` / `readable_tables` / connection stats, and creates the **global write lock** (`_write_lock`) inside `connect()` (the two services sharing it via owner would otherwise fall back to private locks and interleave implicit transactions). It no longer owns the connection lifecycle — that lives in `database_connection.py`. `connect()` still rejects any backend other than `postgres`; no DDL runs at startup.
  - `table_db.py` — `TableView`: per-table view sharing the one `PgConnection`; no startup column migrations (`migrate_orders_kds_columns` retired with SQLite).
  - `ports.py` — `OrdersPort` / `DishStationsPort` / `ReportsPort`; access via `db.orders` / `db.dish_stations` / `db.reports`.
  - `adapters/` — `orders.py` / `dish_stations.py` / `reports.py`: the port adapters exposed by `DatabaseManager`.
  - `schema.py` — table-name lists only (`ALL_TABLES`, `RECIPE_TABLES`, `HYGIENE_TABLES`, `ADMIN_READ_ONLY_TABLES`). The SQLite DDL, `apply_recipe_schema()`, `apply_hygiene_schema()` and the `migrate_*_columns()` helpers are gone: schema lives in `migrations/pg/*.sql`.
  - `orders_repo.py`, `tables_repo.py`, `dish_stations_repo.py`, `semi_rules_repo.py`, `report_dishes_repo.py`, `wecom_repo.py`, `settings_repo.py` — per-domain repo mixins (`app_settings` lives in `settings_repo.py`).
  - `aggregation.py`, `reports.py`, `stats.py` — cross-table aggregation, sales-report/business analytics, and perf/health-check mixins.
  - `backend/` — dialect layer at the driver boundary: `dialect.py` (rowid/placeholder rewriting on the way to PostgreSQL), `pg.py` (PostgreSQL connection, `PgConnection`, `pg_dump`). Two methods there are load-bearing for long-lived holders, because the object is handed out during wiring and must never become a zombie (**整库恢复后配方接口全 500** was exactly that bug): `rebind(dsn=None)` swaps in a fresh driver connection **in place** (same `PgConnection` object, same references held by `main.py` / `TableView`; uncommitted transactions are rolled back and dropped, table-shape caches kept), and `alive()` answers "is it still usable?" from the driver itself (`asyncpg.Connection.is_closed()`, covering both graceful close and abort — never `_raw is None`, which is only true during the await window of `_reconnect_raw()` and would make a concurrent `connect()` build a second object).
  - `utils.py` — `CHINA_TZ`, `ensure_beijing_datetime`, `to_sql_datetime`, `row_to_dict`. `errors.py` holds the shared error mapping — integrity conflicts go through `is_integrity_violation()`, which recognises both the asyncpg and the legacy `sqlite3` exception families. `order_notes.py` holds order-note parsing.
- Runs on `asyncpg` through `db_core/backend/pg.py` (it mimics the old aiosqlite surface so existing mixins keep working); all DB operations go through `DatabaseManager`, never a raw driver call in routes. `aiosqlite` is no longer a dependency: ADR 0089 removed the last legacy `.db` export/import paths, and the one-off `scripts/migrate_recipes_structured.py` was retired.
- `dish_stations` table: `dish_name TEXT UNIQUE` (not MongoDB ObjectId).
- Orders use `id INTEGER PRIMARY KEY` — editing goes through `rowid`. **When querying for display/editing, always use `SELECT rowid, * FROM orders`**; the dialect layer rewrites it to `SELECT id AS rowid, *` for PostgreSQL, keeping the alias that admin row-edit depends on.
- `CHINA_TZ = timezone(timedelta(hours=8))` is the standard timezone constant used everywhere.
- **迁移脚本**：`scripts/archive/migrate_sqlite_to_postgres.py`（唯一**写**入 PostgreSQL 的迁移工具，把老 SQLite 门店的数据搬进 PostgreSQL；先 `--dry-run`，跑法见 `migrations/pg/README.md`）。另有两处也会碰到遗留 `data/app.db`，都不是迁移路径：`scripts/archive/backtest_prep_forecast.py`（只读回测）与 `services/backup_import_staging.py`（读写上传包里的同名成员；`api/backup.py` 对 `.db` 上传包固定 400 拒绝）。Day-to-day vs archived scripts: `scripts/README.md`. Repo layout: [ADR 0012](docs/adr/0012-repo-layout.md).

### API Routes

- `api/orders.py` — order query (filtered by station/table/time)
- `api/dishes.py` — dish aggregation (merged view, hot dishes, stats)
- `api/dish_stations.py` — dish→station mapping CRUD (`/api/dish-stations/`)
- `api/admin.py` — database admin SPA backend (`/api/admin/`), including:
  - CRUD for all tables (`/api/admin/tables/{table}/rows`)
  - Column management (`/api/admin/tables/{table}/columns`)
  - Sync unmapped dishes to orders (`/api/admin/sync-stations`)
  - Get unmapped dishes from orders (`/api/admin/unmapped-dishes`)
- The rest of the route modules live in `api/`: `auth`, `hygiene`, `scheduling`, `analytics`, `prep_plan`, `recipes`, `release_update`, `db_migrations`, `backup`, `tables`, `wecom_push`, `logs`, `credentials`, `db_credentials`, `export_api`, `runtime_settings`, `security`, `report_dishes`, `semi_rules` — all registered with `app.include_router(...)` in `main.py`. (`api/scheduling.py` 与 `api/hygiene.py` 在**代码上互不 import**：排班不读卫生、卫生也不读排班，两边只共用公共层的员工身份与工作区名单，见 `CONTEXT.md` 的「固定工作区」。**界面上这两个模块从 2026-10-04 起是同一个子系统「工作台」**（排班 + 卫生合并，页内分人事 / 现场 / 后勤三组），页面同住 `/workbench/*` —— 旧的 `/scheduling*` 与 `/hygiene/*` 前缀已删除、不留别名。)

### Scraper (`scraper/restaurant_scraper.py` + modules)

`RestaurantScraper` is the composition root (`create_restaurant_scraper()`): owns one-cycle orchestration (`run_cycle`) and status DTO; delegates to —

- `pos_session.py` — browser/session, login, credentials, business-hours gate, table HTTP
- `pos_http_client.py` — form POST / recovery / failure counts
- `table_change_detector.py` — table order monitoring / change detection
- `delivery_bill_tracker.py` — settled bills / delivery collect + cancel sweep
- `state_store.py` — table/delivery state file persistence
- `order_line_builder.py` — shared inbound order-line shape

`run_restaurant_scraper()` in `main.py` owns the while-loop (idle/pause sleeps) and calls `scraper.run_cycle(db)`.

### Admin SPA (`admin-web/`)

The admin/management UI is a Vue3 SPA (Vite + Vue3 + Pinia + vue-router) in `admin-web/`, not static HTML pages. Old `public/*.html` pages have been removed; only `public/{kds,vendor,recipe.css,hygiene-admin.css}` remain (KDS build output, vendored static assets, and the recipe / hygiene-admin stylesheets).

- **Dev:** `cd admin-web && npm run dev` — Vite dev server on `:5173`, proxies `/api` and `/ws` to the backend (`:8000` by default, override via `LUYUN_API_PROXY`).
- **Build:** `cd admin-web && npm run build` → `admin-web/dist`.
- **PWA:** Vite builds two manifests and two scoped service workers — `admin.webmanifest` + root-scope `sw.js`, and `workbench.webmanifest` + `/workbench/sw.js` (`scope: /workbench`, served with `Service-Worker-Allowed: /workbench`). Which app a page belongs to is decided by path in `src/utils/pwaManifest.js` (workbench prefix → workbench, everything else → admin). The workers precache only frontend assets; `/api`, `/ws`, uploads, and protected images stay network-only. The browser checks for a waiting update on startup and the user applies it from a non-blocking prompt.
- **Production serving:** FastAPI (`main.py`) serves the SPA directly — its page routes (`/`, `/admin`, `/login`, `/register`, `/settings`, `/sales-report`, `/wecom-push`, `/logs`, `/workbench`（今天首页）, `/workbench/hr/*`（人事：月历 / 待办 / 班次表 / 花名册）, `/workbench/floor/*`（现场：卫生七页）, `/workbench/kitchen/*`（后勤：配方五页 + 备货计划）, `/workbench/me/*`（我的：员工端三页）, `/workbench/forbidden`）return `admin-web/dist/index.html`, and client-side `vue-router` takes over routing. Built JS/CSS chunks are mounted at `/assets` from `admin-web/dist/assets`. Login (`/login`), staff registration (`/register`) and the system settings page (`/settings`, renamed from `/setup`) are SPA routes too, not separate HTML files — initial setup (creating the super admin) happens on `/login`, never on `/settings`. **The staff entry is `/login`** (the staff tab there; it lands on `/workbench/me/today`) — the old bookmark `/staff/today` is dead since the employee pages moved into `/workbench/me/*`, so send staff a fresh `/login` link. **The list must equal the registered routes in both directions**: `SPA_PAGE_ROUTES` mirrors the single source `admin-web/src/router/pageRoutes.json` (plus its two server-side aliases `/index.html`、`/admin/`) and `tests/test_spa_page_routes.py` fails if vue-router has a page the backend doesn't (hard navigation 404s) or the backend has a page vue-router doesn't (white screen — the old dashboard 速度页 route was exactly that and has been deleted; its content lives in the Dashboard `StationSpeedChart`). **`main.py` itself has no catch-all fallback**, and the reverse proxies' SPA allow-lists (`deploy/nginx.conf` / `deploy/Caddyfile`) cover only the `admin|sales-report|logs|wecom-push|workbench` prefixes: `/workbench*` was added to that list in the cleanup ticket, so workbench pages no longer live off the "everything else is proxied to uvicorn" fallback, and the dead `/prep-plan` / `/recipe*` prefixes were dropped from it (kept there they would hand out a 200 empty shell instead of the agreed natural 404). Everything else — including `/login`, `/register`, `/settings` — is proxied to uvicorn, where pages are registered one by one; so every page needs its own entry in `main.py`'s `SPA_PAGE_ROUTES`, and a route that exists only in `vue-router` 404s when uvicorn is hit directly.
- Station lookups no longer use a hardcoded JS constant — `admin-web/src/stores/stations.js` fetches `/api/stations` once and caches it in a Pinia store, avoiding drift from `config.py`'s `KITCHEN_STATIONS`.

### Station Definitions (`config.py`)

Stations are defined in `KITCHEN_STATIONS` dict. When adding a new station:
1. Add entry in `config.py` `KITCHEN_STATIONS`.
2. No frontend constant to update — `admin-web`'s stations store (`admin-web/src/stores/stations.js`) reads `/api/stations` at runtime.

### Realtime (`services/realtime/hub.py`, `/ws/realtime`)

Realtime updates use a **nudge + pull** model, not push-the-payload: the server only ever broadcasts a tiny `{"type": "nudge", "topic": "...", "scope": {...}}` message with no data, sequence number, or delta. Clients that receive a nudge re-fetch via the existing HTTP REST APIs.

- `RealtimeHub` (`services/realtime/hub.py`) tracks per-connection subscriptions (`{id, topics, filters}`) and dispatches `broadcast_nudge(topic, scope)` only to matching subscribers. Valid topics: `orders`, `tables`, `scraper`, `dashboard`, `logs`, `admin`, `hygiene`, `scheduling`. **员工（`auth == "staff"`）只允许订 `hygiene` 与 `scheduling`**（`allowed_topics()`）——nudge 不带数据，但订单/档口/日志的时序本身就是经营信息。**光限主题还不够**：`_staff_owns_scope()` 会把 scope 里带了别人 `employee_id` 的 nudge 拦下来（身份由 `api/security.py` 的 `identify_ws` 给出、经 `register(..., employee_id=…)` 记在连接上；身份为 `None` 时 fail-closed），客户端订阅时传的 `filters` 也一律被换成他自己的 id —— 没有这一层，员工虽然订不到 orders，却能从 hygiene / scheduling 的 scope 里读到同事的动作时序（谁什么时候换了区、被驳了）。**排班的广播统一走 `api/scheduling.py` 的 `_scheduling_nudge(reason, *employee_ids)`**：一人一条、scope 带他自己的 id，于是员工只收得到与自己有关的那条，店长那侧不受限。
- `orders`/`tables` nudges also debounce-trigger a combined `dashboard` nudge (`DASHBOARD_DEBOUNCE_SECONDS = 0.3s`) so the (expensive) dashboard summary endpoint isn't hit on every single change.
- Single endpoint `@app.websocket("/ws/realtime")` in `main.py` handles both auth modes: **Cookie session** (Admin SPA, browser) or **`?token=<api_token>`** query param (KDS and other non-cookie clients). See `authenticate_ws()`. **员工端页面的连接带 `?identity=staff` 显式声明身份**（管理端页面不带，`App.vue` 按路由 `meta.staffAuth` 给声明；两套 cookie 的 `path` 都是 `/`，同浏览器可同时持有），于是 `identify_ws` 的优先级是**显式声明 > 管理端 cookie > `?token` > 员工 cookie**。声明**只选凭据、不给身份**（fail-closed）：声明 `staff` 就只用员工会话 cookie 验证，没有或无效一律拒绝连接，不回落到管理端 cookie / token；声明 `admin` 同理。不带声明时优先级逐字不变，KDS 的 `?token` 链路不受影响。
- There is no delta/seq/snapshot-cache protocol — clients are expected to be resilient to missed nudges (see KDS's 60s reconciliation poll below).

### KDS (`kds/`)

The kitchen display is a uni-app project (`kds/`, H5 build), no longer HTTP-polling based.

- `kds/utils/realtime.js` — `RealtimeConnection`: wraps `uni.connectSocket` to `/ws/realtime?token=...`, auto-reconnects on a fixed 3s interval, sends a 30s heartbeat ping, and replays pending subscriptions after reconnect.
- `kds/stores/realtime.js` — Pinia store (`useRealtimeStore`) built on top of `RealtimeConnection`: pages register per-topic handlers via `on(topic, handler)`; also runs a **60s low-frequency reconciliation poll** that re-triggers the `orders` handlers regardless of connection state, as a safety net against missed nudges or a dead connection.
- `kds/pages/kitchen/kitchen.vue` shows a prominent disconnect banner (断连告警，含提示音/振动) when the WS connection drops or is reconnecting, and queues print jobs via `kds/utils/printQueue.js` (`enqueuePrintTicket`/`retryAllFailedJobs`) with serialized processing + failure retry + a manual "retry failed" button.
- Build: `scripts/build_kds.sh` builds the uni-app H5 bundle and deploys it into `public/kds/`, which FastAPI mounts at `/kds` (`StaticFiles(directory=..., html=True)`).
- PWA: a separate scoped `/kds/sw.js` and `manifest.webmanifest` are generated from a build fingerprint. Startup checks a waiting update, and applying it confirms first when persisted print jobs remain.

---

## Testing & CI

- Test suite lives in `tests/` and mixes `unittest`-style and `pytest`-style tests; run with `pytest tests/` (collects both styles). Plain `python -m unittest discover -s tests` no longer collects the full suite.
- **测试强制跑 PostgreSQL 测试库**：`tests/conftest.py` 在 `import config` 之前把 `DATABASE_BACKEND` 钉死为 `postgres` 并把 DSN 指向专用测试库（进程 env 优先于 `.env`，本机 `.env` 指的是真库）：设了 `LUYUN_TEST_DSN` 就用它（库名须匹配 `luyun_test`、`<前缀>_luyun_test`、`luyun_test_<后缀>` 三种形式之一），**没设就按 PID 派生 `luyun_test_<pid>`**（ticket 19 / MERGE-01）——否则两个并发会话会共用同一个默认库互相 `DROP SCHEMA`/清库，表现为假红；多会话/多 agent 并跑时仍应显式各给一个后缀，会话开始会打印实际库名；随后用 `migrations/pg/*.sql` 重建 schema 并全清一次、把「清完的样子」记成**干净基线**，之后每个用例开始前只把**偏离基线的部分**清回去（`TRUNCATE ... RESTART IDENTITY CASCADE`；序列被推进过则 `ALTER SEQUENCE ... RESTART`）——库本来就干净时整段跳过，结尾打印「N 个跳过 / M 个清理过」的台账。判据只用确定性查询（表里有没有行、序列有没有被调用过），**不要**改用 `pg_stat_user_tables` 的写入计数：那套累计统计受最长 1 秒的上报节流，实测「长连接写入后立刻从另一个连接查」20 次全部看不到增长，漏判一次就是用例间污染；`DISABLE_BACKGROUND_TASKS=true` 关掉 lifespan 里的常驻后台循环。所以测试仍不会碰业务库，但也不再靠「临时目录里的一个 SQLite 文件」隔离。
- **想跑快就并行**：`pytest tests/ -n 4 --dist loadfile`（本机全量约 550s → 约 2 分钟；不写 `-n` 就是原来的串行）。xdist 的每个 worker 都是独立的 pytest 进程，各自按 PID 派生一个库（`luyun_test_<pid>`），隔离契约不变——**刻意不**用 `gw0` 这类固定的 worker 名做库名，否则同机上两个并行会话会双双落进 `luyun_test_gw0` 互相清空；`--dist loadfile` 让同一个文件的用例留在同一个 worker，模块级 fixture 与用例顺序的语义不变（默认的 `load` 会把同文件用例拆到不同 worker）。**并行时不要设 `LUYUN_TEST_DSN`**——所有 worker 会共用一个库并互相清空，conftest 在 controller 就拒跑（退出码 3）；xdist 的 controller 不建库、不建 schema，准备库是各 worker 自己的事。
- **禁止在导入期 import `config` / `db_core`（ticket 27 硬规矩）**：pytest 插件、临时验证脚本、`sitecustomize`/`usercustomize` 这类解释器启动期注入，都在 `tests/conftest.py` **之前**执行——导入期 `import config`（或 import 连带 config 的模块，如 `db_core.reports`）会让 pydantic-settings 当场把 `.env` 里的**生产库**读进 `settings.POSTGRES_DSN`，此后再设 `POSTGRES_DSN` 环境变量对它无效：conftest 照常在测试库建 schema，被测应用却把夹具数据写进真库（2026-09-25 事故：生产 `orders` +325 行、`public.tables` 被覆盖 3 行）。要改行为请在 `pytest_configure`/fixture 内**延迟 import**，或直接改实例/`monkeypatch`。护栏：`tests/conftest.py` 的 `_guard_effective_dsn()`（`pytest_configure` 第一条动作）同时看环境变量 `POSTGRES_DSN` 与已导入的 `config.settings.POSTGRES_DSN`，库名不匹配 `TEST_DB_NAME_RE` 即**拒跑**——`pytest.exit`，退出码 **3**，消息点名实际库名与"config 已在 conftest 之前被导入"的指纹；会话开始还会用应用实际 DSN 真连一次 `SELECT current_database()` 自证（不是本会话的测试库同样退出码 3）。用例见 `tests/test_test_db_guard.py`。
- CI (`.github/workflows/`) runs three jobs: Python (`postgres:16` service + `redis-server` via apt — the app's Bootstrap preflight hard-fails without Redis + `LUYUN_TEST_DSN` + `pip install -r requirements.txt` + `pytest tests/ -v`), `admin-web` (`npm ci` + build/PWA artifact checks + Vitest), and `kds` (`npm ci` + Vitest).

## Deployment (`deploy/`)

Single-machine, single-instance, **single uvicorn worker** deployment. **Two external services are mandatory**: PostgreSQL and Redis. Storage is **PostgreSQL only** (ADR 0089): `DATABASE_BACKEND` defaults to `postgres`, any other value makes the app fail at startup instead of falling back, and the schema is applied from `migrations/pg/` — see `deploy/README.md` §10. Redis carries cross-process realtime nudges and is a **required component** (ADR 0090): `REDIS_URL` (env `LUYUN_REDIS_URL` wins) publishes every nudge to the `luyun:nudge` pub/sub channel so other processes dispatch it to their own subscribers; an **unset** `REDIS_URL` makes startup fail with install guidance (same hard cut as a non-postgres backend, and Update Preflight blocks Apply Update), while an **unreachable** Redis only logs and reconnects with backoff — never a failed startup, a request error, or a lost local nudge. **The single-worker constraint still holds**: 7 resident business loops (hygiene overdue/variant/maintenance, WeCom push, restaurant scraper, reconcile scheduler, unmapped-dish watchdog) plus 5 auxiliary resident tasks (memory monitor, memory cleanup, disk guard, realtime Redis subscription, log-storage consumer) have no distributed leader election, so `--workers > 1` still duplicates collection/pushes. Docker may host the process with bind mounts but image pull is not delivery. Delivery follows **ADR 0011** (supersedes the earlier git-checkout update design): GitHub Release **发行包 (Release Bundle)** (app tree + prebuilt Admin/KDS + **版本清单**/checksums) + Admin「系统更新」（版本检测 · **更新环境自检** · 应用更新 · **数据库迁移**）→ 更新作业; shop machines stay Node-free (no Deploy Key / clone). `deploy/` contains:

- `luyun.service` — systemd unit running `uvicorn main:app --workers 1` (must stay single-worker: the 7 resident business loops have no leader election, and the in-memory log buffer + scraper failure counters are still per-process).
- `luyun-update.service` — systemd oneshot Update Job started by Admin Apply Update.
- `Dockerfile` / `docker-compose.yml` / `docker-entrypoint.sh` — Docker process-shell (bind-mount Release Bundle tree under parent volume; upgrades still via Admin「系统更新」). Helper: `scripts/docker_up.sh`.
- `Caddyfile` / `nginx.conf` — reverse proxy + TLS termination, forwarding `/api/*` and `/ws/*` to the backend and serving `admin-web/dist` directly at the proxy layer.
- `backup.sh` + `luyun-backup.service`/`luyun-backup.timer` — online backup on a systemd timer with retention policy. One code path: whole-database `pg_dump → app.pgdump` (no SQLite files involved), retention read from the PostgreSQL `app_settings` table.
- `enable_postgres.sh` — one-shot `sudo` script that migrates a **legacy SQLite install** to PostgreSQL end to end (install PG **and Redis** → create db/user → apply `migrations/pg/` schema → stop app → back up SQLite → migrate data → write `env.production`, including `REDIS_URL` → restart → smoke). Idempotent; `--dry-run` previews; Redis failures only warn (the script's job is the database move). Needed as a root script because the Update Job deliberately runs as the unprivileged app user.
- `env.production.example` — production environment variable template (GitHub Releases PAT optional for the public repo; `DATABASE_BACKEND=postgres` / `POSTGRES_DSN` for the only supported backend, plus `REDIS_URL`, a required component).
- `deploy/README.md` — Bootstrap Install, Docker Compose, upgrade via Version Check / Update Preflight / Apply Update, reverse proxy, backup. Publish: `scripts/publish_release.sh`. Operator flow: `docs/RELEASE_AND_DEPLOY.md`; bundle contract: `docs/release-asset-layout.md`.

---

## Important Gotchas

- **uvicorn `--reload` resets globals.** Every API route must use `db=Depends(get_db)` so the database is resolved fresh per request — never captured at module load time.
- **`SELECT rowid, *`** is still the convention for display/edit queries. PostgreSQL has no `rowid` column: the dialect layer rewrites it to that table's row-identity column **and keeps the alias** (`SELECT id AS rowid, *`) — without the alias the `id` column comes back twice and the duplicate keys break `JSON.stringify()`. Always filter/handle the alias in the API layer — see `db_core/backend/dialect.py`.
- **CORS:** `allow_credentials=True` with `allow_origins=["*"]` is incompatible. Use `allow_credentials=False`. Still true — `main.py` sets `allow_origins=["*"]` + `allow_credentials=False`.
- **Batch station sync (`sync-stations`):** Updates orders table in batches of 200 rows, commits after each batch. The `updated` count reflects matched dishes; `skipped` are dishes with no mapping entry.
- **dish_stations primary key:** Uses `dish_name TEXT UNIQUE`, not an auto-increment ID. The API endpoint is `/api/dish-stations/{dish_name}` (path param, not ID).
- **FastAPI path priority:** FastAPI matches routes in declaration order, so declare the more specific admin table path first — `/api/admin/tables/{table_name}/rows/{row_id}` must not be shadowed by a broader `/api/admin/tables/{table_name}/…` route (see `api/admin.py`).
- **All tables live in one PostgreSQL database.** No per-table `.db` files, no `ATTACH DATABASE`, and no `data/logs.db`: `logs` is a regular table in the same database (created by `migrations/pg/0004_logs.sql`).
- **Admin UI is not static HTML anymore.** Don't add pages under `public/`; add a Vue route/view under `admin-web/src/` and rebuild (`npm run build`) so `admin-web/dist` picks it up. `public/` now only holds the KDS build output, vendored JS/CSS, `recipe.css`, and `hygiene-admin.css`.
- **Realtime payloads carry no data.** Don't expect fields beyond `topic`/`scope` on a `nudge` message — always re-fetch via HTTP after receiving one. Cross-table queries are just normal SQL against the one database, but the app is still single-worker only (see `deploy/README.md`) because the 7 resident business loops have no leader election and the log buffer/scraper state are still in-process.
- **Playwright lib 与浏览器 build 强绑定。** `requirements.txt` 钉死 `playwright==1.63.0`；升该版本时必须同时执行 `.venv/bin/python -m playwright install chromium`（更新作业的 `syncing_deps` 阶段与 Docker entrypoint 已内置，开发机要手动）。只升 lib 不升浏览器会报 `Executable doesn't exist at /ms-playwright/chromium_headless_shell-<rev>/...`，缺失的是浏览器而不是代码路径。
- **日志表 `logs` 在 PostgreSQL 同库，已没有 `data/logs.db`。** 写入走 `queue.Queue` 批量落库（`services/log_storage.py`，纯 PG 实现）。SQLite 时代的 WAL / `synchronous=NORMAL` / 启动 `quick_check` / 损坏隔离 `logs.db.corrupt.<stamp>` / `.forensics.txt` 随 SQLite 一起退役（ADR 0089）——PG 不产生页损坏，那套自愈逻辑没有了。**磁盘满仍然不会被当成损坏**：只丢当批日志并计入 `queue_dropped`，冷却期后再试。启动与运行期每 `LOG_MAINTENANCE_INTERVAL_SECONDS` 按保留天数清理过期日志（空间回收交给 PG autovacuum）。配套的两个 SQLite 配置项（`SQLITE_QUICK_CHECK_ON_START`、`LOG_CORRUPT_KEEP`）已随这套逻辑一起从 `config.py` 删除。
- **`GET /api/healthz`**（免鉴权、只读）返回 DB 状态与聚合磁盘水位，供探针使用；磁盘水位高**不**改状态码（重启容器腾不出空间）。进程内磁盘守护见 `services/disk_guard.py`，阈值由 `DISK_WARN_PCT` / `DISK_CRITICAL_PCT` 控制。
- **schema 变更不随重启生效，也不随更新作业生效。** 应用按设计**不在启动期改结构**（结构变更要可追溯，见 `_connect_postgres` 的 docstring），`db_core/schema.py` 现在只剩表名清单。所以新增或改动表/索引，就要出 `migrations/pg/000N_*.sql`（**只做加成性变更**：加列带默认值 / 加索引 / 建新表）并**提交**——发行包按 `git archive HEAD` 打包，没提交的脚本不会进包。门店在 Admin「系统更新」→「数据库迁移」应用（`/api/db-migrations`），版本检测会把待应用条数显示在版本状态卡上。`0001` 带 `luyun:bootstrap-only` 标记（含 `DROP TABLE`），永远不进待应用清单。详见 `migrations/pg/README.md`。
- **卫生端会话 id 只存哈希。** `hygiene_staff_sessions.session_id` 存的是 `sha256(cookie)`（`accounts.hash_session_id`），cookie 本身发原文——拿 cookie 原值直接查库永远查不到，必须走 `EmployeeAccounts.get_staff_session()`。**身份代码在公共层 `services/identity/accounts.py`**（员工花名册、登录、会话 cookie；排班和卫生共用），工作区名单的只读入口也在公共层（`services/identity/zones.py` 的 `ZoneDirectory`，表和页面都还是卫生的，排班只读它）；`services/hygiene/accounts.py` 的 `HygieneEmployeeAccounts` 继承它、只加上卫生的当日分工（班次 + 工作区），并把老名字 `EmployeeAccounts` 继续转发出来，所以 26 个既有导入方不用改。库表仍是 `hygiene_employees` / `hygiene_staff_sessions`，这次只搬代码不搬表。存量明文行由 `EmployeeAccounts.prepare()` 在启动期（`main.py`）**原地哈希化**，员工不需要重登；这一项**不改 schema**，不需要新的 `migrations/pg/000N`。额外还有 `SESSION_IDLE_HOURS`（默认 336 = 14 天，0 = 关闭）的闲置上限，靠每次请求节流刷新 `last_seen_at` 判定。
- **写请求有跨站拦截。** `main.py` 的 `csrf_origin_guard` 中间件：带会话 cookie、且浏览器报 `Sec-Fetch-Site: cross-site` 的 POST/PUT/PATCH/DELETE 直接 403。判定**优先看 `Sec-Fetch-Site`**（不受反向代理改写 Host 影响），缺失才退回比较 `Origin` 与 `Host`，两者都缺放行。带 `Authorization` / `X-Admin-Token` 的调用和无 cookie 的调用不受影响，所以 TestClient 与 KDS 的 `?token=` 链路照常。
- **`tests/conftest.py` 只保护 `pytest`。** `python -c`、REPL、临时验证脚本、没写隔离的 `scripts/` 脚本都会按 `.env` 的 `POSTGRES_DSN` 直接连库——本机 `.env` 指的是真库 `luyun`（审查期间真发生过一次误写）。做任何验证前先把 DSN 指到专用测试库（`POSTGRES_DSN=postgresql://localhost:5432/luyun_test`，**必须在 `from config import settings` 之前**，env 优先级高于 `.env`；库不存在就先跑一次 `pytest`，conftest 会建），测试内查库可参照 `tests/pg_probe.py`。要连生产库时只读、只 `SELECT`，并先打印 `settings.POSTGRES_DSN` 确认。

---

## Agent skills

### Issue tracker

Issues and specs live as local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Uses the five canonical triage roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Repo layout

Where docs, scripts, and top-level scatter belong: [ADR 0012](docs/adr/0012-repo-layout.md). `CLAUDE.md` is a short pointer to this file.
