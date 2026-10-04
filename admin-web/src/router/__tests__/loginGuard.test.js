// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

// 票 09：登录守卫的端到端收口 —— 用**真实** `router/index.js`（不是复制一份 ROUTES），
// 断言未登录时 URL 最后落在哪、`?next=` 是什么。既有页面测试各自只扫自己那段源码，
// 没有一条真的让守卫跑一遍；这里补上，覆盖票面两条冒烟：
//
// - 未登录硬导航受保护页 → 守卫带 `next=<原目标>` 送到 `/login`；
// - `/staff/today`（`staffProbe: false`）例外：守卫**故意**放行，页面自己那次请求
//   分得清 401 与断网，401 时由页面把 `fullPath` 带回去（见本文件下半段真实挂载）。

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 每个用例都换一份崭新的 router 模块：`authStatus` 的 10 秒缓存和 router 单例都不跨用例。 */
async function freshRouter() {
  vi.resetModules()
  const mod = await import('../index.js')
  return mod.default
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url) => {
      const path = String(url)
      if (path.includes('/api/hygiene/staff/me')) {
        return jsonResponse({ detail: '需要员工登录' }, 401)
      }
      if (path.includes('/api/auth/status')) {
        return jsonResponse({ logged_in: false, initialized: true })
      }
      return jsonResponse({}, 404)
    }),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('vue-router 登录守卫：未登录时带 ?next= 送到 /login', () => {
  it('员工页 /staff/clean（走会话探针那条）', async () => {
    const router = await freshRouter()
    await router.push('/staff/clean')

    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/staff/clean')
  })

  it('管理端卫生页 /hygiene/daily', async () => {
    const router = await freshRouter()
    await router.push('/workbench/daily')

    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/workbench/daily')
  })

  it('系统配置页 /settings', async () => {
    const router = await freshRouter()
    await router.push('/settings')

    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/settings')
  })

  it('原目标带 query 时整条带过去（编码由 vue-router 负责）', async () => {
    const router = await freshRouter()
    await router.push('/logs?level=error')

    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/logs?level=error')
  })

  it('公开页 /login 与 /register 不被拦', async () => {
    const router = await freshRouter()
    await router.push('/register')
    expect(router.currentRoute.value.path).toBe('/register')

    await router.push('/login')
    expect(router.currentRoute.value.path).toBe('/login')
  })

  it('员工页 /staff/today 是显式例外：守卫放行，落点由页面自己那一次 401 决定', async () => {
    const router = await freshRouter()
    await router.push('/staff/today')

    // 不是被守卫送到 /login —— meta.staffProbe=false（弱网下别让员工先陪守卫白等一次
    // 探针超时）。页面拿到 401 时自己 replace 到 /login?next=<fullPath>，见下半段。
    expect(router.currentRoute.value.path).toBe('/staff/today')
  })
})

describe('/staff/today 真实挂载：未登录时页面自己把 fullPath 带到 /login', () => {
  it('页面那次请求 401 → /login?next=%2Fstaff%2Ftoday', async () => {
    vi.resetModules()
    const TodayView = (await import('../../views/today/TodayView.vue')).default

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/staff/today', component: TodayView },
        { path: '/login', component: { template: '<div />' } },
      ],
    })
    await router.push('/staff/today')
    await router.isReady()

    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(TodayView, {
      global: {
        plugins: [router, pinia],
        stubs: {
          ConfirmDialog: true,
          HygieneLiveCamera: true,
          HygieneStandardOverlay: true,
        },
      },
    })
    await flushPromises()
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/staff/today')
    wrapper.unmount()
  })
})
