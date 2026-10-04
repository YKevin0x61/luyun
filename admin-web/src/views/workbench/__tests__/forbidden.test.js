// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'

// 票 03：「无权访问」页（`/workbench/forbidden`）—— 越权落点，不是登录落点。
// 只断言外部行为：给出会话组合与目标路径，页面**显示什么**、按钮**点去哪**。
// 身份从两套会话的真实探测读出来（与守卫同一套口径），页面不做授权判断。
//
// 文案与去向对着页面清单写（目标那一页的身份来自清单那一行），所以这里也按清单里的
// 标题断言：`/workbench/daily` 是「日常验收」（店长的页），`/workbench/me/today`
// 是「今天」（员工的页）。

const SESSION_ROUTES = [
  '/workbench/forbidden',
  '/workbench',
  '/workbench/me/today',
  '/workbench/me/month',
  '/login',
]

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 手机上那个浏览器：只有员工会话。 */
function staffOnlyFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/hygiene/staff/me')) return jsonResponse({ employee: { name: '张三' } })
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: false, initialized: true })
    return jsonResponse({}, 404)
  })
}

/** 店里那台共用电脑：只有管理端会话。 */
function adminOnlyFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/hygiene/staff/me')) return jsonResponse({ detail: '需要员工登录' }, 401)
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: true, initialized: true })
    return jsonResponse({}, 404)
  })
}

/** 两套都没有（会话就在这一页上过期了）。 */
function noSessionFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/hygiene/staff/me')) return jsonResponse({ detail: '需要员工登录' }, 401)
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: false, initialized: true })
    return jsonResponse({}, 404)
  })
}

/** 每个用例都换一份崭新的模块（`authStatus` 的 10 秒缓存不跨用例）。 */
async function mountForbidden(next) {
  vi.resetModules()
  const ForbiddenView = (await import('../ForbiddenView.vue')).default
  const router = createRouter({
    history: createMemoryHistory(),
    routes: SESSION_ROUTES.map((path) => ({ path, component: { template: '<div />' } })),
  })
  await router.push({ path: '/workbench/forbidden', query: next ? { next } : {} })
  await router.isReady()
  const wrapper = mount(ForbiddenView, { global: { plugins: [router] } })
  await flushPromises()
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  vi.stubGlobal('fetch', staffOnlyFetch())
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('「无权访问」页：说清这页是谁的，给一颗回自己首页的按钮', () => {
  it('员工进店长专属页：说明这页是店长用的，按钮回「我的」首页', async () => {
    const { wrapper, router } = await mountForbidden('/workbench/daily')

    expect(wrapper.text()).toContain('无权访问')
    expect(wrapper.text()).toContain('店长')
    // 目标页的名字取自页面清单，不是我在这儿再写一份路径→名字的表。
    expect(wrapper.text()).toContain('日常验收')
    expect(wrapper.text()).toContain('员工')

    const button = wrapper.get('button')
    expect(button.text()).toBe('回我的首页')
    await button.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/workbench/me/today')
  })

  it('超级管理员进员工页：说明这是员工手机端的页，按钮回工作台首页', async () => {
    vi.stubGlobal('fetch', adminOnlyFetch())
    const { wrapper, router } = await mountForbidden('/workbench/me/today')

    expect(wrapper.text()).toContain('员工手机端')
    expect(wrapper.text()).toContain('今天')
    expect(wrapper.text()).toContain('超级管理员')

    const button = wrapper.get('button')
    expect(button.text()).toBe('回工作台首页')
    await button.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/workbench')
  })

  it('会话已在 (这页上) 过期：不冒充身份，按钮去登录页', async () => {
    vi.stubGlobal('fetch', noSessionFetch())
    const { wrapper, router } = await mountForbidden('/workbench/daily')

    expect(wrapper.text()).toContain('未登录')

    const button = wrapper.get('button')
    expect(button.text()).toBe('去登录')
    await button.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/login')
  })

  it('目标不在清单里（手改地址栏）：不编造归属，仍给一条走得掉的路', async () => {
    const { wrapper } = await mountForbidden('/nowhere')

    expect(wrapper.text()).toContain('无权访问')
    expect(wrapper.text()).toContain('不是工作台里的页面')
    expect(wrapper.get('button').text()).toBe('回我的首页')
  })
})
