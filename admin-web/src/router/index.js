import { createRouter, createWebHistory } from 'vue-router'
import { isLoggedIn } from '../utils/authStatus'
import { buildLoginNextFromRoute } from '../utils/loginNext'
import { staffSessionState } from '../utils/hygieneStaff'
import { pageMeta, pageTitle } from './pageRoutes.js'

const HygieneAdminLayout = () => import('../views/hygiene/HygieneAdminLayout.vue')
const HygieneStaffAuthLayout = () => import('../views/hygiene/HygieneStaffAuthLayout.vue')

// 页面清单（`./pageRoutes.json`）是唯一来源（票 01）：每条路由的「独立外壳 / 免登录」
// 标记与页面标题都从它派生。加一页先改那张表 —— 后端的页面路由清单与两张页面豁免表
// 也对着同一张表校验（`tests/test_spa_page_routes.py`），两边不会各说各话。
function hygieneAdminPage(path, name, loader) {
  return {
    path,
    component: HygieneAdminLayout,
    meta: pageMeta(path),
    children: [{ path: '', name, component: loader }],
  }
}

function hygieneStaffAuthPage(path, name, loader) {
  return {
    path,
    component: HygieneStaffAuthLayout,
    meta: { ...pageMeta(path), staffPageTitle: pageTitle(path) },
    children: [{ path: '', name, component: loader }],
  }
}

const routes = [
  { path: '/', name: 'dashboard', component: () => import('../views/DashboardView.vue'), meta: pageMeta('/') },
  { path: '/admin', name: 'admin', component: () => import('../views/AdminView.vue'), meta: pageMeta('/admin') },
  { path: '/recipe', name: 'recipe-stations', component: () => import('../views/recipe/RecipeStationsView.vue'), meta: pageMeta('/recipe') },
  { path: '/recipe/detail', name: 'recipe-detail', component: () => import('../views/recipe/RecipeDetailView.vue'), meta: pageMeta('/recipe/detail') },
  { path: '/recipe/manage', name: 'recipe-manage', component: () => import('../views/recipe/RecipeManageView.vue'), meta: pageMeta('/recipe/manage') },
  { path: '/recipe/print', name: 'recipe-print', component: () => import('../views/recipe/RecipePrintView.vue'), meta: pageMeta('/recipe/print') },
  { path: '/recipe/qr', name: 'recipe-qr', component: () => import('../views/recipe/RecipeQrView.vue'), meta: pageMeta('/recipe/qr') },
  { path: '/sales-report', name: 'sales-report', component: () => import('../views/SalesReportView.vue'), meta: pageMeta('/sales-report') },
  { path: '/logs', name: 'logs', component: () => import('../views/LogsView.vue'), meta: pageMeta('/logs') },
  { path: '/prep-plan', name: 'prep-plan', component: () => import('../views/PrepPlanView.vue'), meta: pageMeta('/prep-plan') },
  { path: '/wecom-push', name: 'wecom-push', component: () => import('../views/WecomPushView.vue'), meta: pageMeta('/wecom-push') },
  // ── 工作台（排班 + 卫生合并成一个子系统，2026-10-04）──────────────────────
  // 两组同住 `/workbench/*`：首页与待办 / 班次表套 `SchedulingLayout`（独立页，自带
  // 一条窄栏：‹ 后台 / 工作台 / 现场 / 实时点）；卫生那八页套 `HygieneAdminLayout`
  // （独立页，自带 rail）。两边的壳都靠 `meta.standalone` 让 `App.vue` 不渲染后台导航
  // —— 这个标记从页面清单派生，不再写在这里。
  //
  // 前缀搬家走的是"先并存、再迁移跳转、最后删旧的"三步（票 02/03/04）。**旧的
  // `/scheduling*` 与 `/hygiene/*` 已经删干净、不留别名** —— 用户拍板：门店手机上那些
  // 旧书签 404 是可以接受的。`?next=` 里可能还存着老地址，由 `utils/loginNext.js` 在
  // 入口处换成新前缀（不给老路径留路由，那样迟早会漂成两套）。
  //
  // 这一段里的路径**都要写成字面量**（或没有插值的模板字符串）：`tests/test_spa_page_routes.py`
  // 按源码解析这些注册写法 —— 变量路径与插值模板它解析不出来，会当场报错而不是静默漏页。
  hygieneAdminPage('/workbench/roster', 'workbench-roster', () => import('../views/hygiene/HygieneRosterView.vue')),
  hygieneAdminPage('/workbench/zones', 'workbench-zones', () => import('../views/hygiene/HygieneZonesView.vue')),
  hygieneAdminPage('/workbench/daily', 'workbench-daily', () => import('../views/hygiene/HygieneDailyView.vue')),
  hygieneAdminPage('/workbench/deep-clean', 'workbench-deep-clean', () => import('../views/hygiene/HygieneDeepCleanView.vue')),
  hygieneAdminPage('/workbench/fix', 'workbench-fix', () => import('../views/hygiene/HygieneFixView.vue')),
  hygieneAdminPage('/workbench/boards', 'workbench-boards', () => import('../views/hygiene/HygieneBoardsView.vue')),
  hygieneAdminPage('/workbench/data', 'workbench-data', () => import('../views/hygiene/HygieneDataView.vue')),
  // 仪容仪表（票 12）：按人拍，名单由排班给（休假的与没排到的不在表上）。
  hygieneAdminPage('/workbench/attire', 'workbench-attire', () => import('../views/hygiene/HygieneAttireView.vue')),
  {
    path: '/workbench',
    component: () => import('../views/scheduling/SchedulingLayout.vue'),
    meta: pageMeta('/workbench'),
    children: [
      { path: '', name: 'workbench', component: () => import('../views/scheduling/SchedulingCalendarView.vue'), meta: pageMeta('/workbench') },
      // 待办（票 08）：店长批请假的地方。从月历页底下那根「请假等着批」的条进来。
      // 跟首页同一扇门（管理端 cookie，没有 meta.public）。
      { path: '/workbench/inbox', name: 'workbench-inbox', component: () => import('../views/scheduling/SchedulingInboxView.vue'), meta: pageMeta('/workbench/inbox') },
      // 班次表（票 11）：加一条、改名字、调显示顺序、启用停用、删掉建错的那条。
      { path: '/workbench/shifts', name: 'workbench-shifts', component: () => import('../views/scheduling/SchedulingShiftsView.vue'), meta: pageMeta('/workbench/shifts') },
    ],
  },
  // 员工手机端的入口是「今天」页（票 05）：登录后落到这里，第一眼是自己的班。
  // 票 04 起员工端整体住在 `/staff/*`（今天 /staff/today、整月 /staff/month、
  // 卫生首页 /staff/clean），旧的 `/today`、`/today/month`、`/hygiene` 已删除且不留别名。
  // 卫生那张卡（票 10）与这颗实时开关一起到齐：`realtime: true` 是给人的页面显式打开的
  // （`App.vue` 只对**非 public** 的路由默认开），员工页是 public 的，不写就不连。
  // `staffProbe: false`：这一页自己那一次请求就分得清 401 与断网（见 TodayView 的
  // `load()`），不必先陪守卫白等一次探针超时（弱网下最多 4 秒）。
  { path: '/staff/today', name: 'today', component: () => import('../views/today/TodayView.vue'), meta: { ...pageMeta('/staff/today'), staffProbe: false, realtime: true } },
  // 整月（票 06）：从「今天」页那张排班卡的「整月」按钮进来，看自己这个月每天上什么班。
  // 跟 `/staff/today` 同一套 meta —— 同一扇门（员工的 cookie）、同样的 `staffProbe: false`，
  // 也订同一颗实时开关（店长改了某一天，这一页上的格子要跟着变）。
  { path: '/staff/month', name: 'today-month', component: () => import('../views/today/TodayMonthView.vue'), meta: { ...pageMeta('/staff/month'), staffProbe: false, realtime: true } },
  // 卫生首页（员工那半）：票 04 从 `/hygiene` 搬到 `/staff/clean` ——
  // `/hygiene*` 从此是管理端卫生页的领土（票 05 把八个页面搬进去）。
  { path: '/staff/clean', name: 'hygiene-home', component: () => import('../views/hygiene/HygieneHomeView.vue'), meta: { ...pageMeta('/staff/clean'), realtime: true } },
  // 员工登录页已并入 `/login` 的员工栏（票 03）：原 `/hygiene/login` 这条路由与
  // `HygieneLoginView.vue` 一起删掉了。
  // 员工自助注册（票 02）：公开页，从员工前缀里挪到顶层 /register，仍走同一套员工鉴权排印
  // 布局（浅色）。页面标题从页面清单来（票 01），不在这里再写一遍。
  hygieneStaffAuthPage('/register', 'register', () => import('../views/hygiene/HygieneRegisterView.vue')),
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'), meta: pageMeta('/login') },
  { path: '/settings', name: 'settings', component: () => import('../views/SetupView.vue'), meta: pageMeta('/settings') },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 未登录 / 会话过期 / 身份不匹配的统一落点：登录页 + 原目标。票 03 会把「有会话但
// 身份不匹配」那一支换成明确的「无权访问」页，未登录与会话过期的落点仍是这里。
function loginRedirect(to) {
  return { path: '/login', query: { next: buildLoginNextFromRoute(to) } }
}

// 全站登录守卫（票 02）：判据是页面清单里的「允许的身份」三态（`meta.audience`），
// 不再是逐个路由手写的布尔标记。
//   admin → 只认管理端会话；staff → 只认员工会话；both → 任一会话有效即可
//   （工作台外壳就是这一档，与服务端页面墙对 `/workbench` 的判定同一口径）。
// 免登录（`meta.public`）优先于身份判定：public 是「不需要会话就能拿到页面壳」，
// 三页员工手机端与 `/register` 都在这一档，页面自己那次请求 401 时各自 replace 到
// `/login?next=<fullPath>`（见 `HygieneHomeView.leaveForStaffLogin`、`TodayView.load`）。
router.beforeEach(async (to) => {
  // 免登录页（登录 / 注册 / 配方阅读面 / 员工手机三页）放行 —— 配方阅读面改成
  // 要登录是票 07 的事。
  if (to.meta.public) return true

  const audience = to.meta.audience

  // 员工页（票 03 起是 `/workbench/me/*`）：只认员工会话。
  if (audience === 'staff') {
    // 页面自己会拉数据的（`staffProbe: false`）跳过探针：那一次请求本来就分得清
    // 401 与断网，探针只是让员工在白屏前多等一次超时。
    if (to.meta.staffProbe === false) return true
    // 网络不明（断网 / 后端刚重启 / 超时）时放行到页面：那里会显示"网络不好，
    // 正在重试"并退避重试。把这种情况也判成未登录，弱网下就会把员工反复甩到登录页。
    if ((await staffSessionState()) !== 'unauthenticated') return true
    return loginRedirect(to)
  }

  if (await isLoggedIn()) return true
  if (audience === 'both' && (await staffSessionState()) !== 'unauthenticated') return true

  // 未登录、会话过期、身份不匹配，以及认不出的 audience（fail-closed：宁可多问一次
  // 登录，也不静默放行一页）都落这里。
  return loginRedirect(to)
})

export default router
