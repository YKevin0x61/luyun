import { createRouter, createWebHistory } from 'vue-router'
import { isLoggedIn } from '../utils/authStatus'
import { buildLoginNextFromRoute } from '../utils/loginNext'
import { staffSessionState } from '../utils/hygieneStaff'
import { pageMeta, pageTitle } from './pageRoutes.js'

const HygieneAdminLayout = () => import('../views/hygiene/HygieneAdminLayout.vue')
const HygieneStaffAuthLayout = () => import('../views/hygiene/HygieneStaffAuthLayout.vue')
const SchedulingLayout = () => import('../views/scheduling/SchedulingLayout.vue')
const WorkbenchLayout = () => import('../views/workbench/WorkbenchLayout.vue')

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

/** 工作台「人事」那一组（月历 / 待办 / 班次表 / 花名册）的页面：套人事壳
 *  （`SchedulingLayout`：窄栏 + 本组四页的导航 + 进「现场」组的门），页面本体在子记录里。
 *
 *  花名册是**人事**页（票面口径），所以从卫生那八页里搬了过来 —— 它仍在卫生的数据库与
 *  接口上跑，只是 URL 与外壳跟着分组走。两组的壳各自留一扇互跳的门（见两个 Layout）。 */
function workbenchHrPage(path, name, loader) {
  return {
    path,
    component: SchedulingLayout,
    meta: pageMeta(path),
    children: [{ path: '', name, component: loader }],
  }
}

/** 工作台「我的」那一组（员工端）的页面：套工作台外壳（顶上一条窄栏 + 按身份过滤的
 *  导航），页面本体还在子记录里。
 *
 *  `staffProbe: false` 三页一致：页面自己那一次请求本来就分得清 401 与断网，不必先陪
 *  守卫白等一次员工会话探针超时（弱网下最多 4 秒，而这是员工手机上每次导航都要付的）。
 *  **它只免掉员工会话那一次探针**：守卫仍会用管理端会话判一次身份 —— 管理端 cookie
 *  在这类页上只会被员工接口 401，不先拦下来就会落成员工登录页（见守卫里那段注释）。
 *  票 04 时只有两页带它 —— 同前缀两套行为正是 navigation-audit 条目 7 记的问题，
 *  这次收齐。
 *  `realtime: true`：店长改了排班 / 派了活，页面上的格子与待办要跟着变。 */
function workbenchStaffPage(path, name, loader) {
  return {
    path,
    component: WorkbenchLayout,
    meta: { ...pageMeta(path), staffProbe: false, realtime: true },
    children: [{ path: '', name, component: loader }],
  }
}

/** 工作台「产品」那一组（票 07：配方；票 08 的备货计划同组）里套工作台外壳的页面：
 *  壳是 `WorkbenchLayout`（手机档一行页头 + 按身份过滤的底栏），页面本体在子记录里。
 *
 *  **2026-10-08 起这一组的六页全走这个工厂**：阅读 / 打印 / 印码那三页原来不在其中
 *  （理由见下面路由那一段），用户裁定一并套上 —— 组内导航与配色跟同组其余页面对齐。
 *  六页在页面清单里都记 `standalone: true`（那条的语义是「不渲染管理后台那条导航」）。 */
function workbenchKitchenPage(path, name, loader) {
  return {
    path,
    component: WorkbenchLayout,
    meta: pageMeta(path),
    children: [{ path: '', name, component: loader }],
  }
}

const routes = [
  { path: '/', name: 'dashboard', component: () => import('../views/DashboardView.vue'), meta: pageMeta('/') },
  { path: '/admin', name: 'admin', component: () => import('../views/AdminView.vue'), meta: pageMeta('/admin') },
  { path: '/sales-report', name: 'sales-report', component: () => import('../views/SalesReportView.vue'), meta: pageMeta('/sales-report') },
  { path: '/logs', name: 'logs', component: () => import('../views/LogsView.vue'), meta: pageMeta('/logs') },
  { path: '/wecom-push', name: 'wecom-push', component: () => import('../views/WecomPushView.vue'), meta: pageMeta('/wecom-push') },
  // ── 工作台（排班 + 卫生合并成一个子系统，2026-10-04）──────────────────────
  // 票 05 起页面按组落在 URL 上，两组各有自己的壳：
  //   人事 `/workbench/hr/*`（月历 / 待办 / 班次表 / 花名册）套 `SchedulingLayout`；
  //   现场 `/workbench/floor/*`（卫生七页）套 `HygieneAdminLayout`（自带 rail）。
  // 两个壳都靠 `meta.standalone` 让 `App.vue` 不渲染后台导航 —— 这个标记从页面清单派生。
  // 两组的落点（`/workbench/hr/calendar`、`/workbench/floor/daily`）写在 `utils/workbenchCopy.js`，
  // 两个壳与页内跳转都从那一份常量走，不各写一遍。
  //
  // **票 05 之前那批平铺地址（`/workbench/inbox`、`/workbench/roster` …）随这一票作废**：
  // 不给别名、不做重定向、自然 404。`?next=` 里可能还存着它们（手机上存着的旧链接），
  // 由 `utils/loginNext.js` 的 `LEGACY_NEXT_PATHS` 在入口换成新地址 —— 不给老路径留路由，
  // 那样迟早会漂成两套（`/hygiene/*`、`/scheduling*`、`/staff/*` 走的是同一条路）。
  //
  // 这一段里的路径**都要写成字面量**（或没有插值的模板字符串）：`tests/test_spa_page_routes.py`
  // 按源码解析这些注册写法 —— 变量路径与插值模板它解析不出来，会当场报错而不是静默漏页。
  workbenchHrPage('/workbench/hr/calendar', 'workbench-hr-calendar', () => import('../views/scheduling/SchedulingCalendarView.vue')),
  // 待办（票 08）：店长批请假的地方。从月历页底下那根「请假等着批」的条进来。
  workbenchHrPage('/workbench/hr/inbox', 'workbench-hr-inbox', () => import('../views/scheduling/SchedulingInboxView.vue')),
  // 班次表（票 11）：加一条、改名字、调显示顺序、启用停用、删掉建错的那条。
  workbenchHrPage('/workbench/hr/shifts', 'workbench-hr-shifts', () => import('../views/scheduling/SchedulingShiftsView.vue')),
  // 花名册（票 05 从卫生那八页搬进人事组）：员工注册审核、卫生权限档位、那两班的口径，
  // 还有发给新店员的那张入口码。页面本体一个字没动，只是换了 URL 与外壳。
  workbenchHrPage('/workbench/hr/roster', 'workbench-hr-roster', () => import('../views/hygiene/HygieneRosterView.vue')),
  hygieneAdminPage('/workbench/floor/zones', 'workbench-floor-zones', () => import('../views/hygiene/HygieneZonesView.vue')),
  hygieneAdminPage('/workbench/floor/daily', 'workbench-floor-daily', () => import('../views/hygiene/HygieneDailyView.vue')),
  // 仪容仪表（票 12）：按人拍，名单由排班给（休假的与没排到的不在表上）。
  hygieneAdminPage('/workbench/floor/attire', 'workbench-floor-attire', () => import('../views/hygiene/HygieneAttireView.vue')),
  hygieneAdminPage('/workbench/floor/deep-clean', 'workbench-floor-deep-clean', () => import('../views/hygiene/HygieneDeepCleanView.vue')),
  hygieneAdminPage('/workbench/floor/fix', 'workbench-floor-fix', () => import('../views/hygiene/HygieneFixView.vue')),
  hygieneAdminPage('/workbench/floor/boards', 'workbench-floor-boards', () => import('../views/hygiene/HygieneBoardsView.vue')),
  hygieneAdminPage('/workbench/floor/data', 'workbench-floor-data', () => import('../views/hygiene/HygieneDataView.vue')),
  // 卫生趋势（2026-10-05 用户裁定）：现场组的第八页。前面七页全是「当下」，数据页是
  // 台账与导出 —— 这一页回答「这周比上周好还是差」。
  hygieneAdminPage('/workbench/floor/trend', 'workbench-floor-trend', () => import('../views/hygiene/HygieneTrendView.vue')),
  // ── 工作台 · 后勤（配方票 07；备货计划票 08）────────────────────────────────
  // 配方从独立域 `/recipe*` 搬进「后勤」组，同时**阅读面从免登录改成要登录**
  // （ADR 0092 里那个推翻既有刻意设计的动作）。扫码看岗位配方的路子保留：扫码 →
  // 未登录 → 登录页默认开员工栏 → 回到那条配方（回跳白名单在 `utils/loginNext.js`）。
  //
  // **六页一律套 `WorkbenchLayout`**（都不是管理后台那条导航，所以清单里一律 `standalone: true`）。
  // 2026-10-08 用户裁定：阅读 / 打印 / 印码这三页原来**自带顶栏底栏、不套外壳**（那时的理由是
  // "扫码进来的人是来看那一条配方的"），代价是既没有组内导航、也不吃工作台那套深青墨，
  // 跟同组其余页面同屏就是两种观感。现在一并收进外壳；外壳自己带 `no-print`
  // （`views/workbench/WorkbenchLayout.vue`），所以「配方打印」那一页打印时顶栏底栏不会被打进去。
  // 旧的 `/recipe*` 一条都不留（自然 404，不给别名、不做重定向）。
  workbenchKitchenPage('/workbench/kitchen/recipe', 'recipe-stations', () => import('../views/recipe/RecipeStationsView.vue')),
  workbenchKitchenPage('/workbench/kitchen/recipe/manage', 'recipe-manage', () => import('../views/recipe/RecipeManageView.vue')),
  workbenchKitchenPage('/workbench/kitchen/recipe/detail', 'recipe-detail', () => import('../views/recipe/RecipeDetailView.vue')),
  workbenchKitchenPage('/workbench/kitchen/recipe/print', 'recipe-print', () => import('../views/recipe/RecipePrintView.vue')),
  workbenchKitchenPage('/workbench/kitchen/recipe/qr', 'recipe-qr', () => import('../views/recipe/RecipeQrView.vue')),
  // 备货计划（票 08）：从管理后台的 `/prep-plan` 搬进「后勤」组，**与配方列表同一个形态**
  // ——套工作台外壳（顶栏那条窄栏 + 按身份过滤的导航），跟随组里的落点常量走（`links` 里
  // 那一条，页面清单只负责把这一页登记成 `kitchen` / `both`）。它进工作台只是换位置与统一
  // 导航，**不扩权**：读接口走「任一身份」门、写接口仍只认管理端，员工这一档页面只读
  // （`composables/usePrepPlanAdmin.js`）。旧 `/prep-plan` 从路由里删干净（自然 404，
  // 不给别名、不做重定向；`?next=` 里的老地址由 `utils/loginNext.js` 换成新地址）。
  workbenchKitchenPage('/workbench/kitchen/prep-plan', 'prep-plan', () => import('../views/PrepPlanView.vue')),
  // 子应用根（票 06）：就是「今天」首页 —— 按身份用已有接口聚合今日摘要，只做分流与
  // 摘要、不做业务动作。它套**工作台外壳**（`WorkbenchLayout`：顶栏一条 + 按身份过滤的
  // 导航），跟「我的」那三页同一个壳；月历仍自己占一行（人事组的落点），两行不再指
  // 同一个页面组件。`audience: both` 与页面墙对 `/workbench` 的判定同一口径（任一会话）。
  {
    path: '/workbench',
    component: WorkbenchLayout,
    meta: pageMeta('/workbench'),
    children: [
      { path: '', name: 'workbench', component: () => import('../views/workbench/WorkbenchHomeView.vue'), meta: pageMeta('/workbench') },
    ],
  },
  // ── 工作台 · 我的（员工端，票 03）──────────────────────────────────────────
  // 三页从 `/staff/*` 搬进 `/workbench/me/*`：**只改前缀与名字**，页内结构与那五个
  // tab 一个不动。旧前缀连带 `main.py` 里那条裸 `/staff` 免墙条目一起删掉、不留别名
  // （旧书签自然 404，spec 故事 48）。员工登录后的落点、登录回跳白名单、花名册页那张
  // 二维码、PWA 归属判据都从 `utils/staffPaths.js` 那一份常量走，不各写一遍。
  // 三页都套工作台外壳（导航里只有「我的」一格，票 07 / 08 往里加配方与备货计划）。
  workbenchStaffPage('/workbench/me/today', 'today', () => import('../views/today/TodayView.vue')),
  // 整月：从「今天」页那张排班卡的「整月」按钮进来，看自己这个月每天上什么班。
  workbenchStaffPage('/workbench/me/month', 'today-month', () => import('../views/today/TodayMonthView.vue')),
  // 加班与补钟（票 01）：员工自己提一笔、看自己的记录与月度净时长。管理端的审批与
  // 统计在票 02（`/workbench/hr/overtime`），店长在手机上的审批面在票 04 —— 这一页
  // 现在只认员工会话（`audience: staff`）。
  workbenchStaffPage('/workbench/me/overtime', 'me-overtime', () => import('../views/overtime/MeOvertimeView.vue')),
  // 卫生待办（员工那半）：五个 tab 与页内结构照旧。
  workbenchStaffPage('/workbench/me/clean', 'hygiene-home', () => import('../views/hygiene/HygieneHomeView.vue')),
  // 越权落点（票 03）：有会话、但这一页不是这个身份的 —— 一页说明 + 一颗回自己首页的
  // 按钮，不再静默改道（旧行为把员工换成 `/staff/today`，`?next=` 也丢了）。
  { path: '/workbench/forbidden', name: 'workbench-forbidden', component: () => import('../views/workbench/ForbiddenView.vue'), meta: pageMeta('/workbench/forbidden') },
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

// 「一个会话都没有」的落点（票 02）：登录页 + 原目标。身份不匹配走下面的
// `forbiddenRedirect` —— 两者分开正是票 03 的验收之一。
function loginRedirect(to) {
  return { path: '/login', query: { next: buildLoginNextFromRoute(to) } }
}

// 有会话但这不是他的页（票 03）：落明确的「无权访问」页，原目标一起带过去（那一页
// 据此说明这页属于谁）。与 `loginRedirect` 的分界就是「有没有会话」—— 一个会话都没有
// 时不该说「这不是你的页」，该让他先登录、登录后仍回原目标。
function forbiddenRedirect(to) {
  return { path: '/workbench/forbidden', query: { next: buildLoginNextFromRoute(to) } }
}

// 全站登录守卫（票 02）：判据是页面清单里的「允许的身份」三态（`meta.audience`），
// 不再是逐个路由手写的布尔标记。
//   admin → 只认管理端会话；staff → 只认员工会话；both → 任一会话有效即可
//   （工作台外壳就是这一档，与服务端页面墙对 `/workbench` 的判定同一口径）。
// 免登录（`meta.public`）优先于身份判定：public 是「不需要会话就能拿到页面壳」，
// `/register` 与配方阅读面在这一档，页面自己那次请求 401 时各自 replace 到
// `/login?next=<fullPath>`（见 `HygieneHomeView.leaveForStaffLogin`、`TodayView.load`）。
// 票 03 起工作台里的页一律不是 public（工作台是「进去要登录」的页面区）。
router.beforeEach(async (to) => {
  if (to.meta.public) return true

  const audience = to.meta.audience

  // 员工页（`/workbench/me/*`）：只认员工会话。
  if (audience === 'staff') {
    // **管理端会话先判**，而且要判在 `staffProbe` 短路之前：这类页拿管理端 cookie 去
    // 请求员工接口必然 401，页面自己那条 401 兜底会把人送去 `/login?next=<员工页>`
    // —— 落成**员工登录页**，与 `forbiddenRedirect` 的口径打架（D3：管理端打开
    // `/workbench/me/today` 看到的是手机号表单，页面上还一个出口都没有）。
    // 有会话但身份不匹配是 forbidden 那一档，不是 login 那一档，所以这一条不能参与
    // 「跳过探针」——跳过的只是**员工会话**探针（让员工在弱网下少等一次超时），
    // 不是「这页该不该给这个身份看」的判定。
    if (await isLoggedIn()) {
      // 两套 cookie 都在时仍按员工身份放行：同浏览器双会话是店里那台共用电脑的常态，
      // 员工会话本人就是这把钥匙（票 04 的「只降不升」把显示交给切换器，不管授权）。
      if ((await staffSessionState()) !== 'unauthenticated') return true
      return forbiddenRedirect(to)
    }
    // 页面自己会拉数据的（`staffProbe: false`）跳过员工会话探针：那一次请求本来就分得清
    // 401 与断网，探针只是让员工在白屏前多等一次超时。
    if (to.meta.staffProbe === false) return true
    // 网络不明（断网 / 后端刚重启 / 超时）时放行到页面：那里会显示"网络不好，
    // 正在重试"并退避重试。把这种情况也判成未登录，弱网下就会把员工反复甩到登录页。
    if ((await staffSessionState()) !== 'unauthenticated') return true
    return loginRedirect(to)
  }

  if (await isLoggedIn()) return true

  const staff = await staffSessionState()
  if (staff !== 'unauthenticated') {
    // `both`：工作台外壳对两种身份都开。
    if (audience === 'both') return true
    // 员工会话进店长专属页（票 03 的核心那一条）：落「无权访问」页。
    if (audience === 'admin') return forbiddenRedirect(to)
  }

  // 一个会话都没有、会话过期、以及认不出的 audience（fail-closed：宁可多问一次
  // 登录，也不静默放行一页）都落这里。
  return loginRedirect(to)
})

export default router
