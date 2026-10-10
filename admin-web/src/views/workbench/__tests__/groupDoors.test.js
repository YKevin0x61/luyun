// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { WORKBENCH_FIELD_HOME, WORKBENCH_HOME, WORKBENCH_HR_HOME } from '../../../utils/workbenchCopy.js'
import { HYGIENE_ADMIN_NAV } from '../../../utils/hygieneCopy.js'
import { PREP_PLAN_PATH } from '../../../utils/prepPlanPaths.js'
import { RECIPE_HOME_PATH } from '../../../utils/recipePaths.js'
import { workbenchPagesOf } from '../../../utils/workbenchNav.js'

const here = dirname(fileURLToPath(import.meta.url))
const HR_SHELL_SOURCE = readFileSync(join(here, '../../scheduling/SchedulingLayout.vue'), 'utf8')

// 工作台三个壳的关系（B1 的小步之后）：
//   - `WorkbenchLayout`（首页 / 后勤 / 我的）：一条顶栏，工作台级导航；
//   - 人事壳（`SchedulingLayout`）与现场壳（`HygieneAdminLayout`）：各自的外壳还在，
//     但**都渲染同一颗工作台级导航**（`components/workbench/WorkbenchNav.vue`，
//     表来自 `utils/workbenchNav.js`）—— 于是「今天」在 11 个页面上都点得到（B2），
//     两组原先互相开的那两扇单门（「现场 ›」/ rail 里的「人事」）随之撤掉：它们是
//     这一排导航的真子集，留着就是同一件事写两遍。
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
  // 票 07：后勤那一格（配方）—— 导航里画得出来，路由表里就得有它，
  // 否则点一下只剩一条 "No match found" 告警。
  ...workbenchPagesOf('kitchen').map(pageStub),
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

describe('人事壳（工作台级导航 + 本组四页）', () => {
  it('本组四页都在顶栏导航里，链接指向新地址（不是平铺的旧地址）', async () => {
    const { wrapper } = await mountShell('hr', '/workbench/hr/calendar')

    const hrefs = wrapper.findAll('.wb-rail-item').map((item) => item.attributes('href'))
    expect(hrefs).toEqual(workbenchPagesOf('hr').map((page) => page.path))
    for (const href of hrefs) {
      expect(href.startsWith('/workbench/hr/')).toBe(true)
    }
  })

  it('顶栏带上整排工作台级导航：今天 / 人事 / 现场 / 后勤 / 我的都在，落点正确（B1/B2）', async () => {
    // 这一组原来只有本组四页的导航与一扇「现场 ›」，**没有任何一条回工作台首页的链接**
    // （href 全集里不含 `/workbench`）。现在顶栏并进整排工作台级导航（与
    // `WorkbenchLayout` 同一颗组件、同一张表），「今天」在人事四页上都点得到。
    const { wrapper } = await mountShell('hr', '/workbench/hr/calendar', { identity: 'super' })

    const hrefs = wrapper.findAll('.wb-nav-item').map((item) => item.attributes('href'))
    // 身份过滤照旧按**落点那一页**的 `audience` 走：店长这一档没有「我的」那一格
    // （员工三页是 `staff`），与 `WorkbenchLayout` 里的判定同一份代码。
    expect(hrefs).toEqual([
      WORKBENCH_HOME, WORKBENCH_HR_HOME, WORKBENCH_FIELD_HOME,
      RECIPE_HOME_PATH, PREP_PLAN_PATH,
    ])
    expect(wrapper.find(`.wb-nav-item[href="${WORKBENCH_HOME}"]`).exists()).toBe(true)
    // ③-2：组内页挪到左 rail（统一壳一处渲染），它自己的条数 = 本组可见页数。
    expect(wrapper.get('.wb-rail').findAll('.wb-rail-item').length).toBe(workbenchPagesOf('hr').length)
  })

  it('牌子写「工作台」就指工作台首页（不再指本组首页）', async () => {
    const { wrapper } = await mountShell('hr', '/workbench/hr/calendar')

    const brand = wrapper.get('.wb-brand')
    expect(brand.text()).toBe('工作台')
    expect(brand.attributes('href')).toBe(WORKBENCH_HOME)
  })

  it('旧的「现场 ›」那扇门没了：跨组由工作台级导航接手（同一件事不写两遍）', async () => {
    const { wrapper } = await mountShell('hr', '/workbench/hr/calendar')

    expect(wrapper.find('.sched-field').exists()).toBe(false)
  })

  it('顶栏挂着身份切换器（票 04 交接：店长在自己的页面里也要看得到自己在哪一档）', async () => {
    // 切换器现在**只显示当前身份**（2026-10-05 裁定），所以这个用例要给一个真的会话来定档；
    // 探针没回来、一个档位都不可用时它不渲染 —— 那是有意的（宁可空着，也不闪一个假身份）。
    const { wrapper } = await mountShell('hr', '/workbench/hr/calendar', { identity: 'super' })

    expect(wrapper.find('.wb-id').exists()).toBe(true)
    expect(wrapper.get('.wb-id').text()).toContain('超级管理员')
  })

  it('高亮跟着新分组走：站在花名册上，花名册那一格亮', async () => {
    const { wrapper } = await mountShell('hr', '/workbench/hr/roster')

    const on = wrapper.findAll('.wb-rail-item').filter((item) => item.classes().includes('is-on'))
    expect(on).toHaveLength(1)
    expect(on[0].attributes('href')).toBe('/workbench/hr/roster')
  })

  it('花名册搬过来之后页面自己不带内边距：由这条壳补上（原来那份是卫生壳给的）', () => {
    // jsdom 不做布局，所以这一条按源码断：花名册的 `.roster-page` 只设了宽度与居中，
    // 内边距一直是卫生壳 `.hy-main` 给的；换成人事壳之后必须有这一条，否则页面上
    // 文字会贴着屏幕边（其余三页自带内边距，不吃这条）。
    expect(HR_SHELL_SOURCE).toMatch(
      /\.sched-shell :deep\(\.roster-page\)\s*\{[^}]*padding:\s*var\(--hy-page\)/,
    )
  })

  it('那条内边距真的落得上：花名册挂在统一壳的内容区里', async () => {
    // ③-2 之前这一条断的是「`.roster-page` 带着壳的作用域标记」（壳用直接子选择器给内边距）。
    // 三壳合一之后页面挂在统一壳的 `<main class="wb-main">` 里，作用域标记挂在那一层 ——
    // 所以这里断**挂载关系**（页面确实在壳的内容区里），内边距那条规则由上面那条源码断言钉住。
    const { wrapper } = await mountShell('hr', '/workbench/hr/roster')
    expect(wrapper.get('.wb-main .roster-page').exists()).toBe(true)
  })
})

describe('现场壳（rail 八项 + 内容区顶上的工作台级导航）', () => {
  it('rail 渲染的是现场壳自己那份名单，花名册不在其中（它现在是人事页）', async () => {
    const { wrapper } = await mountShell('floor', '/workbench/floor/daily')

    const hrefs = wrapper.findAll('.wb-rail-item').map((item) => item.attributes('href'))
    // **现场壳的 rail 不是从页面清单派生的**：人事壳那一侧才是 `workbenchPagesOf('hr')`，
    // 现场壳读的是 `utils/hygieneCopy.js` 里手写的 `HYGIENE_ADMIN_NAV`。所以往现场组加页要
    // **同时**动清单与那份手写名单 —— 只动一边，下面第二条断言就红。
    expect(hrefs).toEqual(HYGIENE_ADMIN_NAV.map((item) => item.path))
    expect(hrefs).not.toContain('/workbench/hr/roster')
    // 两份名单**此刻是一致的**（2026-10-05 补上「卫生趋势」那一行之后）：这条把关系钉住，
    // 将来谁只往清单里加页、忘了 rail（页面敲 URL 进得去、导航里没入口），这里立刻红。
    expect([...HYGIENE_ADMIN_NAV].map((item) => item.path).sort()).toEqual(
      workbenchPagesOf('floor').map((page) => page.path).sort(),
    )
  })

  it('内容区顶上是整排工作台级导航：今天 / 人事 / 现场 / 后勤 / 我的（B1/B2）', async () => {
    // 这一组原来右边那扇「人事」门是**唯一**的跨组出口，回工作台的链接一条都没有。
    // 现在整排工作台导航在内容区顶上那条横条里（那一条右侧原先大片空着）。
    const { wrapper } = await mountShell('floor', '/workbench/floor/daily', { identity: 'super' })

    expect(wrapper.get('.wb-top').find('.wb-nav').exists()).toBe(true)
    const hrefs = wrapper.findAll('.wb-nav-item').map((item) => item.attributes('href'))
    // 身份过滤照旧按**落点那一页**的 `audience` 走：店长这一档没有「我的」那一格
    // （员工三页是 `staff`），与 `WorkbenchLayout` 里的判定同一份代码。
    expect(hrefs).toEqual([
      WORKBENCH_HOME, WORKBENCH_HR_HOME, WORKBENCH_FIELD_HOME,
      RECIPE_HOME_PATH, PREP_PLAN_PATH,
    ])
    expect(wrapper.find(`.wb-nav-item[href="${WORKBENCH_HOME}"]`).exists()).toBe(true)
    // rail 自己的名字跟着组名走：工作台 · 卫生验收（组名 2026-10-09 由「卫生」改成「卫生验收」，
    // 与工作台导航里那一格同一个词 —— 名单在 `utils/workbenchNav.js` 一处）。
    expect(wrapper.get('.wb-rail').attributes('aria-label')).toBe('工作台 · 卫生验收')
  })

  it('rail 里那扇单门「人事」没了：跨组由工作台级导航接手', async () => {
    const { wrapper } = await mountShell('floor', '/workbench/floor/daily')

    expect(wrapper.find('.hy-tab-cross').exists()).toBe(false)
  })

  it('品牌链接指工作台首页（牌子写的是「工作台」，不是现场组那一页）', async () => {
    const { wrapper } = await mountShell('floor', '/workbench/floor/boards')

    const brands = wrapper.findAll('.wb-brand')
    expect(brands.length).toBeGreaterThan(0)
    for (const brand of brands) {
      expect(brand.attributes('href')).toBe(WORKBENCH_HOME)
    }
  })

  it('顶栏挂着身份切换器（同一颗，不改它的判定）', async () => {
    const { wrapper } = await mountShell('floor', '/workbench/floor/daily', { identity: 'super' })

    expect(wrapper.find('.wb-top .wb-id').exists()).toBe(true)
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
