import { afterEach, describe, expect, it, vi } from 'vitest'
import { isLoggedIn, setAuthLoggedIn } from '../authStatus.js'
import { logoutAdminSession } from '../adminLogout.js'

// 管理端登出**只有这一处实现**（票 10：四处登出收敛成一条）。后台导航、配置页、
// 备份中心、工作台的退出按钮都调它 —— 这里钉住它对外可见的三件事：
// 发哪个请求、落到哪里（客户端路由 + 带原目标）、以及把会话缓存清掉。
// 断言的是行为，不是它内部怎么写的。

function fakeRouter(fullPath = '/admin') {
  const replace = vi.fn(async () => {})
  return { currentRoute: { value: { fullPath } }, replace }
}

function stubFetch(impl = async () => ({ ok: true })) {
  const fetchMock = vi.fn(impl)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('logoutAdminSession（管理端登出唯一实现）', () => {
  it('发登出请求，然后走客户端路由回登录页并带上原页', async () => {
    const fetchMock = stubFetch()
    const router = fakeRouter('/workbench/hr/roster?tab=2')

    await logoutAdminSession(router)

    expect(fetchMock).toHaveBeenCalledWith('/api/auth/logout', {
      method: 'POST',
      credentials: 'include',
    })
    expect(router.replace).toHaveBeenCalledWith({
      path: '/login',
      query: { next: '/workbench/hr/roster?tab=2' },
    })
  })

  it('调用方给了原页就用它的（身份派发那一路会显式带过来）', async () => {
    stubFetch()
    const router = fakeRouter('/settings')

    await logoutAdminSession(router, '/workbench/me/today')

    expect(router.replace).toHaveBeenCalledWith({
      path: '/login',
      query: { next: '/workbench/me/today' },
    })
  })

  it('请求失败也照样离开（会话可能已经没了，别把人留在无权的页上）', async () => {
    stubFetch(async () => {
      throw new TypeError('Failed to fetch')
    })
    const router = fakeRouter('/admin')

    await logoutAdminSession(router)

    expect(router.replace).toHaveBeenCalledWith({ path: '/login', query: { next: '/admin' } })
  })

  it('原页不是站内路径时不带 next（别把脏值塞进查询串）', async () => {
    stubFetch()
    const router = fakeRouter('/admin')

    await logoutAdminSession(router, 'https://evil.example/phish')

    expect(router.replace).toHaveBeenCalledWith({ path: '/login', query: {} })
  })

  it('清掉会话缓存：会话没了而 10 秒缓存还留着「已登录」，守卫会放行一页失效的会话', async () => {
    setAuthLoggedIn(true)
    stubFetch(async () => ({ ok: true, json: async () => ({ logged_in: false }) }))

    await logoutAdminSession(fakeRouter())

    expect(await isLoggedIn()).toBe(false)
  })
})
