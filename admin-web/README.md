# admin-web

全栈性能重构 阶段三：Admin 前端重写（Vite + Vue3 + Pinia），替代原 `public/*.html`
多页面方案。

## 迁移范围（已全部完成）

- 仪表盘（`/`）：汇总卡片、热销菜品、最新订单、档口进单速率图表、系统状态
- 数据管理（`/admin`）：通用表格 CRUD，适用于 `/api/admin/tables` 列出的全部表；
  含批量菜品分类弹窗、表结构管理（新增/删除列）
- 销售报表（`/sales-report`）：汇总卡、趋势图、档口占比、菜品明细、半成品换算规则、
  退款、企微推送、文字导出
- 配方 SOP（`/recipe`、`/recipe/detail`、`/recipe/manage`、`/recipe/print`、`/recipe/qr`）：
  岗位列表、配方阅读器（含 TOC/搜索/字号/主题/用量缩放）、配方管理编辑器、打印预览、
  岗位二维码
- 企微推送（`/wecom-push`）：Webhook 管理、推送任务管理、消息预览与立即发送、发送记录
- 备货计划（`/prep-plan`）：一键生成执行清单、档口执行板、辅助信息
- 实时日志（`/logs`）：实时跟踪 / 历史查询、级别与 logger 过滤、统计面板
- WebSocket 实时事件驱动刷新（订单/餐桌变化）
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
npm run build        # 产出 dist/；生产由 FastAPI 或 deploy/ 反向代理托管
```

## 目录结构

```
src/
  api/client.js            # fetch 封装（Cookie 会话、401 跳转登录页）
  stores/
    stations.js            # 档口常量（替代原硬编码 STATIONS_MAP）
  composables/
    useRealtime.js          # WebSocket 连接与重连（subscribe/unsubscribe/nudge）
    useDashboardData.js     # 仪表盘数据 nudge + pull 刷新
    useAdminTable.js        # 通用表格 CRUD + 表结构管理
    useSalesReport.js       # 销售报表数据与交互
    useSemiRules.js         # 半成品换算规则
    useLogs.js               # 实时日志 / 历史查询状态管理
    usePrepPlan.js           # 备货计划状态管理
    useWecomPush.js          # 企微推送状态管理
    useScopedStylesheet.js   # 按需加载/卸载独立页面样式（recipe.css）
  utils/
    dateRange.js
    recipeCore.js            # 配方 slugify/用量缩放等纯函数（移植自 recipe-core.js）
    salesReportText.js
  components/
    NavBar.vue
    dashboard/*.vue
    admin/*.vue               # DataTable、RowEditModal、ClassifyDishesModal、ColumnManageModal
    salesreport/*.vue
  views/
    DashboardView.vue
    AdminView.vue
    SalesReportView.vue
    LogsView.vue
    PrepPlanView.vue
    WecomPushView.vue
    recipe/*.vue              # RecipeStationsView / RecipeDetailView / RecipeManageView / RecipePrintView / RecipeQrView
```

## 已知限制 / 后续可优化项

- 主 vendor chunk（Vue/Pinia/Router/ECharts 合并）约 1.1MB，未做手动分包
  （`build.rollupOptions.output.manualChunks`），首屏加载可进一步优化
- 登录/初始设置已是 SPA 路由（`/login`、`/setup`），不再使用独立静态 HTML
