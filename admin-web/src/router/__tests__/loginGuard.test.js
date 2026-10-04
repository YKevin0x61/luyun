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
// - 标了 `public` 的页（员工手机三页、`/register`、配方阅读面）守卫放行，页面自己
//   那次请求分得清 401 与断网，401 时由页面把 `fullPath` 带回去（见本文件下半段
//   真实挂载的那条）。
//
// 票 02 起判据换成页面清单里的「允许的身份」三态（`meta.audience`），未登录 / 会话
// 过期 / 身份不匹配都落 `/login?next=`；`/staff/clean` 不再被守卫的会话探针先拦一道
// —— 它和另外两页一样是 `public`，落点由页面自己那次 401 决定（与表里的 public 一致）。

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
  it('公开的员工页 /staff/clean 放行：落点由页面自己那次 401 决定', async () => {
    // 票 02 的收敛：这一页以前在守卫里先陪探针等一次（同前缀三页两套行为），现在和
    // `/staff/today` 一样走 public；未登录时 `HygieneHomeView.leaveForStaffLogin`
    // 拿到 401 再把 `fullPath` 带回 `/login`。
    const router = await freshRouter()
    await router.push('/staff/clean')

    expect(router.currentRoute.value.path).toBe('/staff/clean')
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

// 票 02：守卫的判据从布尔标记（`staffAuth` / `public`）换成清单里的「允许的身份」
// 三态（管理端 / 员工 / 两者）。这三条断言**行为**：给出会话组合与目标路径，URL 最后
// 落在哪。今天清单里还没有「两者且要登录」的页（票 03 的工作台首页才是），所以用
// `addRoute` 临时挂一条 —— 钉的是守卫对三态的反应，不是某一页。
describe('身份三态（票 02）：守卫按清单里的 audience 判定', () => {
  /** 手机上那个浏览器：只有员工会话（管理端未登录）。 */
  function staffOnlyFetch() {
    return vi.fn(async (url) => {
      const path = String(url)
      if (path.includes('/api/hygiene/staff/me')) {
        return jsonResponse({ employee: { name: '张三' } })
      }
      if (path.includes('/api/auth/status')) {
        return jsonResponse({ logged_in: false, initialized: true })
      }
      return jsonResponse({}, 404)
    })
  }

  /** 店里那台共用电脑的店长：只有管理端会话。 */
  function adminOnlyFetch() {
    return vi.fn(async (url) => {
      const path = String(url)
      if (path.includes('/api/hygiene/staff/me')) {
        return jsonResponse({ detail: '需要员工登录' }, 401)
      }
      if (path.includes('/api/auth/status')) {
        return jsonResponse({ logged_in: true, initialized: true })
      }
      return jsonResponse({}, 404)
    })
  }

  async function routerWith(path, meta) {
    const router = await freshRouter()
    router.addRoute({ path, component: { template: '<div />' }, meta })
    return router
  }

  it('「两者」页：只有员工会话也放行（工作台外壳对两种身份都开）', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())
    const router = await routerWith('/tmp/both', { audience: 'both' })

    await router.push('/tmp/both')

    expect(router.currentRoute.value.path).toBe('/tmp/both')
  })

  it('「两者」页：只有管理端会话也放行', async () => {
    vi.stubGlobal('fetch', adminOnlyFetch())
    const router = await routerWith('/tmp/both', { audience: 'both' })

    await router.push('/tmp/both')

    expect(router.currentRoute.value.path).toBe('/tmp/both')
  })

  it('管理端页：员工会话不算数，落 /login?next=<目标>（票 03 换成「无权访问」页）', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())
    const router = await freshRouter()

    await router.push('/workbench/daily')

    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/workbench/daily')
  })

  it('员工页：管理端会话不算数（两套会话各认各的门）', async () => {
    vi.stubGlobal('fetch', adminOnlyFetch())
    const router = await routerWith('/tmp/staff', { audience: 'staff' })

    await router.push('/tmp/staff')

    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/tmp/staff')
  })

  it('员工页：只有员工会话放行', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())
    const router = await routerWith('/tmp/staff', { audience: 'staff' })

    await router.push('/tmp/staff')

    expect(router.currentRoute.value.path).toBe('/tmp/staff')
  })

  it('认不出的 audience（meta 里没有）不静默当成 both：只有员工会话也落登录页', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())
    const router = await routerWith('/tmp/unknown', {})

    await router.push('/tmp/unknown')

    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/tmp/unknown')
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
