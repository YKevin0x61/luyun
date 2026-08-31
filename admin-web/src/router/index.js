import { createRouter, createWebHistory } from 'vue-router'
import { buildLoginNextFromRoute } from '../utils/loginNext'

const RECIPE_READER_META = { public: true, standalone: true }

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
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'), meta: { standalone: true, public: true } },
  { path: '/setup', name: 'setup', component: () => import('../views/SetupView.vue'), meta: { standalone: true } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 登录态缓存：避免每次客户端导航都请求 /api/auth/status（登录/登出走整页跳转会重置本模块）。
const AUTH_STATUS_TTL_MS = 10000
let authStatusCache = null // { loggedIn: boolean, ts: number }

async function isLoggedIn() {
  const now = Date.now()
  if (authStatusCache && now - authStatusCache.ts < AUTH_STATUS_TTL_MS) {
    return authStatusCache.loggedIn
  }
  try {
    const resp = await fetch('/api/auth/status', { credentials: 'include' })
    const data = await resp.json()
    authStatusCache = { loggedIn: !!data.logged_in, ts: now }
    return authStatusCache.loggedIn
  } catch {
    // fail-closed：拿不到状态时按未登录处理，跳登录页
    return false
  }
}

// 全站登录守卫：public 路由（登录页、配方阅读面）放行；其余未登录跳 /login?next=
router.beforeEach(async (to) => {
  if (to.meta.public) return true
  if (await isLoggedIn()) return true
  return { path: '/login', query: { next: buildLoginNextFromRoute(to) } }
})

export default router
