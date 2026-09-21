import { createRouter, createWebHistory } from 'vue-router'
import { isLoggedIn } from '../utils/authStatus'
import { buildLoginNextFromRoute } from '../utils/loginNext'
import { staffSessionState } from '../utils/hygieneStaff'

const RECIPE_READER_META = { public: true, standalone: true }
const HYGIENE_STAFF_META = { public: true, standalone: true, staffPhone: true }
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
  hygieneAdminPage('/hygiene-roster', 'hygiene-roster', () => import('../views/hygiene/HygieneRosterView.vue')),
  hygieneAdminPage('/hygiene-zones', 'hygiene-zones', () => import('../views/hygiene/HygieneZonesView.vue')),
  hygieneAdminPage('/hygiene-daily', 'hygiene-daily', () => import('../views/hygiene/HygieneDailyView.vue')),
  hygieneAdminPage('/hygiene-deep-clean', 'hygiene-deep-clean', () => import('../views/hygiene/HygieneDeepCleanView.vue')),
  hygieneAdminPage('/hygiene-fix', 'hygiene-fix', () => import('../views/hygiene/HygieneFixView.vue')),
  hygieneAdminPage('/hygiene-boards', 'hygiene-boards', () => import('../views/hygiene/HygieneBoardsView.vue')),
  { path: '/hygiene', name: 'hygiene-home', component: () => import('../views/hygiene/HygieneHomeView.vue'), meta: { ...HYGIENE_STAFF_META, staffAuth: true, realtime: true } },
  hygieneStaffAuthPage('/hygiene/login', 'hygiene-login', () => import('../views/hygiene/HygieneLoginView.vue'), '员工登录'),
  hygieneStaffAuthPage('/hygiene/register', 'hygiene-register', () => import('../views/hygiene/HygieneRegisterView.vue'), '员工注册'),
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'), meta: { standalone: true, public: true } },
  { path: '/setup', name: 'setup', component: () => import('../views/SetupView.vue'), meta: { standalone: true } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 全站登录守卫：public 路由（登录页、配方阅读面、员工手机入口）放行；
// 员工卫生首页另查员工会话；其余未登录跳 /login?next=
router.beforeEach(async (to) => {
  if (to.meta.staffAuth) {
    const state = await staffSessionState()
    // 网络不明（断网 / 后端刚重启 / 超时）时放行到页面：那里会显示"网络不好，
    // 正在重试"并退避重试。把这种情况也判成未登录，弱网下就会把员工反复甩到登录页。
    if (state !== 'unauthenticated') return true
    return { path: '/hygiene/login' }
  }
  if (to.meta.public) return true
  if (await isLoggedIn()) return true
  return { path: '/login', query: { next: buildLoginNextFromRoute(to) } }
})

export default router
