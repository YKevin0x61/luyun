import { createRouter, createWebHistory } from 'vue-router'
import { isLoggedIn } from '../utils/authStatus'
import { buildLoginNextFromRoute } from '../utils/loginNext'
import { staffSessionState } from '../utils/hygieneStaff'

const RECIPE_READER_META = { public: true, standalone: true }
const HYGIENE_STAFF_META = { public: true, standalone: true }
const HygieneAdminLayout = () => import('../views/hygiene/HygieneAdminLayout.vue')
const HygieneStaffAuthLayout = () => import('../views/hygiene/HygieneStaffAuthLayout.vue')

function hygieneAdminPage(path, name, loader) {
  return {
    path,
    component: HygieneAdminLayout,
    meta: { standalone: true },
    children: [{ path: '', name, component: loader }],
  }
}

function hygieneStaffAuthPage(path, name, loader, title) {
  return {
    path,
    component: HygieneStaffAuthLayout,
    meta: { ...HYGIENE_STAFF_META, staffPageTitle: title },
    children: [{ path: '', name, component: loader }],
  }
}

const routes = [
  { path: '/', name: 'dashboard', component: () => import('../views/DashboardView.vue') },
  { path: '/admin', name: 'admin', component: () => import('../views/AdminView.vue') },
  { path: '/recipe', name: 'recipe-stations', component: () => import('../views/recipe/RecipeStationsView.vue'), meta: RECIPE_READER_META },
  { path: '/recipe/detail', name: 'recipe-detail', component: () => import('../views/recipe/RecipeDetailView.vue'), meta: RECIPE_READER_META },
  { path: '/recipe/manage', name: 'recipe-manage', component: () => import('../views/recipe/RecipeManageView.vue') },
  { path: '/recipe/print', name: 'recipe-print', component: () => import('../views/recipe/RecipePrintView.vue'), meta: RECIPE_READER_META },
  { path: '/recipe/qr', name: 'recipe-qr', component: () => import('../views/recipe/RecipeQrView.vue'), meta: RECIPE_READER_META },
  { path: '/sales-report', name: 'sales-report', component: () => import('../views/SalesReportView.vue') },
  { path: '/logs', name: 'logs', component: () => import('../views/LogsView.vue') },
  { path: '/prep-plan', name: 'prep-plan', component: () => import('../views/PrepPlanView.vue') },
  { path: '/wecom-push', name: 'wecom-push', component: () => import('../views/WecomPushView.vue') },
  { path: '/scheduling', name: 'scheduling', component: () => import('../views/scheduling/SchedulingCalendarView.vue') },
  // 待办（票 08）：店长批请假的地方。从月历页底下那根「请假等着批」的条进来。
  // 跟 `/scheduling` 同一扇门（管理端 cookie，没有 meta.public）。
  { path: '/scheduling/inbox', name: 'scheduling-inbox', component: () => import('../views/scheduling/SchedulingInboxView.vue') },
  // 班次表（票 11）：加一条、改名字、调显示顺序、启用停用、删掉建错的那条。
  // 同样没有 meta —— 跟 `/scheduling`、`/scheduling/inbox` 一扇门（管理端 cookie）。
  { path: '/scheduling/shifts', name: 'scheduling-shifts', component: () => import('../views/scheduling/SchedulingShiftsView.vue') },
  hygieneAdminPage('/hygiene/roster', 'hygiene-roster', () => import('../views/hygiene/HygieneRosterView.vue')),
  hygieneAdminPage('/hygiene/zones', 'hygiene-zones', () => import('../views/hygiene/HygieneZonesView.vue')),
  hygieneAdminPage('/hygiene/daily', 'hygiene-daily', () => import('../views/hygiene/HygieneDailyView.vue')),
  hygieneAdminPage('/hygiene/deep-clean', 'hygiene-deep-clean', () => import('../views/hygiene/HygieneDeepCleanView.vue')),
  hygieneAdminPage('/hygiene/fix', 'hygiene-fix', () => import('../views/hygiene/HygieneFixView.vue')),
  hygieneAdminPage('/hygiene/boards', 'hygiene-boards', () => import('../views/hygiene/HygieneBoardsView.vue')),
  hygieneAdminPage('/hygiene/data', 'hygiene-data', () => import('../views/hygiene/HygieneDataView.vue')),
  // 仪容仪表（票 12）：按人拍，名单由排班给（休假的与没排到的不在表上）。
  hygieneAdminPage('/hygiene/attire', 'hygiene-attire', () => import('../views/hygiene/HygieneAttireView.vue')),
  // 员工手机端的入口是「今天」页（票 05）：登录后落到这里，第一眼是自己的班。
  // 票 04 起员工端整体住在 `/staff/*`（今天 /staff/today、整月 /staff/month、
  // 卫生首页 /staff/clean），旧的 `/today`、`/today/month`、`/hygiene` 已删除且不留别名。
  // 卫生那张卡（票 10）与这颗实时开关一起到齐：`realtime: true` 是给人的页面显式打开的
  // （`App.vue` 只对**非 public** 的路由默认开），员工页是 public 的，不写就不连。
  // `staffProbe: false`：这一页自己那一次请求就分得清 401 与断网（见 TodayView 的
  // `load()`），不必先陪守卫白等一次探针超时（弱网下最多 4 秒）。
  { path: '/staff/today', name: 'today', component: () => import('../views/today/TodayView.vue'), meta: { ...HYGIENE_STAFF_META, staffAuth: true, staffProbe: false, realtime: true } },
  // 整月（票 06）：从「今天」页那张排班卡的「整月」按钮进来，看自己这个月每天上什么班。
  // 跟 `/staff/today` 同一套 meta —— 同一扇门（员工的 cookie）、同样的 `staffProbe: false`，
  // 也订同一颗实时开关（店长改了某一天，这一页上的格子要跟着变）。
  { path: '/staff/month', name: 'today-month', component: () => import('../views/today/TodayMonthView.vue'), meta: { ...HYGIENE_STAFF_META, staffAuth: true, staffProbe: false, realtime: true } },
  // 卫生首页（员工那半）：票 04 从 `/hygiene` 搬到 `/staff/clean` ——
  // `/hygiene*` 从此是管理端卫生页的领土（票 05 把八个页面搬进去）。
  { path: '/staff/clean', name: 'hygiene-home', component: () => import('../views/hygiene/HygieneHomeView.vue'), meta: { ...HYGIENE_STAFF_META, staffAuth: true, realtime: true } },
  // 员工登录页已并入 `/login` 的员工栏（票 03）：原 `/hygiene/login` 这条路由与
  // `HygieneLoginView.vue` 一起删掉了。
  // 员工自助注册（票 02）：公开页，从员工前缀里挪到顶层 /register，仍走同一套员工鉴权排印
  // 布局（浅色）。
  hygieneStaffAuthPage('/register', 'register', () => import('../views/hygiene/HygieneRegisterView.vue'), '员工注册'),
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'), meta: { standalone: true, public: true } },
  { path: '/settings', name: 'settings', component: () => import('../views/SetupView.vue'), meta: { standalone: true } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 全站登录守卫：public 路由（登录页、配方阅读面、员工手机入口）放行；
// 员工卫生首页另查员工会话；其余未登录跳 /login?next=
router.beforeEach(async (to) => {
  if (to.meta.staffAuth) {
    // 页面自己会拉数据的（`staffProbe: false`）跳过探针：那一次请求本来就分得清
    // 401 与断网，探针只是让员工在白屏前多等一次超时。
    if (to.meta.staffProbe === false) return true
    const state = await staffSessionState()
    // 网络不明（断网 / 后端刚重启 / 超时）时放行到页面：那里会显示"网络不好，
    // 正在重试"并退避重试。把这种情况也判成未登录，弱网下就会把员工反复甩到登录页。
    if (state !== 'unauthenticated') return true
    // 员工未登录：落管理面板的员工栏，并把原目标带上（票 03）。`?next=` 落在员工端
    // 前缀内时面板会强制开员工栏；不带 next 的话员工登回来就丢了自己要去的那一页。
    return { path: '/login', query: { next: buildLoginNextFromRoute(to) } }
  }
  if (to.meta.public) return true
  if (await isLoggedIn()) return true
  return { path: '/login', query: { next: buildLoginNextFromRoute(to) } }
})

export default router
