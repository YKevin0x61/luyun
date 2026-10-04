import { afterEach, describe, expect, it, vi } from 'vitest'

// 配置页「账号与会话」那一节的退出（票 10）：以前是 `window.location.href = '/login'`
// 整页重载（navigation-audit 条目 8）。现在走与后台导航 / 备份中心 / 工作台同一个实现：
// 客户端路由 + 带原目标。两步确认由页面层负责（那个不弹浏览器原生 confirm），
// 这里只钉「点了之后发什么请求、落到哪里」。

const apiGet = vi.fn()

vi.mock('../../api/client', () => ({
  api: {
    get: (...args) => apiGet(...args),
    post: vi.fn(),
  },
}))

const { useAccountSettings } = await import('../useAccountSettings.js')

function fakeRouter(fullPath = '/settings') {
  const replace = vi.fn(async () => {})
  return { currentRoute: { value: { fullPath } }, replace }
}

function makeHarness({ router = fakeRouter() } = {}) {
  const settings = useAccountSettings({
    showAlert: vi.fn(),
    clearAlert: vi.fn(),
    router,
  })
  return { ...settings, router }
}

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

describe('配置页的退出登录', () => {
  it('发登出请求，然后走客户端路由回登录页并带上当前页', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }))
    vi.stubGlobal('fetch', fetchMock)
    const { handleLogout, router } = makeHarness({ router: fakeRouter('/settings?section=account') })

    await handleLogout()

    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/auth/logout')).toHaveLength(1)
    expect(router.replace).toHaveBeenCalledWith({
      path: '/login',
      query: { next: '/settings?section=account' },
    })
  })
})
