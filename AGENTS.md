# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Restaurant order data collection and query system for **LuckIn**. Scrapes POS system data via Playwright, stores it in PostgreSQL (the only backend since ADR 0089), and exposes REST APIs. Also includes a sales report crawler, a Vue3 admin SPA (`admin-web/`), and a uni-app KDS kitchen display (`kds/`).

**Tech stack:** FastAPI + PostgreSQL (asyncpg) + Playwright + Pandas · Admin frontend: Vite + Vue3 + Pinia + vue-router · KDS: uni-app (H5 build)

---

## Commands

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Start the backend (uvicorn auto-reload only when DEBUG=true)
python3 scripts/start.py
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
  - `connection.py` — `_ConnectionMixin`: PostgreSQL connection lifecycle (connect/close). `connect()` rejects any backend other than `postgres`; no DDL runs at startup.
  - `table_db.py` — `TableView`: per-table view sharing the one `PgConnection`; no startup column migrations (`migrate_orders_kds_columns` retired with SQLite).
  - `ports.py` — `OrdersPort` / `DishStationsPort` / `ReportsPort`; access via `db.orders` / `db.dish_stations` / `db.reports`.
  - `adapters/` — `orders.py` / `dish_stations.py` / `reports.py`: the port adapters exposed by `DatabaseManager`.
  - `schema.py` — table-name lists only (`ALL_TABLES`, `RECIPE_TABLES`, `HYGIENE_TABLES`, `ADMIN_READ_ONLY_TABLES`). The SQLite DDL, `apply_recipe_schema()`, `apply_hygiene_schema()` and the `migrate_*_columns()` helpers are gone: schema lives in `migrations/pg/*.sql`.
  - `orders_repo.py`, `tables_repo.py`, `dish_stations_repo.py`, `semi_rules_repo.py`, `report_dishes_repo.py`, `wecom_repo.py`, `settings_repo.py` — per-domain repo mixins (`app_settings` lives in `settings_repo.py`).
  - `aggregation.py`, `reports.py`, `stats.py` — cross-table aggregation, sales-report/business analytics, and perf/health-check mixins.
  - `backend/` — dialect layer at the driver boundary: `dialect.py` (rowid/placeholder rewriting on the way to PostgreSQL), `pg.py` (PostgreSQL connection, `PgConnection`, `pg_dump`).
  - `utils.py` — `CHINA_TZ`, `ensure_beijing_datetime`, `to_sql_datetime`, `row_to_dict`. `errors.py` holds the shared error mapping — integrity conflicts go through `is_integrity_violation()`, which recognises both the asyncpg and the legacy `sqlite3` exception families. `order_notes.py` holds order-note parsing.
- Runs on `asyncpg` through `db_core/backend/pg.py` (it mimics the old aiosqlite surface so existing mixins keep working); all DB operations go through `DatabaseManager`, never a raw driver call in routes. `aiosqlite` is no longer a dependency: ADR 0089 removed the last legacy `.db` export/import paths, and the one-off `scripts/migrate_recipes_structured.py` was retired.
- `dish_stations` table: `dish_name TEXT UNIQUE` (not MongoDB ObjectId).
- Orders use `id INTEGER PRIMARY KEY` — editing goes through `rowid`. **When querying for display/editing, always use `SELECT rowid, * FROM orders`**; the dialect layer rewrites it to `SELECT id AS rowid, *` for PostgreSQL, keeping the alias that admin row-edit depends on.
- `CHINA_TZ = timezone(timedelta(hours=8))` is the standard timezone constant used everywhere.
- **迁移脚本**：`scripts/archive/migrate_sqlite_to_postgres.py`（唯一能读遗留 `data/app.db` 的代码，把老 SQLite 门店的数据搬进 PostgreSQL；先 `--dry-run`，跑法见 `migrations/pg/README.md`）。Day-to-day vs archived scripts: `scripts/README.md`. Repo layout: [ADR 0012](docs/adr/0012-repo-layout.md).

### API Routes

- `api/orders.py` — order query (filtered by station/table/time)
- `api/dishes.py` — dish aggregation (merged view, hot dishes, stats)
- `api/dish_stations.py` — dish→station mapping CRUD (`/api/dish-stations/`)
- `api/admin.py` — database admin SPA backend (`/api/admin/`), including:
  - CRUD for all tables (`/api/admin/tables/{table}/rows`)
  - Column management (`/api/admin/tables/{table}/columns`)
  - Sync unmapped dishes to orders (`/api/admin/sync-stations`)
  - Get unmapped dishes from orders (`/api/admin/unmapped-dishes`)
- The rest of the route modules live in `api/`: `auth`, `hygiene`, `analytics`, `prep_plan`, `recipes`, `release_update`, `db_migrations`, `backup`, `tables`, `wecom_push`, `logs`, `credentials`, `db_credentials`, `export_api`, `runtime_settings`, `security`, `report_dishes`, `semi_rules` — all registered with `app.include_router(...)` in `main.py`.

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
- **PWA:** Vite builds a root-scope `sw.js` plus Admin/Hygiene/Recipe manifests and icons. The worker precaches only frontend assets; `/api`, `/ws`, uploads, and protected images stay network-only. The browser checks for a waiting update on startup and the user applies it from a non-blocking prompt.
- **Production serving:** FastAPI (`main.py`) serves the SPA directly — its page routes (`/`, `/admin`, `/login`, `/setup`, `/stations-speed`, `/sales-report`, `/prep-plan`, `/wecom-push`, `/recipe*`, `/logs`, `/hygiene`, `/hygiene/login`, `/hygiene/register`, `/hygiene-roster`, `/hygiene-zones`, `/hygiene-daily`, `/hygiene-deep-clean`, `/hygiene-fix`, `/hygiene-boards`) return `admin-web/dist/index.html`, and client-side `vue-router` takes over routing. Built JS/CSS chunks are mounted at `/assets` from `admin-web/dist/assets`. Login (`/login`) and initial setup (`/setup`) are SPA routes too, not separate HTML files. **`main.py` itself has no catch-all fallback** — the reverse proxies add one (`deploy/nginx.conf` / `deploy/Caddyfile` end with `try_files … /index.html`) — so a route that exists only in `vue-router` (currently `/hygiene-data`) 404s when uvicorn is hit directly without that proxy rule.
- Station lookups no longer use a hardcoded JS constant — `admin-web/src/stores/stations.js` fetches `/api/stations` once and caches it in a Pinia store, avoiding drift from `config.py`'s `KITCHEN_STATIONS`.

### Station Definitions (`config.py`)

Stations are defined in `KITCHEN_STATIONS` dict. When adding a new station:
1. Add entry in `config.py` `KITCHEN_STATIONS`.
2. No frontend constant to update — `admin-web`'s stations store (`admin-web/src/stores/stations.js`) reads `/api/stations` at runtime.

### Realtime (`services/realtime/hub.py`, `/ws/realtime`)

Realtime updates use a **nudge + pull** model, not push-the-payload: the server only ever broadcasts a tiny `{"type": "nudge", "topic": "...", "scope": {...}}` message with no data, sequence number, or delta. Clients that receive a nudge re-fetch via the existing HTTP REST APIs.

- `RealtimeHub` (`services/realtime/hub.py`) tracks per-connection subscriptions (`{id, topics, filters}`) and dispatches `broadcast_nudge(topic, scope)` only to matching subscribers. Valid topics: `orders`, `tables`, `scraper`, `dashboard`, `logs`, `admin`, `hygiene`. **员工（`auth == "staff"`）只允许订 `hygiene`**（`allowed_topics()`）——nudge 不带数据，但订单/档口/日志的时序本身就是经营信息。
- `orders`/`tables` nudges also debounce-trigger a combined `dashboard` nudge (`DASHBOARD_DEBOUNCE_SECONDS = 0.3s`) so the (expensive) dashboard summary endpoint isn't hit on every single change.
- Single endpoint `@app.websocket("/ws/realtime")` in `main.py` handles both auth modes: **Cookie session** (Admin SPA, browser) or **`?token=<api_token>`** query param (KDS and other non-cookie clients). See `authenticate_ws()`.
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
- **测试强制跑 PostgreSQL 测试库**：`tests/conftest.py` 在 `import config` 之前把 `DATABASE_BACKEND` 钉死为 `postgres` 并把 DSN 指向专用测试库 `luyun_test`（进程 env 优先于 `.env`，本机 `.env` 指的是真库），会话开始时用 `migrations/pg/*.sql` 重建 schema，每个用例前 `TRUNCATE` 全部表；`DISABLE_BACKGROUND_TASKS=true` 关掉 lifespan 里的常驻后台循环。所以测试仍不会碰业务库，但也不再靠「临时目录里的一个 SQLite 文件」隔离。
- CI (`.github/workflows/`) runs three jobs: Python (`postgres:16` service + `LUYUN_TEST_DSN` + `pip install -r requirements.txt` + `pytest tests/ -v`), `admin-web` (`npm ci` + build/PWA artifact checks + Vitest), and `kds` (`npm ci` + Vitest).

## Deployment (`deploy/`)

Single-machine, single-instance, **single uvicorn worker** deployment. Storage is **PostgreSQL only** (ADR 0089): `DATABASE_BACKEND` defaults to `postgres`, any other value makes the app fail at startup instead of falling back, and the schema is applied from `migrations/pg/` — see `deploy/README.md` §10. Redis now carries cross-process realtime nudges: `REDIS_URL` (env `LUYUN_REDIS_URL` wins; empty string = off) publishes every nudge to the `luyun:nudge` pub/sub channel so other processes dispatch it to their own subscribers, and an unset or unreachable Redis degrades to in-process-only broadcast with a single log line (see `services/realtime/redis_bus.py`; decision and non-goals in ADR 0090) — never a failed startup, a request error, or a lost local nudge. **The single-worker constraint still holds**: the 7 resident background loops have no distributed leader election yet, so `--workers > 1` still duplicates collection/pushes. Docker may host the process with bind mounts but image pull is not delivery. Delivery follows **ADR 0011** (supersedes the earlier git-checkout update design): GitHub Release **发行包 (Release Bundle)** (app tree + prebuilt Admin/KDS + **版本清单**/checksums) + Admin「系统更新」（版本检测 · **更新环境自检** · 应用更新 · **数据库迁移**）→ 更新作业; shop machines stay Node-free (no Deploy Key / clone). `deploy/` contains:

- `luyun.service` — systemd unit running `uvicorn main:app --workers 1` (must stay single-worker: the 7 resident background loops have no leader election, and the in-memory log buffer + scraper failure counters are still per-process).
- `luyun-update.service` — systemd oneshot Update Job started by Admin Apply Update.
- `Dockerfile` / `docker-compose.yml` / `docker-entrypoint.sh` — Docker process-shell (bind-mount Release Bundle tree under parent volume; upgrades still via Admin「系统更新」). Helper: `scripts/docker_up.sh`.
- `Caddyfile` / `nginx.conf` — reverse proxy + TLS termination, forwarding `/api/*` and `/ws/*` to the backend and serving `admin-web/dist` directly at the proxy layer.
- `backup.sh` + `luyun-backup.service`/`luyun-backup.timer` — online backup on a systemd timer with retention policy. One code path: whole-database `pg_dump → app.pgdump` (no SQLite files involved), retention read from the PostgreSQL `app_settings` table.
- `enable_postgres.sh` — one-shot `sudo` script that migrates a **legacy SQLite install** to PostgreSQL end to end (install PG → create db/user → apply `migrations/pg/` schema → stop app → back up SQLite → migrate data → write `env.production` → restart → smoke). Idempotent; `--dry-run` previews. Needed as a root script because the Update Job deliberately runs as the unprivileged app user.
- `env.production.example` — production environment variable template (GitHub Releases PAT optional for the public repo; `DATABASE_BACKEND=postgres` / `POSTGRES_DSN`, the only supported backend).
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
- **Realtime payloads carry no data.** Don't expect fields beyond `topic`/`scope` on a `nudge` message — always re-fetch via HTTP after receiving one. Cross-table queries are just normal SQL against the one database, but the app is still single-worker only (see `deploy/README.md`) because the 7 resident background loops have no leader election and the log buffer/scraper state are still in-process.
- **Playwright lib 与浏览器 build 强绑定。** `requirements.txt` 钉死 `playwright==1.63.0`；升该版本时必须同时执行 `.venv/bin/python -m playwright install chromium`（更新作业的 `syncing_deps` 阶段与 Docker entrypoint 已内置，开发机要手动）。只升 lib 不升浏览器会报 `Executable doesn't exist at /ms-playwright/chromium_headless_shell-<rev>/...`，缺失的是浏览器而不是代码路径。
- **日志表 `logs` 在 PostgreSQL 同库，已没有 `data/logs.db`。** 写入走 `queue.Queue` 批量落库（`services/log_storage.py`，纯 PG 实现）。SQLite 时代的 WAL / `synchronous=NORMAL` / 启动 `quick_check` / 损坏隔离 `logs.db.corrupt.<stamp>` / `.forensics.txt` 随 SQLite 一起退役（ADR 0089）——PG 不产生页损坏，那套自愈逻辑没有了。**磁盘满仍然不会被当成损坏**：只丢当批日志并计入 `queue_dropped`，冷却期后再试。启动与运行期每 `LOG_MAINTENANCE_INTERVAL_SECONDS` 按保留天数清理过期日志（空间回收交给 PG autovacuum）。配套的两个 SQLite 配置项（`SQLITE_QUICK_CHECK_ON_START`、`LOG_CORRUPT_KEEP`）已随这套逻辑一起从 `config.py` 删除。
- **`GET /api/healthz`**（免鉴权、只读）返回 DB 状态与聚合磁盘水位，供探针使用；磁盘水位高**不**改状态码（重启容器腾不出空间）。进程内磁盘守护见 `services/disk_guard.py`，阈值由 `DISK_WARN_PCT` / `DISK_CRITICAL_PCT` 控制。
- **schema 变更不随重启生效，也不随更新作业生效。** 应用按设计**不在启动期改结构**（结构变更要可追溯，见 `_connect_postgres` 的 docstring），`db_core/schema.py` 现在只剩表名清单。所以新增或改动表/索引，就要出 `migrations/pg/000N_*.sql`（**只做加成性变更**：加列带默认值 / 加索引 / 建新表）并**提交**——发行包按 `git archive HEAD` 打包，没提交的脚本不会进包。门店在 Admin「系统更新」→「数据库迁移」应用（`/api/db-migrations`），版本检测会把待应用条数显示在版本状态卡上。`0001` 带 `luyun:bootstrap-only` 标记（含 `DROP TABLE`），永远不进待应用清单。详见 `migrations/pg/README.md`。
- **卫生端会话 id 只存哈希。** `hygiene_staff_sessions.session_id` 存的是 `sha256(cookie)`（`accounts.hash_session_id`），cookie 本身发原文——拿 cookie 原值直接查库永远查不到，必须走 `EmployeeAccounts.get_staff_session()`。存量明文行由 `EmployeeAccounts.prepare()` 在启动期（`main.py`）**原地哈希化**，员工不需要重登；这一项**不改 schema**，不需要新的 `migrations/pg/000N`。额外还有 `SESSION_IDLE_HOURS`（默认 336 = 14 天，0 = 关闭）的闲置上限，靠每次请求节流刷新 `last_seen_at` 判定。
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
