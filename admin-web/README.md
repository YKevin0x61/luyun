# admin-web

管理端 SPA（Vite + Vue3 + Pinia）：全栈性能重构阶段三的重写产物，替代原
`public/*.html` 多页面方案。管理后台、卫生管理端、员工手机端入口与配方阅读面
共用这一个构建产物。

## 功能范围

- 仪表盘（`/`）：汇总卡片、热销菜品、最新订单、档口进单速率图表、系统状态
- 数据管理（`/admin`）：业务表通用 CRUD，并按业务 / 配方 / 卫生 / 排班 / 认证分组浏览
  其余只读表；运行日志（PostgreSQL 的 `logs` 表，不再有 `logs.db`）提供 `/logs` 入口。含批量菜品分类弹窗、
  表结构管理（新增/删除列）
- 销售报表（`/sales-report`）：汇总卡、趋势图、档口占比、菜品明细、半成品换算规则、
  退款、企微推送、文字导出
- 配方 SOP（`/workbench/kitchen/recipe`、`/workbench/kitchen/recipe/detail`、
  `/workbench/kitchen/recipe/manage`、`/workbench/kitchen/recipe/print`、
  `/workbench/kitchen/recipe/qr`，工作台「后勤」组）：
  岗位列表、配方阅读器（含 TOC/搜索/字号/主题/用量缩放）、配方管理编辑器、打印预览、
  岗位二维码；阅读面两种身份都进得去（扫码看岗位配方的路子保留），管理页只给超级管理员
- 企微推送（`/wecom-push`）：Webhook 管理、推送任务管理、消息预览与立即发送、发送记录
- 备货计划（`/workbench/kitchen/prep-plan`，工作台「后勤」组；员工这一档只读）：
  一键生成执行清单、档口执行板、辅助信息
- 实时日志（`/logs`）：实时跟踪 / 历史查询、级别与 logger 过滤、统计面板
- 卫生管理端（`/workbench/floor/zones`、`/workbench/floor/daily`、
  `/workbench/floor/attire`、`/workbench/floor/deep-clean`、`/workbench/floor/fix`、
  `/workbench/floor/boards`、`/workbench/floor/data`，工作台「现场」组；花名册在人事组
  `/workbench/hr/roster`）：
  工作区、日常与专项计划、整改单、红黑榜、卫生数据台账（浏览 / 导出 / 清理）、
  仪容仪表（按人拍，名单由排班给）
- 排班（店长端工作台「人事」组 `/workbench/hr/calendar`、`/workbench/hr/inbox` 与
  `/workbench/hr/shifts`，员工端「我的」组 `/workbench/me/today` 与
  `/workbench/me/month`）：
  固定班次、工作区默认、轮转周期，由系统展开成月历；店长能点某天的某人**就地改那一天**
  （票 07：换班次 / 改成休 / 只换区，只动这一天、规则一个字不改，改过的日子带青点、
  撤掉覆盖就回到规则）；员工在「今天」页**提请假**（一天或一段，没批之前能撤回），
  店长在「待办」页批（票 08：批之前就写着那天每个班次还剩几个人，人手够不够只提示不拦；
  批了那几天变成请假、跟本来就休分开说，驳回排班一个字不变）；**换班**也在这两页上
  （票 09：员工指定同事与哪一天提一条，对方先在手机上同意或拒绝，**两边都点了才轮到店长**，
  批了那两个人当天的班对调、工作区各跟着自己的新班次走）；班次本身也是店长在
  **班次表**页上维护的（票 11：加一条、改名字、调显示顺序、启用停用 —— 停用后新排班不再
  用它、**历史排班照旧显示**，还有人的轮转里排着它时会拦下来说清人数；排过班的删不掉，
  删除只留给刚建错的那条。加第三个班次不需要改代码：月历图例、规则下拉、员工卡片都按
  N 个班次渲染）；
  员工端「今天」页是员工登录后的落点，「整月」页（票 06）
  按日历列出自己这个月的班别，两页都只读自己的班（卫生那块下一张票接到「今天」页上）
- 员工手机端（工作台「我的」组：`/workbench/me/today`、`/workbench/me/month`、
  `/workbench/me/clean`，另有 `/register`）：**员工入口是 `/login`** 的员工栏
  （一个面板两个 Tab，独立员工登录页早已删除）。旧书签 `/staff/today` 随员工端
  搬进工作台作废，给员工重发一次 `/login` 即可（登录后落到 `/workbench/me/today`）。
  独立员工会话、实时 nudge 刷新、离线重试；卫生那块也在同一套会话下。旧的
  `/staff/*`、`/today`、`/today/month`、`/hygiene/*`、`/scheduling*` 一律删除、不留别名
- 系统配置页（`/settings`）：POS 凭据、数据库凭据、备份中心、系统更新（版本检测 /
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

dev server 下**不注册** Service Worker：两份 worker 都是 `npm run build` 时生成的，dev 下
`/sw.js` 与 `/workbench/sw.js` 会落到 SPA history fallback 回一份 `index.html`（`text/html`），
浏览器按 MIME 直接拒注册。要验 PWA 行为（安装、更新提示）请用构建产物走后端 `:8000`。

鉴权沿用 Cookie Session：先在 `/login` 登录一次，浏览器 Cookie 对 `localhost`
同源共享，`fetch` 带 `credentials: 'include'`。

## 生产构建

```bash
npm run build        # 产出 dist/（含 sw.js、多角色 manifest 与 PWA 图标）
```

生产由 FastAPI 或 `deploy/` 反向代理托管。两份 Service Worker 都只缓存前端程序资源；
`/api/*`、`/ws/*` 和上传下载始终直连后端。页面在每次启动时检查一次新版本，
发现等待中的新 Worker 后显示全局更新提示，用户确认后才切换并刷新。

两个 App 各有一份清单与一个作用域内的 Worker：

- 管理端：`/pwa/manifests/admin.webmanifest`（`scope: /`）+ `/sw.js`
- 工作台：`/pwa/manifests/workbench.webmanifest`（`scope: /workbench`）+ `/workbench/sw.js`
  （该脚本由 FastAPI 带 `Service-Worker-Allowed: /workbench` 发出 —— 少了它浏览器不许
  脚本用它所在目录之上的 scope，而 `/workbench` 正是首页与 `start_url`）

页面属于哪个 App 由 `src/utils/pwaManifest.js` 按路径判定（工作台前缀归工作台、
其余归管理端），清单、图标、主题色与注册哪个 Worker 都从这一条判据来。

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
                           # hygieneWorkFlow / hygieneMarkup / backupPoints / updateProgress /
                           # todayShift / leaveRequest / shiftTable（排班那三页的人话翻译）
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
    workbench/*.vue         # 工作台外壳 WorkbenchLayout / 首页「今天」WorkbenchHomeView /
                            # 越权落点 ForbiddenView
    recipe/*.vue            # RecipeStationsView / RecipeDetailView / RecipeManageView /
                            # RecipePrintView / RecipeQrView
    hygiene/*.vue           # 现场组 8 页 + 员工端卫生待办 HygieneHomeView / 注册页 +
                            # 三个 Layout（AdminLayout / StaffAuthLayout 等）
    scheduling/*.vue        # 排班日历 + 待办 + 班次表（店长端，独立系统，不 import 卫生）
    today/*.vue             # 员工端「今天」（含请假与换班表单）+「整月」两页（登录后的落点，
                            # 读 /api/scheduling/me、/me/month 与 /me/requests、
                            # /me/colleagues 与 /me/swaps*（换班选人、提、回应））
```

## 已知限制 / 后续可优化项

- 主 vendor chunk（Vue/Pinia/Router/ECharts 合并）约 1.1MB，未做手动分包
  （`build.rollupOptions.output.manualChunks`），首屏加载可进一步优化
- 登录、员工注册与系统配置页已是 SPA 路由（`/login`、`/register`、`/settings`），不再使用独立
  静态 HTML；首次初始化（建管理员账号）在 `/login` 的管理员栏里，系统配置页不是初始化页
