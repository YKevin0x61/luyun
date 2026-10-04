// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import NavBar from '../NavBar.vue'

// 后台导航条上的「退出登录」（票 10）：以前是 `window.location.href = '/login'` ——
// 整页重载、丢掉原目标（navigation-audit 条目 8）。现在走与其余四处同一个实现：
// 客户端路由 + `?next=<原页>`。这里只断言外部行为：发哪个请求、落到哪个路由。

const ROUTES = [
  { path: '/', component: { template: '<div />' } },
  { path: '/login', component: { template: '<div />' } },
  { path: '/admin', component: { template: '<div />' } },
  { path: '/settings', component: { template: '<div />' } },
  { path: '/sales-report', component: { template: '<div />' } },
  { path: '/wecom-push', component: { template: '<div />' } },
  { path: '/logs', component: { template: '<div />' } },
  { path: '/workbench', component: { template: '<div />' } },
]

async function mountNavBar(path = '/admin') {
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  await router.push('/')
  await router.push(path)
  await router.isReady()
  const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }))
  vi.stubGlobal('fetch', fetchMock)
  const wrapper = mount(NavBar, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router, fetchMock }
}

function logoutButton(wrapper) {
  return wrapper.findAll('button').find((button) => button.text() === '退出登录')
}

beforeEach(() => {
  vi.stubGlobal('confirm', () => true)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('后台导航的退出登录', () => {
  it('确认之后发登出请求，并客户端回登录页（带上当前页，整页不重载）', async () => {
    const { wrapper, router, fetchMock } = await mountNavBar('/workbench')

    await logoutButton(wrapper).trigger('click')
    await flushPromises()

    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/auth/logout')).toHaveLength(1)
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/workbench')
  })

  it('点「取消」什么都不做（误点不该把会话退掉）', async () => {
    const { wrapper, router, fetchMock } = await mountNavBar('/admin')
    vi.stubGlobal('confirm', () => false)

    await logoutButton(wrapper).trigger('click')
    await flushPromises()

    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/auth/logout')).toHaveLength(0)
    expect(router.currentRoute.value.path).toBe('/admin')
  })
})
