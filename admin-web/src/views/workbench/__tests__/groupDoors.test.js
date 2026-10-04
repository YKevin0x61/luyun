// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { WORKBENCH_FIELD_HOME, WORKBENCH_HR_HOME } from '../../../utils/workbenchCopy.js'
import { workbenchPagesOf } from '../../../utils/workbenchNav.js'

const here = dirname(fileURLToPath(import.meta.url))
const HR_SHELL_SOURCE = readFileSync(join(here, '../../scheduling/SchedulingLayout.vue'), 'utf8')

// 票 05 的验收之一：**两组之间双向可达**（navigation-audit 条目 1 那个单向门）。
// 两个壳各自留一扇门，落点都是 `workbenchCopy.js` 里那份常量：
//   - 人事壳（`SchedulingLayout`）：本组四页的导航 + 「现场 ›」→ 现场落点；
//   - 现场壳（`HygieneAdminLayout`）：本组七页的 rail（**不再把花名册当现场页**）+
//     「人事」→ 人事落点。
// 断的是渲染出来的东西：有几条链接、href 是哪、当前项亮不亮。

/** 页面壳的替身：花名册那一页的根节点跟真页面一样叫 `.roster-page`（下面有一条断言
 *  看壳的作用域标记有没有落到它身上）。 */
function pageStub(page) {
  const cls = page.path === '/workbench/hr/roster' ? 'roster-page' : 'page-stub'
  return { path: page.path, component: { template: `<div class="${cls}">${page.title}</div>` } }
}

const ROUTES = [
  { path: '/', component: { template: '<div />' } },
  { path: '/login', component: { template: '<div />' } },
  // 壳自己的落点（首页 `both` + 我的那三页）：`WorkbenchLayout` 的退出入口要落回 `/login`，
  // 路由表里得有这两条。
  { path: '/workbench', component: { template: '<div />' } },
  ...workbenchPagesOf('me').map(pageStub),
  ...workbenchPagesOf('hr').map(pageStub),
  ...workbenchPagesOf('floor').map(pageStub),
]

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 两套会话都没有：这一层只看壳怎么画，身份探针给一份「没登录」就够。 */
function anonymousFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: false, initialized: true })
    if (path.includes('/api/hygiene/staff/me')) return jsonResponse({}, 401)
    if (path.includes('/api/auth/logout') || path.includes('/api/hygiene/staff/logout')) {
      return jsonResponse({})
    }
    return jsonResponse({}, 404)
  })
}

/** 每个用例用的那份 fetch 替身（退出入口那几条要查它发了什么请求）。 */
let fetchMock

/** 某个地址上的请求（退出那几条断言用它）。 */
function callsTo(path) {
  return fetchMock.mock.calls.filter(([url]) => url === path)
}

/** 挂一个壳（`hr` / `floor`），落在 `path` 上。每个用例换一份崭新的模块图：
 *  `authStatus` 有 10 秒状态缓存、工作台身份的记忆在 localStorage 里，跨用例会串。
 *
 *  `identity` 直接放进 store（退出入口按身份分派，而探针本身在别的文件里压过）。 */
async function mountShell(which, path, { identity = null } = {}) {
  vi.resetModules()
  localStorage.clear()
  const [{ default: Shell }, { useWorkbenchIdentityStore }] = await Promise.all([
    which === 'hr'
      ? import('../../scheduling/SchedulingLayout.vue')
      : import('../../hygiene/HygieneAdminLayout.vue'),
    import('../../../stores/workbenchIdentity'),
  ])

  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useWorkbenchIdentityStore()
  store.identity = identity
  store.sessions = { admin: identity === 'super', staff: identity === 'staff' }
  store.available = identity ? [identity] : []
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  await router.push(path)
  await router.isReady()

  const wrapper = mount(Shell, { global: { plugins: [router, pinia] }, attachTo: document.body })
  await flushPromises()
  return { wrapper, router, store }
}

beforeEach(() => {
  fetchMock = anonymousFetch()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.resetModules()
})

describe('人事壳（现场 → 人事 的那半扇门）', () => {
  it('本组四页都在顶栏导航里，链接指向新地址（不是平铺的旧地址）', async () => {
    const { wrapper } = await mountShell('hr', '/workbench/hr/calendar')

    const hrefs = wrapper.findAll('.sched-nav-item').map((item) => item.attributes('href'))
    expect(hrefs).toEqual(workbenchPagesOf('hr').map((page) => page.path))
    for (const href of hrefs) {
      expect(href.startsWith('/workbench/hr/')).toBe(true)
    }
  })

  it('「现场 ›」指到现场落点（两组互通，不再只能回后台）', async () => {
    const { wrapper } = await mountShell('hr', '/workbench/hr/calendar')

    const door = wrapper.get('.sched-field')
    expect(door.attributes('href')).toBe(WORKBENCH_FIELD_HOME)
    expect(door.text()).toContain('现场')
  })

  it('顶栏挂着身份切换器（票 04 交接：店长在自己的页面里也要看得到那两档）', async () => {
    const { wrapper } = await mountShell('hr', '/workbench/hr/calendar')

    expect(wrapper.find('.wb-id').exists()).toBe(true)
    expect(wrapper.get('.wb-id').text()).toContain('超级管理员')
  })

  it('高亮跟着新分组走：站在花名册上，花名册那一格亮', async () => {
    const { wrapper } = await mountShell('hr', '/workbench/hr/roster')

    const on = wrapper.findAll('.sched-nav-item').filter((item) => item.classes().includes('is-on'))
    expect(on).toHaveLength(1)
    expect(on[0].attributes('href')).toBe('/workbench/hr/roster')
  })

  it('花名册搬过来之后页面自己不带内边距：由这条壳补上（原来那份是卫生壳给的）', () => {
    // jsdom 不做布局，所以这一条按源码断：花名册的 `.roster-page` 只设了宽度与居中，
    // 内边距一直是卫生壳 `.hy-main` 给的；换成人事壳之后必须有这一条，否则页面上
    // 文字会贴着屏幕边（其余三页自带内边距，不吃这条）。
    expect(HR_SHELL_SOURCE).toMatch(
      /\.sched-shell > \.roster-page\s*\{[^}]*padding:\s*var\(--hy-page\)/,
    )
  })

  it('那条内边距真的落得上：花名册的根节点带着这条壳的作用域标记', async () => {
    // 上面那条只保证规则写在那儿；scoped CSS 要生效，还得让子页面的根节点带上壳的
    // 作用域标记（Vue 对子组件根节点就是这么做的）。这一条按渲染出来的 DOM 断它。
    const { wrapper } = await mountShell('hr', '/workbench/hr/roster')
    const scopeIds = (selector) => wrapper
      .get(selector)
      .element
      .getAttributeNames()
      .filter((name) => name.startsWith('data-v-'))

    const shellIds = scopeIds('.sched-top')
    expect(shellIds.length).toBeGreaterThan(0)
    expect(scopeIds('.roster-page').some((id) => shellIds.includes(id))).toBe(true)
  })
})

describe('现场壳（人事 → 现场 的那半扇门）', () => {
  it('rail 只有现场七页：花名册不在其中（它现在是人事页）', async () => {
    const { wrapper } = await mountShell('floor', '/workbench/floor/daily')

    const hrefs = wrapper.findAll('.hy-tab:not(.hy-tab-cross)').map((item) => item.attributes('href'))
    expect(hrefs).toEqual(workbenchPagesOf('floor').map((page) => page.path))
    expect(hrefs).not.toContain('/workbench/hr/roster')
  })

  it('「人事」那扇门指到人事落点（现场也能一步回人事）', async () => {
    const { wrapper } = await mountShell('floor', '/workbench/floor/daily')

    const door = wrapper.get('.hy-tab-cross')
    expect(door.attributes('href')).toBe(WORKBENCH_HR_HOME)
    expect(door.text()).toContain('人事')
    // rail 自己的名字：工作台 · 现场（它不是「卫生管理」那套旧牌子了）。
    expect(wrapper.get('.hy-tabbar').attributes('aria-label')).toBe('工作台 · 现场')
  })

  it('品牌链接指现场落点（不再是人事组的花名册）', async () => {
    const { wrapper } = await mountShell('floor', '/workbench/floor/boards')

    const brands = wrapper.findAll('.hy-brand')
    expect(brands.length).toBeGreaterThan(0)
    for (const brand of brands) {
      expect(brand.attributes('href')).toBe(WORKBENCH_FIELD_HOME)
    }
  })

  it('顶栏挂着身份切换器（同一颗，不改它的判定）', async () => {
    const { wrapper } = await mountShell('floor', '/workbench/floor/daily')

    expect(wrapper.find('.hy-header .wb-id').exists()).toBe(true)
  })
})

// 票 06：**独立外壳页也有退出登录入口**（今天工作台十一页一个退出按钮都没有，
// spec 故事 37 / 47）。两个管理壳各挂一颗，行为只有一处（`useWorkbenchLogout`）。
describe('工作台的外壳都有退出入口', () => {
  it('人事壳：点一下退管理端会话，落登录页并带上原页', async () => {
    const { wrapper, router } = await mountShell('hr', '/workbench/hr/calendar', { identity: 'super' })

    const exit = wrapper.get('.wb-exit')
    expect(exit.text()).toContain('退出')
    await exit.trigger('click')
    await flushPromises()

    const logout = callsTo('/api/auth/logout')
    expect(logout).toHaveLength(1)
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/workbench/hr/calendar')
  })

  it('现场壳：同一颗按钮、同一条行为', async () => {
    const { wrapper, router } = await mountShell('floor', '/workbench/floor/daily', { identity: 'super' })

    await wrapper.get('.wb-exit').trigger('click')
    await flushPromises()

    expect(callsTo('/api/auth/logout')).toHaveLength(1)
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/workbench/floor/daily')
  })

  it('工作台外壳（我的那三页）也有，且员工那一档走员工登出', async () => {
    vi.resetModules()
    const [{ default: Shell }, { useWorkbenchIdentityStore }] = await Promise.all([
      import('../WorkbenchLayout.vue'),
      import('../../../stores/workbenchIdentity'),
    ])
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useWorkbenchIdentityStore()
    store.identity = 'staff'
    store.sessions = { admin: false, staff: true }
    store.available = ['staff']
    const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
    await router.push('/workbench/me/today')
    await router.isReady()
    const wrapper = mount(Shell, { global: { plugins: [router, pinia] }, attachTo: document.body })
    await flushPromises()

    await wrapper.get('.wb-exit').trigger('click')
    await flushPromises()

    expect(callsTo('/api/hygiene/staff/logout')).toHaveLength(1)
    // 员工那一档不走管理端那条门。
    expect(callsTo('/api/auth/logout')).toHaveLength(0)
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.next).toBe('/workbench/me/today')
  })
})
