# admin-web

管理端 SPA（Vite + Vue3 + Pinia）：全栈性能重构阶段三的重写产物，替代原
`public/*.html` 多页面方案。管理后台、卫生管理端、员工手机端入口与配方阅读面
共用这一个构建产物。

## 功能范围

- 仪表盘（`/`）：汇总卡片、热销菜品、最新订单、档口进单速率图表、系统状态
- 数据管理（`/admin`）：业务表通用 CRUD，并按业务 / 配方 / 卫生 / 认证分组浏览
  其余只读表；运行日志（PostgreSQL 的 `logs` 表，不再有 `logs.db`）提供 `/logs` 入口。含批量菜品分类弹窗、
  表结构管理（新增/删除列）
- 销售报表（`/sales-report`）：汇总卡、趋势图、档口占比、菜品明细、半成品换算规则、
  退款、企微推送、文字导出
- 配方 SOP（`/recipe`、`/recipe/detail`、`/recipe/manage`、`/recipe/print`、`/recipe/qr`）：
  岗位列表、配方阅读器（含 TOC/搜索/字号/主题/用量缩放）、配方管理编辑器、打印预览、
  岗位二维码
- 企微推送（`/wecom-push`）：Webhook 管理、推送任务管理、消息预览与立即发送、发送记录
- 备货计划（`/prep-plan`）：一键生成执行清单、档口执行板、辅助信息
- 实时日志（`/logs`）：实时跟踪 / 历史查询、级别与 logger 过滤、统计面板
- 卫生管理端（`/hygiene-roster`、`/hygiene-zones`、`/hygiene-daily`、
  `/hygiene-deep-clean`、`/hygiene-fix`、`/hygiene-boards`、`/hygiene-data`）：
  排班、责任区、日常与专项计划、整改单、红黑榜、卫生数据台账（浏览 / 导出 / 清理）
- 员工手机端（`/hygiene`、`/hygiene/login`、`/hygiene/register`）：独立员工会话、
  实时 nudge 刷新、离线重试
- 初始设置（`/setup`）：POS 凭据、数据库凭据、备份中心、系统更新（版本检测 /
  环境自检 / 应用更新 / 数据库迁移）。数据库只支持 PostgreSQL（ADR 0089），
  数据库凭据面板也按 PostgreSQL 连接展示
- WebSocket 实时事件驱动刷新（订单 / 餐桌 / 卫生）
- 档口常量统一从 `/api/stations` 拉取，不再硬编码

**生产环境路由**：反向代理配置见 `deploy/Caddyfile` / `deploy/nginx.conf`（把页面路径交给
SPA `index.html`，`/api` 与 `/ws` 转到后端）。本机直连 `:8000` 时由 FastAPI
（`main.py`）直接返回 `admin-web/dist/index.html`，vue-router 接管客户端路由。

## 本地开发

```bash
cd admin-web
npm install
npm run dev          # http://localhost:5173，自动代理 /api /ws 到 http://localhost:8000
```

如后端不在默认地址，设置 `LUYUN_API_PROXY` 环境变量后再跑 `npm run dev`。

鉴权沿用 Cookie Session：先在 `/login` 登录一次，浏览器 Cookie 对 `localhost`
同源共享，`fetch` 带 `credentials: 'include'`。

## 生产构建

```bash
npm run build        # 产出 dist/（含 sw.js、多角色 manifest 与 PWA 图标）
```

生产由 FastAPI 或 `deploy/` 反向代理托管。Service Worker 仅缓存前端程序资源；
`/api/*`、`/ws/*` 和上传下载始终直连后端。页面在每次启动时检查一次新版本，
发现等待中的新 Worker 后显示全局更新提示，用户确认后才切换并刷新。

管理端、卫生员工端与配方阅读端分别使用：

- `/pwa/manifests/admin.webmanifest`
- `/pwa/manifests/hygiene.webmanifest`
- `/pwa/manifests/recipe.webmanifest`

## 目录结构

```
src/
  api/client.js            # fetch 封装（Cookie 会话、401 跳转登录页）
  router/index.js          # 路由与登录守卫（hygiene 管理端 / 员工端分支）
  stores/
    stations.js            # 档口常量（替代原硬编码 STATIONS_MAP）
    standardPhotoCache.js  # 卫生标准图缓存状态
    imageUploadQueue.js    # 图片上传队列（弱网重试）
  composables/             # 页面级状态，如 useRealtime / useNudgePull / useDashboardData /
                           # useAdminTable / useSalesReport / useSemiRules / useLogs /
                           # usePrepPlan / useWecomPush / useSystemUpdate / useBackupCenter /
                           # useDbCredentials / usePosCredentials / useRuntimeSettings /
                           # useSystemHealth / useHygieneRealtime / usePwaUpdate
  utils/                    # 纯函数，如 dateRange / recipeCore / salesReportText /
                           # hygieneWorkFlow / hygieneMarkup / backupPoints / updateProgress
  components/
    NavBar.vue / SvgIcon.vue / PwaUpdateBanner.vue / ImageUploadQueuePanel.vue
    admin/*.vue             # DataTable、RowEditModal、ClassifyDishesModal、ColumnManageModal
    dashboard/*.vue         # 汇总卡、热销、实时桌态、系统状态等面板
    salesreport/*.vue       # 报表表格、图表、规则与导出弹窗
    hygiene/*.vue           # 卫生专有组件（取景 / 标准图 / 任务卡等）
    backup/ system/ update/ ui/  # 备份中心、健康水位、版本更新、通用控件
  views/
    DashboardView.vue / AdminView.vue / SalesReportView.vue / LogsView.vue /
    PrepPlanView.vue / WecomPushView.vue / LoginView.vue / SetupView.vue
    recipe/*.vue            # RecipeStationsView / RecipeDetailView / RecipeManageView /
                            # RecipePrintView / RecipeQrView
    hygiene/*.vue           # 管理端 7 页 + 员工端 Home/Login/Register + 两个 Layout
```

## 已知限制 / 后续可优化项

- 主 vendor chunk（Vue/Pinia/Router/ECharts 合并）约 1.1MB，未做手动分包
  （`build.rollupOptions.output.manualChunks`），首屏加载可进一步优化
- 登录/初始设置已是 SPA 路由（`/login`、`/setup`），不再使用独立静态 HTML
