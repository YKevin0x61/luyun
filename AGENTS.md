# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Restaurant order data collection and query system for **LuckIn**. Scrapes POS system data via Playwright, stores in SQLite, and exposes REST APIs. Also includes a sales report crawler, a Vue3 admin SPA (`admin-web/`), and a uni-app KDS kitchen display (`kds/`).

**Tech stack:** FastAPI + SQLite (aiosqlite, single-file WAL) + Playwright + Pandas · Admin frontend: Vite + Vue3 + Pinia + vue-router · KDS: uni-app (H5 build)

---

## Commands

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Start the backend (auto-reload enabled)
python scripts/start.py
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

**Critical pattern — global `db_manager` with dependency injection:**
```python
# WRONG — captures db_manager value at import time (broken on uvicorn reload)
db = get_db()  # from database.py

# CORRECT — resolves db_manager at request time via Depends()
def _get_db():
    from main import db_manager
    if db_manager is None:
        raise HTTPException(500, "数据库未初始化")
    return db_manager

# All admin/api routes use this pattern:
@router.get("/tables/{name}/rows", db=Depends(_get_db)):
async def get_rows(table_name: str, db): ...
```

### Database (`database.py` + `db_core/`)

**Single-database architecture** — all business tables (17 tables) plus `auth` (admin_user/sessions/api_tokens) live in one `data/app.db` file, opened in WAL mode (`PRAGMA journal_mode=WAL`). Only `data/logs.db` stays separate (high write volume, not merged in). Cross-table queries are plain SQL joins in the same connection — no `ATTACH DATABASE` needed anymore.

- `database.py` is now a thin (~75-line) facade: `DatabaseManager` is composed from mixins in `db_core/`, re-exporting the same public names (`DatabaseManager`, `get_db`, `CHINA_TZ`, `ALL_TABLES`, `ensure_beijing_datetime`) so callers are unaffected.
- `db_core/` module layout:
  - `connection.py` — `_ConnectionMixin`: single `app.db` connection lifecycle (connect/close), WAL setup, backup export.
  - `table_db.py` — `TableView`: per-table view sharing the one connection; `migrate_orders_kds_columns()` backfills KDS columns.
  - `ports.py` — `OrdersPort` / `DishStationsPort` / `ReportsPort`; access via `db.orders` / `db.dish_stations` / `db.reports`.
  - `schema.py` — `_TABLE_SCHEMAS` / `_INDEX_DEFINITIONS` / `ALL_TABLES` (CREATE TABLE + index DDL for every table, including `auth`).
  - `orders_repo.py`, `tables_repo.py`, `dish_stations_repo.py`, `semi_rules_repo.py`, `report_dishes_repo.py`, `wecom_repo.py` — per-domain repo mixins.
  - `aggregation.py`, `reports.py`, `stats.py` — cross-table aggregation, sales-report/business analytics, and perf/health-check mixins.
  - `utils.py` — `CHINA_TZ`, `ensure_beijing_datetime`, `row_to_dict`, SQLite pragma constants.
- Uses `aiosqlite` for async SQLite access; all DB operations go through `DatabaseManager`, never raw `aiosqlite` in routes.
- `dish_stations` table: `dish_name TEXT UNIQUE` (not MongoDB ObjectId).
- Orders use `id INTEGER PRIMARY KEY AUTOINCREMENT` — editing goes through `rowid`. **When querying for display/editing, always use `SELECT rowid, * FROM orders`** — this avoids the `id`/`rowid` column duplication issue that caused duplicate keys in JSON.
- `CHINA_TZ = timezone(timedelta(hours=8))` is the standard timezone constant used everywhere.
- **迁移脚本**：`scripts/archive/consolidate_dbs.py`（幂等，把遗留的分库 `.db` 文件合并进单一 `app.db`，自动备份；回滚步骤见同目录 `consolidate_dbs.rollback.md`）。旧分库文件在迁移完成并验证后可删除。 Day-to-day vs archived scripts: `scripts/README.md`. Repo layout: [ADR 0012](docs/adr/0012-repo-layout.md).

### API Routes

- `api/orders.py` — order query (filtered by station/table/time)
- `api/dishes.py` — dish aggregation (merged view, hot dishes, stats)
- `api/dish_stations.py` — dish→station mapping CRUD (`/api/dish-stations/`)
- `api/admin.py` — database admin SPA backend (`/api/admin/`), including:
  - CRUD for all tables (`/api/admin/tables/{table}/rows`)
  - Column management (`/api/admin/tables/{table}/columns`)
  - Sync unmapped dishes to orders (`/api/admin/sync-stations`)
  - Get unmapped dishes from orders (`/api/admin/unmapped-dishes`)

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

The admin/management UI is a Vue3 SPA (Vite + Vue3 + Pinia + vue-router) in `admin-web/`, not static HTML pages. Old `public/*.html` pages have been removed; only `public/{kds,vendor,recipe.css}` remain (KDS build output, vendored static assets, and the recipe page stylesheet).

- **Dev:** `cd admin-web && npm run dev` — Vite dev server on `:5173`, proxies `/api` and `/ws` to the backend (`:8000` by default, override via `LUYUN_API_PROXY`).
- **Build:** `cd admin-web && npm run build` → `admin-web/dist`.
- **Production serving:** FastAPI (`main.py`) serves the SPA directly — all page routes (`/`, `/admin`, `/login`, `/setup`, `/stations-speed`, `/sales-report`, `/prep-plan`, `/wecom-push`, `/recipe*`, `/logs`) return `admin-web/dist/index.html`, and client-side `vue-router` takes over routing. Built JS/CSS chunks are mounted at `/assets` from `admin-web/dist/assets`. Login (`/login`) and initial setup (`/setup`) are SPA routes too, not separate HTML files.
- Station lookups no longer use a hardcoded JS constant — `admin-web/src/stores/stations.js` fetches `/api/stations` once and caches it in a Pinia store, avoiding drift from `config.py`'s `KITCHEN_STATIONS`.

### Station Definitions (`config.py`)

Stations are defined in `KITCHEN_STATIONS` dict. When adding a new station:
1. Add entry in `config.py` `KITCHEN_STATIONS`.
2. No frontend constant to update — `admin-web`'s stations store (`admin-web/src/stores/stations.js`) reads `/api/stations` at runtime.

### Realtime (`services/realtime/hub.py`, `/ws/realtime`)

Realtime updates use a **nudge + pull** model, not push-the-payload: the server only ever broadcasts a tiny `{"type": "nudge", "topic": "...", "scope": {...}}` message with no data, sequence number, or delta. Clients that receive a nudge re-fetch via the existing HTTP REST APIs.

- `RealtimeHub` (`services/realtime/hub.py`) tracks per-connection subscriptions (`{id, topics, filters}`) and dispatches `broadcast_nudge(topic, scope)` only to matching subscribers. Valid topics: `orders`, `tables`, `scraper`, `dashboard`, `logs`, `admin`.
- `orders`/`tables` nudges also debounce-trigger a combined `dashboard` nudge (`DASHBOARD_DEBOUNCE_SECONDS = 0.3s`) so the (expensive) dashboard summary endpoint isn't hit on every single change.
- Single endpoint `@app.websocket("/ws/realtime")` in `main.py` handles both auth modes: **Cookie session** (Admin SPA, browser) or **`?token=<api_token>`** query param (KDS and other non-cookie clients). See `authenticate_ws()`.
- There is no delta/seq/snapshot-cache protocol — clients are expected to be resilient to missed nudges (see KDS's 60s reconciliation poll below).

### KDS (`kds/`)

The kitchen display is a uni-app project (`kds/`, H5 build), no longer HTTP-polling based.

- `kds/utils/realtime.js` — `RealtimeConnection`: wraps `uni.connectSocket` to `/ws/realtime?token=...`, auto-reconnects on a fixed 3s interval, sends a 30s heartbeat ping, and replays pending subscriptions after reconnect.
- `kds/stores/realtime.js` — Pinia store (`useRealtimeStore`) built on top of `RealtimeConnection`: pages register per-topic handlers via `on(topic, handler)`; also runs a **60s low-frequency reconciliation poll** that re-triggers the `orders` handlers regardless of connection state, as a safety net against missed nudges or a dead connection.
- `kds/pages/kitchen/kitchen.vue` shows a prominent disconnect banner (断连告警，含提示音/振动) when the WS connection drops or is reconnecting, and queues print jobs via `kds/utils/printQueue.js` (`enqueuePrintTicket`/`retryAllFailedJobs`) with serialized processing + failure retry + a manual "retry failed" button.
- Build: `scripts/build_kds.sh` builds the uni-app H5 bundle and deploys it into `public/kds/`, which FastAPI mounts at `/kds` (`StaticFiles(directory=..., html=True)`).

---

## Testing & CI

- Test suite lives in `tests/` and mixes `unittest`-style and `pytest`-style tests; run with `pytest tests/` (collects both styles, 166 tests as of this writing). Plain `python -m unittest discover -s tests` no longer collects the full suite.
- CI (`.github/workflows/`) runs two jobs: a Python job (`pip install -r requirements.txt` + `pytest tests/ -v`) and an `admin-web` job (`npm ci` + `npm run build` + `npm run test`, i.e. vitest) in `admin-web/`.

## Deployment (`deploy/`)

Single-machine, single-instance, **single uvicorn worker** deployment — no Postgres/Redis; Docker may host the process with bind mounts but image pull is not delivery. Delivery follows **ADR 0011** (supersedes ADR 0010): GitHub Release **发行包 (Release Bundle)** (app tree + prebuilt Admin/KDS + **版本清单**/checksums) + Admin「系统更新」（版本检测 · **更新环境自检** · 应用更新）→ 更新作业; shop machines stay Node-free (no Deploy Key / clone). `deploy/` contains:

- `luyun.service` — systemd unit running `uvicorn main:app --workers 1` (must stay single-worker: the realtime hub, in-memory log buffer, and scraper failure counters all live in one process's memory).
- `luyun-update.service` — systemd oneshot Update Job started by Admin Apply Update.
- `Dockerfile` / `docker-compose.yml` / `docker-entrypoint.sh` — Docker process-shell (bind-mount Release Bundle tree under parent volume; upgrades still via Admin「系统更新」). Helper: `scripts/docker_up.sh`.
- `Caddyfile` / `nginx.conf` — reverse proxy + TLS termination, forwarding `/api/*` and `/ws/*` to the backend and serving `admin-web/dist` directly at the proxy layer.
- `backup.sh` + `luyun-backup.service`/`luyun-backup.timer` — SQLite online backup (`sqlite3 .backup`) on a systemd timer, with retention policy.
- `env.production.example` — production environment variable template (GitHub Releases PAT optional for the public repo).
- `deploy/README.md` — Bootstrap Install, Docker Compose, upgrade via Version Check / Update Preflight / Apply Update, reverse proxy, backup. Publish: `scripts/publish_release.sh`. Operator flow: `docs/RELEASE_AND_DEPLOY.md`; bundle contract: `docs/release-asset-layout.md`.

---

## Important Gotchas

- **uvicorn `--reload` resets globals.** Every API route must use `db=Depends(_get_db)` so `db_manager` is resolved fresh per request — never captured at module load time.
- **`SELECT rowid, *`** on a table with `INTEGER PRIMARY KEY` returns the `id` column twice (once as `id`, once as `rowid` alias), producing duplicate keys → `JSON.stringify()` fails. Always filter/handle this in the API layer.
- **CORS:** `allow_credentials=True` with `allow_origins=["*"]` is incompatible. Use `allow_credentials=False`. Still true — `main.py` sets `allow_origins=["*"]` + `allow_credentials=False`.
- **Batch station sync (`sync-stations`):** Updates orders table in batches of 200 rows, commits after each batch. The `updated` count reflects matched dishes; `skipped_no_match` are dishes with no mapping entry.
- **dish_stations primary key:** Uses `dish_name TEXT UNIQUE`, not an auto-increment ID. The API endpoint is `/api/dish-stations/{dish_name}` (path param, not ID).
- **FastAPI path priority:** `/api/admin/tables/{table_name}` matches before `/api/admin/tables/{table_name}/rows/{row_id}`. Ensure row-level routes have more specific paths.
- **No more per-table `.db` files or `ATTACH DATABASE`.** Everything except `logs.db` is one `data/app.db`.
- **Admin UI is not static HTML anymore.** Don't add pages under `public/`; add a Vue route/view under `admin-web/src/` and rebuild (`npm run build`) so `admin-web/dist` picks it up. `public/` now only holds the KDS build output, vendored JS/CSS, and `recipe.css`.
- **Realtime payloads carry no data.** Don't expect fields beyond `topic`/`scope` on a `nudge` message — always re-fetch via HTTP after receiving one. Because there's no server-side ATTACH/multi-db lock contention anymore, cross-table queries are just normal SQL, but the app is still single-worker only (see `deploy/README.md`) because of the in-memory realtime hub/log buffer/scraper state.

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
