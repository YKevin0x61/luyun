// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'

// 票 03：「无权访问」页（`/workbench/forbidden`）—— 越权落点，不是登录落点。
// 只断言外部行为：给出会话组合与目标路径，页面**显示什么**、按钮**点去哪**。
// 身份从两套会话的真实探测读出来（与守卫同一套口径），页面不做授权判断。
//
// 文案与去向对着页面清单写（目标那一页的身份来自清单那一行），所以这里也按清单里的
// 标题断言：`/workbench/floor/daily` 是「日常验收」（店长的页），`/workbench/staff/today`
// 是「我的」（员工的页；页面清单里那一行的 `title` 已从「今天」收口成「我的」）。

const SESSION_ROUTES = [
  '/workbench/forbidden',
  '/workbench',
  '/workbench/staff/today',
  '/workbench/staff/month',
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
    if (path.includes('/api/hygiene/staff/session')) {
      return jsonResponse({ authenticated: true, employee: { name: '张三' } })
    }
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: false, initialized: true })
    return jsonResponse({}, 404)
  })
}

/** 店里那台共用电脑：只有管理端会话。 */
function adminOnlyFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/hygiene/staff/session')) {
      return jsonResponse({ authenticated: false, employee: null })
    }
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: true, initialized: true })
    return jsonResponse({}, 404)
  })
}

/** 手机上的管理员：只有员工会话，且勾了某项管理能力（`staff-admin` 档）。 */
function staffAdminFetch(caps = ['fix']) {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/hygiene/staff/session')) {
      return jsonResponse({ authenticated: true, employee: { name: '彭灼洪', admin_caps: caps } })
    }
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: false, initialized: true })
    return jsonResponse({}, 404)
  })
}

/** 两套都没有（会话就在这一页上过期了）。 */
function noSessionFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/hygiene/staff/session')) {
      return jsonResponse({ authenticated: false, employee: null })
    }
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
    const { wrapper, router } = await mountForbidden('/workbench/floor/daily')

    expect(wrapper.text()).toContain('无权访问')
    expect(wrapper.text()).toContain('超级管理员')
    // 目标页的名字取自页面清单，不是我在这儿再写一份路径→名字的表。
    expect(wrapper.text()).toContain('日常验收')
    expect(wrapper.text()).toContain('员工')

    const button = wrapper.get('button')
    expect(button.text()).toBe('回我的首页')
    await button.trigger('click')
    await flushPromises()

    // 2026-10-14 起员工默认落点就是工作台首页「今天」（`STAFF_ENTRY_PATH`）。
    expect(router.currentRoute.value.path).toBe('/workbench')
  })

  it('员工进要「人事管理」能力的页：说没开这一项，不说「这是店长用的页」', async () => {
    // 2026-11-02 请假审批全链路审查的发现 2：`/workbench/hr/inbox` 靠 `staffCap: schedule`
    // 放行，隔壁同样是员工的同事勾了这一项就进得来 —— 拦住的是「没勾」，不是「身份不对」。
    const { wrapper } = await mountForbidden('/workbench/hr/inbox')

    expect(wrapper.text()).toContain('无权访问')
    expect(wrapper.text()).toContain('要「人事管理」权限')
    expect(wrapper.text()).toContain('请找超级管理员在花名册里勾上')
    expect(wrapper.text()).toContain('还没开「人事管理」')
    // 旧的「这是店长（超级管理员）用的页面」不再出现：按那句员工会去申请升成店长。
    expect(wrapper.text()).not.toContain('是超级管理员在电脑端用的页面')
  })

  it('票 04：员工进印码页同理 —— 说清那是店长的页，仍留一条回自己首页的路', async () => {
    // 印码页（生成岗位二维码、印出来贴到岗位上）票 04 起是 `admin` 那一档：员工手输 URL
    // 被守卫拦到这里，看到的是**解释**（不是静默改道），按钮回自己的首页。
    const { wrapper, router } = await mountForbidden('/workbench/kitchen/recipe/qr')

    expect(wrapper.text()).toContain('无权访问')
    expect(wrapper.text()).toContain('超级管理员')
    expect(wrapper.text()).toContain('岗位二维码')

    const button = wrapper.get('button')
    expect(button.text()).toBe('回我的首页')
    await button.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/workbench')
  })

  it('管理员进没有手机入口的管理页：只说「超级管理员在电脑端用的页面」，不指去卫生页', async () => {
    // 2026-11-10 走查：配方管理页对管理员也写「你的复核工作在手机端『卫生』页里做」，
    // 但那一页跟卫生复核无关，这句会把人指去无关页面。现场组（floor/*）才保留这句。
    vi.stubGlobal('fetch', staffAdminFetch())
    const { wrapper } = await mountForbidden('/workbench/kitchen/recipe/manage')

    expect(wrapper.text()).toContain('超级管理员在电脑端用的页面')
    expect(wrapper.text()).not.toContain('复核工作')
    expect(wrapper.text()).toContain('管理员（员工账号 · 现场复核）')
  })

  it('管理员进现场页：仍保留「复核工作在手机端卫生页」这句', async () => {
    vi.stubGlobal('fetch', staffAdminFetch())
    const { wrapper } = await mountForbidden('/workbench/floor/daily')

    expect(wrapper.text()).toContain('超级管理员在电脑端用的页面')
    expect(wrapper.text()).toContain('你的复核工作在手机端「卫生」页里做')
  })

  it('超级管理员进员工页：说明这是员工手机端的页，按钮回工作台首页', async () => {
    vi.stubGlobal('fetch', adminOnlyFetch())
    const { wrapper, router } = await mountForbidden('/workbench/staff/today')

    expect(wrapper.text()).toContain('员工手机端')
    // IA 第二层（2026-10-19）：`/workbench/staff/today` 的清单标题从「我的今天」改成
    // 「我的」，无权页照着清单报页面名 —— 判据仍是「报出了是哪一页」，只是名字变了。
    expect(wrapper.text()).toContain('我的')
    expect(wrapper.text()).toContain('超级管理员')

    const button = wrapper.get('button')
    expect(button.text()).toBe('回工作台首页')
    await button.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/workbench')
  })

  it('会话已在 (这页上) 过期：不冒充身份，按钮去登录页', async () => {
    vi.stubGlobal('fetch', noSessionFetch())
    const { wrapper, router } = await mountForbidden('/workbench/floor/daily')

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
