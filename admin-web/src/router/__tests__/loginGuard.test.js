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
// - 标了 `public` 的页（`/register`、配方阅读面）守卫放行，页面自己那次请求分得清
//   401 与断网，401 时由页面把 `fullPath` 带回去（见本文件下半段真实挂载的那条）。
//
// 票 02 起判据换成页面清单里的「允许的身份」三态（`meta.audience`）。
// **票 03 起「有会话但身份不匹配」不再落登录页**：员工进店长专属页落
// `/workbench/forbidden?next=<原目标>`（说清这页是谁的 + 一颗回自己首页的按钮），
// 只有「一个会话都没有」才去 `/login`。员工三页则是 `staffProbe: false`：守卫放行、
// 落点由页面自己那一次 401 决定（三页一致，见 router 的 `workbenchStaffPage`）。

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
  it('员工页 /workbench/me/clean 放行：落点由页面自己那次 401 决定', async () => {
    // 三页现在是 `staffProbe: false`（守卫不先陪探针等一次，弱网下最多 4 秒），未登录时
    // `HygieneHomeView.leaveForStaffLogin` 拿到 401 再把 `fullPath` 带回 `/login`。
    const router = await freshRouter()
    await router.push('/workbench/me/clean')

    expect(router.currentRoute.value.path).toBe('/workbench/me/clean')
  })

  it('管理端卫生页 /workbench/floor/daily', async () => {
    const router = await freshRouter()
    await router.push('/workbench/floor/daily')

    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/workbench/floor/daily')
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

  it('员工页 /workbench/me/today 是显式例外：守卫放行，落点由页面自己那一次 401 决定', async () => {
    const router = await freshRouter()
    await router.push('/workbench/me/today')

    // 不是被守卫送到 /login —— meta.staffProbe=false（弱网下别让员工先陪守卫白等一次
    // 探针超时）。页面拿到 401 时自己 replace 到 /login?next=<fullPath>，见下半段。
    expect(router.currentRoute.value.path).toBe('/workbench/me/today')
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

  it('管理端页：员工会话不算数，落「无权访问」页并带上原目标（票 03，不再静默改道）', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())
    const router = await freshRouter()

    await router.push('/workbench/floor/daily')

    expect(router.currentRoute.value.path).toBe('/workbench/forbidden')
    expect(router.currentRoute.value.query.next).toBe('/workbench/floor/daily')
  })

  it('员工页：管理端会话不算数，同样落「无权访问」页（这类页没带 staffProbe 标记）', async () => {
    vi.stubGlobal('fetch', adminOnlyFetch())
    const router = await routerWith('/tmp/staff', { audience: 'staff' })

    await router.push('/tmp/staff')

    expect(router.currentRoute.value.path).toBe('/workbench/forbidden')
    expect(router.currentRoute.value.query.next).toBe('/tmp/staff')
  })

  it('「无权访问」页本身对两种身份都开：员工会话不被再拦一道（守卫不能自己绕圈）', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())
    const router = await freshRouter()

    await router.push('/workbench/forbidden?next=%2Fworkbench%2Ffloor%2Fdaily')

    expect(router.currentRoute.value.path).toBe('/workbench/forbidden')
    expect(router.currentRoute.value.query.next).toBe('/workbench/floor/daily')
  })

  it('有会话也会过期：管理端会话没了又只有员工会话时，管理端页落「无权访问」而不是登录页', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())
    const router = await freshRouter()

    await router.push('/admin')

    expect(router.currentRoute.value.path).toBe('/workbench/forbidden')
    expect(router.currentRoute.value.query.next).toBe('/admin')
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

describe('/workbench/me/today 真实挂载：未登录时页面自己把 fullPath 带到 /login', () => {
  it('页面那次请求 401 → /login?next=%2Fworkbench%2Fme%2Ftoday', async () => {
    vi.resetModules()
    const TodayView = (await import('../../views/today/TodayView.vue')).default

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/workbench/me/today', component: TodayView },
        { path: '/login', component: { template: '<div />' } },
      ],
    })
    await router.push('/workbench/me/today')
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
    expect(router.currentRoute.value.query.next).toBe('/workbench/me/today')
    wrapper.unmount()
  })

  // 票 12 收的 O2：这条链上**并发的 401** 才是真机上的形态 —— 页面一挂载就并行发几条
  // 请求（排班 / 申请 / 卫生），各自 catch 里都会调 `leaveForStaffLogin`。第一个把人送到
  // `/login?next=X`，第二个若再跳一次，就会把「已经是登录页的当前地址」当成目标包进去：
  // 真机实测的最终 URL 就是 `/login?next=/login?next=/workbench/me/today`。
  it('第二个 401 晚到（此刻已站在 /login 上）：URL 不套娃', async () => {
    vi.resetModules()
    const TodayView = (await import('../../views/today/TodayView.vue')).default

    // `/api/scheduling/me` 这条**挂住**，等第一条跳转落定之后再回 401 —— 复刻真机上
    // 「第二个 401 在导航之后才到」的时序；其余请求照 beforeEach 那份桩（卫生那条立刻 401）。
    let releaseSlowRequest
    const slowResponse = new Promise((resolve) => {
      releaseSlowRequest = () => resolve(jsonResponse({ detail: '需要员工登录' }, 401))
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        const path = String(url)
        if (path.includes('/api/scheduling/me') && !path.includes('/me/')) return slowResponse
        if (path.includes('/api/hygiene/staff/me')) {
          return jsonResponse({ detail: '需要员工登录' }, 401)
        }
        if (path.includes('/api/auth/status')) {
          return jsonResponse({ logged_in: false, initialized: true })
        }
        return jsonResponse({}, 404)
      }),
    )

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/workbench/me/today', component: TodayView },
        { path: '/login', component: { template: '<div />' } },
      ],
    })
    await router.push('/workbench/me/today')
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
    expect(router.currentRoute.value.query.next).toBe('/workbench/me/today')

    // 迟到的那个 401 到达：这一跳**不该**发生。
    releaseSlowRequest()
    await flushPromises()
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/workbench/me/today')
    expect(router.currentRoute.value.fullPath.match(/next=/g)).toHaveLength(1)
    wrapper.unmount()
  })
})
