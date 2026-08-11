# LuckIn 订单数据采集与查询系统

餐厅订单数据采集与历史查询系统，服务于**LuckIn**。

## 项目概述

本系统通过 Playwright 浏览器自动化定时爬取餐饮 POS 系统（`cy7mm.wuuxiang.com`）的桌台点菜数据，将订单持久化存储至本地数据库，并提供 REST API 查询接口。

**核心功能：**
- 定时采集订单数据（营业时间内每 5-20 秒轮询）
- 订单历史查询（按档口/桌号/时间/菜品名等多维搜索）
- 实时营业数据（各档口待处理数量、紧急订单统计）
- 热销菜品排行与销售汇总
- 日报表采集与半成品用量计算
- **档口进单速率统计**（实时折线图，支持历史对比）
- **销售报表**（基于数据库订单计算，支持半成品换算规则管理）
- **备货计划**（基于历史订单和半成品规则预测备货量，管理批次与库存流水）

**技术栈：** FastAPI + SQLite (aiosqlite) + Playwright + Pandas

---

## 目录结构

```
luyun/
├── main.py                       # FastAPI 入口（含 SPA 页面路由、/ws/realtime）
├── config.py                     # 应用配置（Pydantic Settings，非 JSON 目录）
├── database.py                   # 单库 DatabaseManager 门面（~75 行，由 db_core/ 组合而成）
├── db_core/                      # 数据库实现拆分：connection/schema/table_db/各 repo/aggregation/reports/stats
├── models.py                     # Pydantic 模型
├── api/                          # REST API
├── services/                     # 业务逻辑（凭据、备货、企微推送、realtime hub 等）
├── scraper/
│   ├── restaurant_scraper.py     # 采集组合根（run_cycle / 状态 DTO）
│   ├── pos_session.py / pos_http_client.py / table_change_detector.py
│   ├── delivery_bill_tracker.py / state_store.py / order_line_builder.py
│   └── _common.py
├── admin-web/                    # 管理后台 SPA（Vite + Vue3 + Pinia + vue-router）
├── kds/                          # 厨房显示屏（uni-app，H5 构建后部署到 public/kds/）
├── public/                       # 仅保留 kds 构建产物 / vendor 静态资源 / recipe.css
├── deploy/                       # 单机部署：systemd + Caddy/Nginx + SQLite 备份脚本
├── data/                         # SQLite（app.db + logs.db + recipes.db）与运行时状态（gitignore）
├── scripts/                      # 日常入口；说明见 scripts/README.md
│   ├── start.py                  # 启动服务
│   ├── quick_start.sh
│   ├── build_kds.sh              # 构建 KDS uni-app H5 并部署到 public/kds/
│   └── archive/                  # 一次性迁移 / 调试 / 冒烟（见 scripts/README.md）
├── tests/                        # pytest（含 unittest 风格用例）
└── docs/                         # 设计文档（营业额口径、备货计划等）；ADR 在 docs/adr/
```

> 登录凭据：`/setup` → `data/credentials.enc`。设计说明见 `docs/README.md`。

---

## 核心功能模块

### 1. 实时订单爬虫（`scraper/restaurant_scraper.py` + 子模块）

以 Playwright Chromium 为内核爬取 POS 数据。`RestaurantScraper` 是组合根（单轮编排 `run_cycle` + 状态 DTO），委托 `pos_session` / `pos_http_client` / `table_change_detector` / `delivery_bill_tracker` / `state_store` / `order_line_builder`。

**登录方式：** 浏览器 Cookie 登录态（Playwright 自动管理 Session）

**营业时间：** 每日 07:30 - 21:30（北京时间），非营业时间自动暂停。

**轮询机制：** 每 5-20 秒（随机间隔）爬取一次，有变化时自动检测。

**爬取流程：**
```
1. 初始化 Chromium（无头模式，1920×1080）
2. 打开登录页 → 填写手机号+密码 → 点击登录
3. 记录浏览器 Cookie（后续 API 请求复用）
4. 访问桌台列表页 → 拦截 /getbusypointdata API → 获取所有占用桌台
5. 对有变化的桌台 → 调用 /getbsdetail API → 获取菜品明细
6. 解析 scDetail 数据 → 按档口分类 → 去重（退菜检测）→ 返回变化量
```

**退菜检测：** 记录每个桌台的历史菜品数量，金额减少时自动识别退菜并生成退菜记录。

**菜品分类：** 基于规则（关键字匹配）分为茶水/点心/美点/佳点/特点/禄点/凉菜/热菜等类目，同时从 `dish_stations` 表查询档口映射。

---

### 2. 订单查询 API（`api/orders.py`）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/orders/` | 查询订单，支持按档口/桌号/时间过滤，**默认当日** |
| GET | `/api/orders/table/{table_number}` | 获取指定桌台的全部订单 |
| GET | `/api/orders/stations-today-stats` | 今日各档口订单数量统计 |
| GET | `/api/orders/station-speed` | 档口进单速率（5分钟粒度，支持历史对比） |
| GET | `/api/orders/station/{station_id}/stats` | 获取档口统计（总数/总数量/紧急数） |
| GET | `/api/orders/priority/urgent` | 获取紧急订单（等待超过 20 分钟） |
| GET | `/api/orders/paginated` | 分页查询（默认当日） |
| GET | `/api/orders/quick-stats` | 轻量统计（一次 DB 查询返回总量/总数量/营业额） |
| GET | `/api/orders/search` | 多维搜索（日期+档口+桌号+菜品名） |
| GET | `/api/orders/{order_id}` | 获取单个订单详情 |

---

### 3. 菜品分析 API（`api/dishes.py`）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/dishes/merged` | 合并菜品视图（按名称+档口聚合，含等待时间/优先级） |
| GET | `/api/dishes/merged/paginated` | 分页合并视图（DB 层聚合优化） |
| GET | `/api/dishes/quick-summary` | 轻量汇总（各档口各状态的菜品数量） |
| GET | `/api/dishes/hot-dishes` | 热销菜品排行（Top-N 按销量） |
| GET | `/api/dishes/{dish_name}/detail` | 单品详情（含完整订单明细） |
| GET | `/api/dishes/station/{station_id}/dishes` | 指定档口全部菜品 |
| GET | `/api/dishes/category/{category}` | 按类目查询（茶水/点心/凉菜/热菜） |
| GET | `/api/dishes/priority/{priority}` | 按优先级查询（urgent/high/normal） |
| GET | `/api/dishes/urgent/all` | 所有紧急菜品 |
| GET | `/api/dishes/stats/overview` | 菜品统计总览 |
| POST | `/api/dishes/import-mappings` | 批量导入菜品-档口映射（CSV） |

**合并菜品聚合逻辑：**
```
多个同名菜品（同一档口）→ 聚合为一条记录
  - total_quantity: 总量
  - table_numbers: 涉及桌号集合
  - max_wait_time: 最大等待时长（毫秒）
  - avg_wait_time: 平均等待时长
  - priority: 根据等待时间自动计算（urgent>20min / high>15min / normal）
```

---

### 4. 菜品-档口映射 API（`api/dish_stations.py`）

管理 `dish_stations` 表（菜品名称 → 档口ID）。

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/dish-stations/` | 列出所有映射（支持菜品名/档口过滤） |
| GET | `/api/dish-stations/stats` | 统计：总数、按档口分布 |
| GET | `/api/dish-stations/{dish_name}` | 查询单个菜品映射 |
| POST | `/api/dish-stations/` | 创建映射（菜品名唯一） |
| PUT | `/api/dish-stations/{dish_name}` | 更新映射（档口/备注） |
| DELETE | `/api/dish-stations/{dish_name}` | 删除映射 |
| POST | `/api/dish-stations/batch` | 批量创建 |
| GET | `/api/dish-stations/search` | 分页搜索 |

### 半成品规则 API（`api/semi_rules.py`）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/semi-rules/` | 列出全部规则 |
| GET | `/api/semi-rules/search?q=` | 搜索规则 |
| POST | `/api/semi-rules/` | 创建/更新规则 |
| PUT | `/api/semi-rules/{rule_id}` | 更新规则 |
| DELETE | `/api/semi-rules/{rule_id}` | 删除规则 |
| GET | `/api/semi-rules/dishes/available` | 获取可选菜品名（用于快速添加） |
| GET | `/api/semi-rules/dishes/grouped` | 按菜品分组返回规则 |

### 销售报表 API

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/orders/sales-report?start_date=&end_date=&station=` | 计算销售报表 |

### 备货计划 API（`api/prep_plan.py`）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/prep-plan/forecast` | 计算目标时间窗内的备货预测 |
| POST | `/api/prep-plan/generate` | 生成并保存一份备货计划 |
| GET | `/api/prep-plan/current` | 获取当前或最近一次备货计划 |
| POST | `/api/prep-plan/batches` | 创建备货批次 |
| PATCH | `/api/prep-plan/batches/{batch_id}` | 更新批次余量/状态 |
| GET | `/api/prep-plan/movements` | 查询库存流水 |
| POST | `/api/prep-plan/movements` | 创建库存流水 |
| GET | `/api/prep-plan/expiring` | 查询临期批次 |
| GET | `/api/prep-plan/accuracy` | 计算历史计划准确率 |
| POST | `/api/prep-plan/init-items-from-rules` | 从半成品规则初始化备货品 |

### 固定报表菜品 API（`api/report_dishes.py`，`/api/report-dishes`）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/report-dishes/` | 列出固定报表菜品 |
| POST | `/api/report-dishes/` | 新增固定报表菜品 |
| DELETE | `/api/report-dishes/{dish_id}` | 删除 |
| PUT | `/api/report-dishes/reorder` | 拖拽排序 |

### 企微推送 API（`api/wecom_push.py`，`/api/wecom-push`）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/wecom-push/webhooks` | 列出 webhook |
| POST | `/api/wecom-push/webhooks` | 新增 webhook |
| PUT | `/api/wecom-push/webhooks/{webhook_id}` | 更新 webhook |
| DELETE | `/api/wecom-push/webhooks/{webhook_id}` | 删除 webhook |
| POST | `/api/wecom-push/webhooks/{webhook_id}/test` | 测试 webhook |
| POST | `/api/wecom-push/send-text` | 发送自定义文本 |
| GET | `/api/wecom-push/jobs` | 列出推送任务 |
| POST | `/api/wecom-push/jobs` | 新增任务 |
| PUT | `/api/wecom-push/jobs/{job_id}` | 更新任务 |
| DELETE | `/api/wecom-push/jobs/{job_id}` | 删除任务 |
| POST | `/api/wecom-push/jobs/{job_id}/preview` | 预览消息 |
| POST | `/api/wecom-push/jobs/{job_id}/send-now` | 立即发送 |
| GET | `/api/wecom-push/logs` | 发送记录 |
| GET | `/api/wecom-push/meta` | 推送相关元数据（档口等） |

### 配方 SOP API（`api/recipes.py`，`/api/recipes`；写操作需 admin 鉴权）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/recipes/stations` | 岗位列表 |
| GET | `/api/recipes/stations/{slug}` | 岗位详情 |
| GET | `/api/recipes/stations/{slug}/recipes` | 岗位下配方列表 |
| POST | `/api/recipes/stations` | 新增岗位 |
| POST | `/api/recipes/stations/{slug}/rename` | 岗位改名 |
| DELETE | `/api/recipes/stations/{slug}` | 删除岗位 |
| POST | `/api/recipes/stations/{slug}/recipes` | 新增配方 |
| PUT | `/api/recipes/recipes/{recipe_id}` | 更新配方 |
| DELETE | `/api/recipes/recipes/{recipe_id}` | 删除配方 |
| POST | `/api/recipes/recipes/{recipe_id}/toggle-active` | 启用/停用配方 |
| GET | `/api/recipes/recipes/{recipe_id}/history` | 配方修改历史 |
| GET | `/api/recipes/stations/{slug}/export` | 导出岗位配方 CSV |
| POST | `/api/recipes/stations/{slug}/import` | 导入岗位配方 CSV |
| GET | `/api/recipes/stations/{slug}/docx` | 导出岗位配方 Word |

### 数据分析 / 导出 API（`api/analytics.py`、`api/export_api.py`）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/analytics/sales-trend` | 营业额/订单走势（按日/按周） |
| GET | `/api/analytics/refunds` | 退款/退菜明细 |
| GET | `/api/export/sales-report.csv` | 服务端导出销售报表 CSV |

### 餐桌 API（`api/tables.py`，`/api/tables`）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/tables/snapshot` | 餐桌状态快照 |
| GET | `/api/tables/operations` | 餐桌操作记录 |

### 管理后台 API（`api/admin.py`，`/api/admin`；均需 admin 鉴权）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/admin/tables` | 列出可管理的表 |
| GET | `/api/admin/tables/{table}/schema` | 表结构（字段/类型/非空/默认值/主键） |
| GET | `/api/admin/tables/{table}/rows` | 分页行查询 |
| POST | `/api/admin/tables/{table}/rows` | 新增行 |
| PUT | `/api/admin/tables/{table}/rows/{row_id}` | 更新行 |
| DELETE | `/api/admin/tables/{table}/rows/{row_id}` | 删除行 |
| POST | `/api/admin/tables/{table}/columns` | 新增列 |
| DELETE | `/api/admin/tables/{table}/columns/{column_name}` | 删除列 |
| GET | `/api/admin/tables/{table}/stats` | 表统计 |
| POST | `/api/admin/sync-stations` | 按映射回填订单档口 |
| GET | `/api/admin/unmapped-dishes` | 未映射档口的菜品 |
| POST | `/api/backup/export` | 导出加密备份包（`.luyunbak`，含 app.db / recipes.db / 凭据） |
| POST | `/api/backup/import/preview` | 备份包导入预览 |
| POST | `/api/backup/import/apply` | 执行备份包导入 |
| GET | `/api/backup/snapshots` | 列出本地快照 |
| POST | `/api/backup/snapshots/{ts}/rollback` | 从快照回滚 |
| GET | `/api/admin/scraper-health` | 采集/对账健康（透传完整 health） |
| POST | `/api/admin/reconcile` | 触发对账（`date`/`fix`/`notify`） |

### 鉴权 API（`api/auth.py`，`/api/auth`）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/auth/status` | 登录状态（`logged_in`/`initialized`/`username`） |
| POST | `/api/auth/init` | 首次创建管理员账号 |
| POST | `/api/auth/login` | 登录（`username`/`password`/`remember`） |
| POST | `/api/auth/logout` | 退出登录 |
| POST | `/api/auth/change-password` | 修改登录密码 |
| POST | `/api/auth/token` | 生成 API Token（KDS 等设备，仅显示一次） |
| GET | `/api/auth/tokens` | 列出 API Token |
| DELETE | `/api/auth/token/{token_hash_prefix}` | 撤销指定 Token |
| GET | `/api/auth/verify` | 校验当前会话 / Token |

### 登录凭据 API（`api/credentials.py`，`/api/credentials`；需 admin 鉴权）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/credentials` | 读取当前 POS 凭据（脱敏） |
| POST | `/api/credentials` | 保存/更新 POS 凭据（Fernet 加密） |
| POST | `/api/credentials/verify-login` | 验证 POS 登录可用性 |
| DELETE | `/api/credentials` | 清空凭据（爬虫进入待机） |

### 日志 API（`api/logs.py`，`/api/logs`；均需 admin 鉴权）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/logs` | 历史日志分页查询（持久化） |
| GET | `/api/logs/recent` | 内存实时最近日志 |
| GET | `/api/logs/persisted/recent` | 持久化的最近 N 条 |
| GET | `/api/logs/facets` | 可选 level / logger 维度 |
| GET | `/api/logs/stats` | 日志写入统计 |
| POST | `/api/logs/cleanup` | 清理 N 天前日志 |

### 系统 / 实时 / 爬虫控制（`main.py` app 级路由）

| 方法 | 路由 | 说明 |
|------|------|------|
| WS | `/ws/realtime` | 实时订阅（nudge 模型；Cookie 或 `?token=` 鉴权） |
| GET | `/api/system/status` | 系统状态（数据库/内存/运行时长） |
| GET | `/api/system/health` | 健康检查 |
| GET | `/api/system/scraper-health` | 采集/对账健康（只读，供监控） |
| GET | `/api/dashboard/summary` | 仪表盘聚合数据 |
| GET | `/api/stations`、`/api/stations/{station_id}` | 档口列表 / 单个档口 |
| POST | `/api/scraper/start`、`/api/scraper/stop` | 启动 / 停止爬虫 |
| GET | `/api/scraper/status` | 爬虫运行状态 |

> 页面路由（`/`、`/login`、`/setup`、`/admin`、`/sales-report`、`/recipe*`、`/prep-plan`、`/wecom-push`、`/logs`）均返回 admin-web SPA 的 `index.html`，由前端 vue-router 接管；未登录访问受 `HtmlAuthMiddleware` 服务端重定向到 `/login`。

**档口定义（`config.py`）：**

| 档口 ID | 名称 | 颜色 |
|---------|------|------|
| `xibing` | 西饼档 | #FF6B6B |
| `changfen` | 肠粉档 | #4ECDC4 |
| `shulong` | 熟笼档 | #45B7D1 |
| `mingdang1` | 明档1 | #96CEB4 |
| `mingdang2` | 明档2 | #FECA57 |
| `jianzha` | 煎炸档 | #FF9FF3 |
| `loumian` | 楼面 | #A78BFA |

---

### 5. 销售报表（Web，基于订单库）

页面 `/sales-report` 与接口 `GET /api/orders/sales-report` 使用 `database.compute_sales_report()`，从 `app.db` 的 `orders` 表聚合，半成品规则来自同库的 `semi_finished_rules`（与备货计划、企微推送共用口径）。详见 `docs/DATA_AND_SALES.md`、`docs/DATA_REVENUE.md`。

---

### 6. 数据库设计

当前后端使用**单 SQLite 数据库架构**：除写入量大的日志表 (`logs`) 独立存放在 `data/logs.db`，其余全部业务表 + 鉴权表（`auth`：`admin_user`/`sessions`/`api_tokens`）统一存放在同一个 `data/app.db`（`PRAGMA journal_mode=WAL`）。跨表查询直接写 SQL JOIN 即可，不再需要 `ATTACH DATABASE`。`DatabaseManager`（`database.py`，~75 行门面）由 `db_core/` 下按职责拆分的多个 Mixin 组合而成：

| 模块 | 职责 |
|------|------|
| `db_core/connection.py` | 单库连接建立/关闭、WAL、备份导出 |
| `db_core/schema.py` | 全部表的 `CREATE TABLE` / 索引定义（`_TABLE_SCHEMAS` / `_INDEX_DEFINITIONS` / `ALL_TABLES`） |
| `db_core/table_db.py` | `TableView` 单表访问视图（共享同一连接）、KDS 列迁移 |
| `db_core/orders_repo.py` / `tables_repo.py` / `dish_stations_repo.py` / `semi_rules_repo.py` / `report_dishes_repo.py` / `wecom_repo.py` | 各业务表的查询/写入 |
| `db_core/aggregation.py` / `reports.py` / `stats.py` | 跨表聚合统计、销售报表/经营分析、性能与健康检查 |

不要在路由层直接使用 `db._conn` 做业务查询。若环境仍保留旧的分库 `.db` 文件，可运行 `scripts/archive/consolidate_dbs.py`（幂等、自动备份，回滚步骤见同目录 `consolidate_dbs.rollback.md`）合并进 `app.db`；迁移完成并确认数据无误后，旧分库文件可安全删除。

`app.db` 中包含的表（均通过 `settings.DATABASE_PATHS` 指向同一文件）：

| 表名 | 说明 |
|------|------|
| `orders` | 订单主表 |
| `tables` | 桌台状态 |
| `stations` | 档口定义 |
| `dish_stations` | 菜品到档口映射 |
| `semi_finished_rules` | 半成品换算规则 |
| `report_dishes` | 销售报表固定菜品 |
| `prep_items` | 备货品定义 |
| `prep_batches` | 备货批次 |
| `prep_stock_movements` | 备货库存流水 |
| `prep_plan_runs` | 备货计划运行记录 |
| `prep_plan_items` | 备货计划明细 |
| `prep_plan_item_slots` | 分时段备货计划 |
| `wecom_push_webhooks` / `wecom_push_jobs` / `wecom_push_logs` | 企微推送配置/任务/日志 |
| `admin_user` / `sessions` / `api_tokens`（`auth`） | 管理员账号、会话、API Token |

`data/logs.db`（独立文件）：`logs` 表，日志写入量大，未并入 `app.db`。

**核心表结构摘要：**

```sql
-- 订单表（核心）
orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  business_flow_id TEXT,        -- 业务流水号（唯一）
  table_number TEXT,             -- 桌号
  dish_name TEXT,
  quantity INTEGER,
  order_time DATETIME,           -- 下单时间
  price REAL, total_amount REAL,
  status TEXT,                   -- 订单状态（未结/已结）
  category TEXT,                 -- 茶水/点心/凉菜/热菜
  station TEXT,                  -- 档口ID
  priority TEXT                  -- urgent/high/normal
)

-- 索引：档口+状态、状态+时间、桌号、菜品名、档口+状态+时间

-- 桌台表
tables (table_number TEXT PRIMARY KEY, amount, people, duration, status, updated_at)

-- 档口配置表
stations (station_id TEXT PRIMARY KEY, name, color, config JSON)

-- 菜品-档口映射表
dish_stations (id INTEGER PRIMARY KEY AUTOINCREMENT, dish_name TEXT UNIQUE, station_id, notes, timestamps)

-- 半成品换算规则表
semi_finished_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, dish_name, semi_name, position, factor REAL, unit, category, notes, timestamps)

-- 备货计划表
prep_items / prep_batches / prep_stock_movements / prep_plan_runs / prep_plan_items / prep_plan_item_slots
```

**配方库** `data/recipes.db`（独立文件，不并入 `app.db`）：`sop_stations` / `sop_recipes` / `sop_recipes_history`，由 `services/recipes/store.py` 管理。

---

## 前端页面

管理后台已改为 **Vue3 SPA**（`admin-web/`，Vite + Vue3 + Pinia + vue-router），由 FastAPI 在生产环境统一以 `admin-web/dist/index.html` 提供服务；旧的 `public/*.html` 多页面已删除。厨房显示屏是独立的 **uni-app** 项目（`kds/`）。

### 管理后台 SPA（`admin-web/`）

开发：`cd admin-web && npm run dev`（Vite `:5173`，反代 `/api`、`/ws` 到后端 `:8000`）。构建：`npm run build` → `admin-web/dist`，由 `main.py` 直接服务。各页面均为 vue-router 客户端路由，登录态由全局路由守卫（`router/index.js`）统一校验：

| 路由 | 视图组件 | 说明 |
|------|---------|------|
| `/` | `DashboardView.vue` | 监控仪表盘：档口实时订单柱状图/进单速率折线图（`StationSpeedChart.vue`）、今日统计、热销菜品、系统状态、营业额趋势，通过 `/ws/realtime` nudge 驱动刷新（非定时轮询） |
| `/admin` | `AdminView.vue` | 数据管理：数据库表 CRUD、字段管理、档口映射管理、批量分类 |
| `/sales-report` | `SalesReportView.vue` | 销售报表：日期范围 + 档口筛选查看销量/半成品用量，规则管理，CSV 导出 |
| `/prep-plan` | `PrepPlanView.vue` | 备货计划：预测、批次与库存流水管理 |
| `/wecom-push` | `WecomPushView.vue` | 企微推送 Webhook/任务/日志管理 |
| `/logs` | `LogsView.vue` | 运行日志查看 |
| `/recipe`、`/recipe/detail`、`/recipe/manage`、`/recipe/print`、`/recipe/qr` | `views/recipe/*.vue` | 配方 SOP：档口导航、详情、管理、打印、二维码 |
| `/login` | `LoginView.vue` | 登录（未登录访问受保护路径会被服务端 302 重定向到此） |
| `/setup` | `SetupView.vue` | 首次凭据配置（POS 账号/密码/门店 ID） |

入口：`http://localhost:8000/admin/`（以及上表各路由路径）。

### 厨房显示屏 KDS（`kds/`）

uni-app 项目，H5 构建后经 `scripts/build_kds.sh` 部署到 `public/kds/`，由 FastAPI 挂载在 `/kds`。通过 `/ws/realtime?token=` 接收 nudge 后拉取当天订单，含 60 秒低频对账兜底、断连告警（横条 + 提示音/振动）、打印失败重试队列。

入口：`http://localhost:8000/kds/`

---

## 系统 API 概览

### 系统管理（`main.py`）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/` | 服务信息 |
| GET | `/api/system/status` | 完整系统状态（DB统计/内存/运行时间） |
| GET | `/api/system/health` | 健康检查 |
| GET | `/api/stations` | 档口列表 |
| GET | `/api/stations/{station_id}` | 档口详情 |
| POST | `/api/scraper/start` | 启动爬虫任务 |
| POST | `/api/scraper/stop` | 停止爬虫任务 |
| GET | `/api/scraper/status` | 爬虫状态+营业时间信息 |

---

## 快速启动

### 方式一：一键启动（推荐）

```bash
cd /Users/admin/Downloads/luyun
bash scripts/quick_start.sh
```

脚本会自动：
1. 检查 Python 版本 ≥ 3.8
2. 安装依赖（`pip install -r requirements.txt`）
3. 安装 Playwright Chromium 浏览器
4. 创建日志目录
5. 启动 FastAPI 服务

> 该脚本只启动后端。管理后台 SPA 需要单独构建一次（见下），否则 `/admin/` 等页面拿不到 `admin-web/dist` 产物。

### 方式二：手动启动

```bash
cd /Users/admin/Downloads/luyun

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium

# 构建管理后台 SPA（生产模式，产物 admin-web/dist 由 FastAPI 直接服务）
cd admin-web && npm ci && npm run build && cd ..

# 启动服务
python3 scripts/start.py
# 或
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

管理后台开发时也可用 Vite 独立开发服务器（热更新）：`cd admin-web && npm run dev`（`:5173`，反代 `/api`、`/ws` 到 `:8000`）。

服务地址：`http://localhost:8000`
API 文档：`http://localhost:8000/docs`

---

## 运行验证

后端测试套件（`tests/` 混合 unittest 风格与 pytest 风格，共 166 个用例，用 pytest 统一收集）：

```bash
python3 -m pytest tests/
```

> 旧的 `python3 -m unittest discover -s tests` 不再能收集完整的测试集合，请改用 `pytest`。

管理后台 SPA 单测（vitest）：

```bash
cd admin-web && npm run test
```

页面冒烟测试需要先启动服务并安装 Playwright Chromium：

```bash
python3 scripts/start.py
python3 scripts/archive/smoke_public_pages.py --base-url http://127.0.0.1:8000
```

CI（`.github/workflows/`）在 `push`/`pull_request` 时并行跑两个 job：Python 后端（`pip install -r requirements.txt` + `pytest tests/ -v`）与 `admin-web`（`npm ci` + `npm run build` + `npm run test`）。

---

## 依赖说明

| 依赖 | 用途 |
|------|------|
| `fastapi` + `uvicorn` | Web 框架与 ASGI 服务器 |
| `aiosqlite` | 异步 SQLite 驱动（单库 `app.db`，WAL 模式） |
| `playwright` | 浏览器自动化（实时爬虫+销售报表） |
| `pandas` + `openpyxl` | 数据分析与 Excel 导出 |
| `feishu-oapi` | 飞书多维表格数据源 |
| `pydantic` + `pydantic-settings` | 配置管理与数据校验 |
| `python-jose[cryptography]` + `passlib[bcrypt]` + `bcrypt` | 管理员账号密码哈希与会话/Token 鉴权（`services/auth_service.py`） |
| `python-multipart` | 表单/文件上传解析（FastAPI 依赖） |
| `httpx` + `aiohttp` + `requests` | 异步/同步 HTTP 客户端 |
| `beautifulsoup4` + `lxml` | HTML 解析（配方 SOP 渲染等） |
| `markdown` | Markdown → HTML 渲染（配方 SOP） |
| `python-docx` | 生成 Word 文档（配方 SOP 导出） |
| `aiofiles` | 异步文件 IO |
| `psutil` | 内存监控 |
| `pytest` | 后端测试框架（`tests/`，见「运行验证」） |

管理后台前端（`admin-web/package.json`）：`vue` + `vue-router` + `pinia`（SPA 框架/路由/状态管理）、`echarts`（图表）、`qrcode`（配方二维码）；构建工具 `vite` + `@vitejs/plugin-vue`，测试用 `vitest`。

已确认移除的未用依赖：`celery`、`asyncio-mqtt`（当前 `requirements.txt` 中均已不存在）。

---

## 配置说明

### 登录凭据（账号 / 密码 / 门店 ID）

POS 系统的登录账号、密码、`shop_id`、`company_id` 等敏感字段不再写在文件里，而是通过 Web 页面录入并加密保存：

1. 启动服务后访问 <http://localhost:8000/setup>
2. 填入手机号、密码、门店 ID（可粘贴 POS 餐桌列表 URL 自动解析）
3. 保存后内容以 Fernet 加密写入 `data/credentials.enc`，密钥位于 `data/.cred_key`

加密文件与密钥已加入 `.gitignore`，并设置为 `0o600` 权限。跨主机迁移时需要同时复制 `credentials.enc` 与 `.cred_key`，或在新主机的环境变量 `LUYUN_CRED_KEY` 中提供同一密钥。

> 若旧路径 `config/config.json` 中仍有 `login.username/password`，首次启动会自动迁移到加密文件并擦除敏感字段。

### 环境变量（`.env`，仅含非敏感项）

```env
DEBUG=true
HOST=0.0.0.0
PORT=8000
# 营业时段 / 轮询间隔 / headless / 重试次数已改由数据库运行配置管理，
# 在「配置 → 运行配置」页面在线修改并热生效，不再用环境变量。
ADMIN_API_KEY=  # 已弃用的过渡期方案：正式鉴权是访问 /login 完成账号密码登录（Session Cookie）
                # 或用 API Token（api_tokens 表，供 KDS 等无 Cookie 客户端用 ?token= 鉴权）
LUYUN_CRED_KEY= # 可选：跨主机迁移时统一指定 Fernet 密钥
```

## 部署（`deploy/`）

生产环境按**单机、单实例、单 uvicorn worker**部署，不使用 Postgres/Redis——实时推送 Hub（`services/realtime/hub.py`）、内存日志缓冲区、爬虫失败计数等状态都保存在单个进程内存中，多 worker 会导致状态分裂。交付与升级遵循 **ADR 0011**（取代 ADR 0010）：GitHub Release **发行包**（应用树 + 预构建 Admin/KDS + 版本清单/校验）+ Admin「系统更新」（版本检测 · 更新环境自检 · 应用更新）→ 更新作业；生产机**不**跑 `npm` / `build_kds.sh`，也**不**需要 Deploy Key / clone。Docker 可以是进程外壳，但**不是**以镜像 pull 为交付真相。`deploy/` 目录内容：

| 文件 | 用途 |
|---|---|
| `luyun.service` | systemd 单元，常驻运行 `uvicorn main:app --workers 1` |
| `luyun-update.service` | systemd oneshot Update Job（Admin Apply Update） |
| `Caddyfile` / `nginx.conf` | 反向代理 + TLS 终结配置（二者等价，Caddy 为首选） |
| `backup.sh` + `luyun-backup.service` / `luyun-backup.timer` | SQLite 在线备份（`sqlite3 .backup`）与每日 systemd 定时任务 |
| `env.production.example` | 生产环境变量示例（含 GitHub Release 凭据说明） |
| `README.md` | 组件级部署细节（反代、备份 timer、手工对照）；发版/升级规范见 `docs/RELEASE_AND_DEPLOY.md` |

- **新机器**：`scripts/bootstrap_install.sh` / Release `install.sh`（同款发行包；反代/TLS/POS 凭据仍人工）
- **日常升级**：`/setup` →「系统更新」（Version Check → Update Preflight → Apply Update → Update Job）
- **发版（开发者）**：`scripts/publish_release.sh`（发行包契约见 `docs/release-asset-layout.md`）

**发行规范与部署流程（推荐入口）**：[`docs/RELEASE_AND_DEPLOY.md`](docs/RELEASE_AND_DEPLOY.md)。  
组件细节见 [`deploy/README.md`](deploy/README.md)。

---

## 优先级计算规则

系统根据等待时长自动计算优先级，无需人工标记：

| 优先级 | 条件 | 颜色 |
|--------|------|------|
| `urgent` | 等待 > 20 分钟 | 红色 |
| `high` | 等待 > 15 分钟 | 黄色 |
| `normal` | 默认 | 青色 |

---

## 数据流全景

```
┌──────────────────────────────────────────────────────────────┐
│                 cy7mm.wuuxiang.com  POS 系统                 │
└──────────────────────────┬───────────────────────────────────┘
                           │ Playwright（Cookie 登录态）
                           ▼
┌──────────────────────────────────────────────────────────────┐
│            scraper/restaurant_scraper.py  采集组合根           │
│  • 每 5-20s 轮询（营业时间 07:30-21:30）                       │
│  • 检测桌台变化 → /getbsdetail 获取菜品明细                    │
│  • 退菜检测（对比历史数量）                                    │
│  • 档口分类（dish_stations 映射 + 规则匹配）                   │
└──────────────────────────┬───────────────────────────────────┘
                           │ 新订单入库
                           ▼
┌──────────────────────────────────────────────────────────────┐
│      database.py（门面）+ db_core/  单库 app.db（WAL）          │
│  orders / tables / stations / dish_stations / ... / auth      │
└──────────────────────────┬───────────────────────────────────┘
                           │ REST API 查询 + /ws/realtime nudge
              ┌────────────┴────────────┐
              ▼                         ▼
┌─────────────────────────┐  ┌─────────────────────────────────┐
│   api/orders.py         │  │  api/dishes.py                  │
│   • 分页/搜索            │  │   • 合并菜品视图                 │
│   • 档口统计             │  │   • 热销/紧急菜品                │
│   • 紧急订单             │  │   • 档口统计                    │
└─────────────────────────┘  └─────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
┌─────────────────────────┐  ┌─────────────────────────────────┐
│  admin-web/（Vue3 SPA）  │  │  kds/（uni-app）                │
│  仪表盘/数据管理/销售报表  │  │  厨房出品看板，/ws/realtime 拉取  │
│  /prep-plan /wecom-push │  │  当天订单，含对账兜底/断连告警    │
└─────────────────────────┘  └─────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│  SalesReportView.vue（admin-web）+  compute_sales_report()    │
│  • 基于 app.db 订单的销售汇总与半成品换算                        │
│  • 导出 CSV / 企微定时推送                                      │
└──────────────────────────────────────────────────────────────┘
```

---

## 常见问题

**Q: 爬虫启动后报"会话过期"？**
A: Cookie 失效，需重新登录。可删除 `scraper/` 下的 `*.json` 状态文件，下次运行会执行完整登录流程。

**Q: Web 销售报表数据不对？**
A: 数据来自 `app.db` 订单表与 `semi_finished_rules` / `report_dishes` 配置，见 `docs/DATA_REVENUE.md`。

**Q: 如何查看系统状态？**
A: 访问 `GET /api/system/status` 获取完整状态，包括数据库统计、内存使用、运行时间等。

---

## 许可证

[MIT](./LICENSE) © 2026 LuckIn
