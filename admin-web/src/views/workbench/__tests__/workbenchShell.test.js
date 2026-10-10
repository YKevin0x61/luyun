// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { WORKBENCH_FIELD_HOME, WORKBENCH_HR_HOME } from '../../../utils/workbenchCopy.js'
import { PREP_PLAN_PATH } from '../../../utils/prepPlanPaths.js'

// 工作台外壳（票 03 立、票 04 接上身份）：
// - 导航跟着**此刻的身份**走，不跟着"这一页是谁的"。店长的会话挂在「我的」页上时，
//   顶栏照样是他的那一档与他的导航面（人事 / 现场 / 后勤那几组落位后自动出现）。
// - 顶栏那颗切换器换一档，导航面跟着换。
// 断言的都是渲染出来的东西（有几格、点去哪、亮不亮），不是组件的内部结构。

const ROUTES = [
  { path: '/workbench', component: { template: '<div>今天</div>' }, meta: { audience: 'both', standalone: true } },
  { path: '/workbench/me/today', component: { template: '<div>今天</div>' }, meta: { audience: 'staff', standalone: true } },
  { path: '/workbench/me/month', component: { template: '<div>整月</div>' }, meta: { audience: 'staff', standalone: true } },
  // 店长那几页与清单里一样带 `audience: 'admin'`（这里只压外壳，页面本体给个占位）。
  // 票 05：工作台按组分家，人事 / 现场各有自己的落点。票 06：首页（`/workbench`）是 `both`。
  { path: '/workbench/hr/calendar', component: { template: '<div>排班月历</div>' }, meta: { audience: 'admin', standalone: true } },
  { path: '/workbench/floor/daily', component: { template: '<div>日常验收</div>' }, meta: { audience: 'admin', standalone: true } },
  // 票 07：后勤那一格的落点（配方列表，`both` —— 员工也看得见）。
  { path: '/workbench/kitchen/recipe', component: { template: '<div>配方</div>' }, meta: { audience: 'both', standalone: true } },
  // 票 08：后勤那一格的第二扇门（备货计划，`both`）—— 换位置、统一导航，不扩权。
  { path: PREP_PLAN_PATH, component: { template: '<div>备货计划</div>' }, meta: { audience: 'both', standalone: true } },
]

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 两套会话都在：店里那台共用电脑。 */
function bothSessionsFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: true, initialized: true })
    if (path.includes('/api/hygiene/staff/me')) return jsonResponse({ employee: { name: '张三' } })
    return jsonResponse({}, 404)
  })
}

/** 只有员工会话：员工自己的手机。 */
function staffOnlyFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: false, initialized: true })
    if (path.includes('/api/hygiene/staff/me')) return jsonResponse({ employee: { name: '张三' } })
    return jsonResponse({}, 404)
  })
}

/** 每个用例换一份崭新的模块图（`authStatus` 有 10 秒状态缓存、身份记忆在 localStorage），
 *  并给一份空的记忆。 */
async function mountShell(path) {
  vi.resetModules()
  localStorage.clear()
  const [{ default: WorkbenchLayout }, { useWorkbenchIdentityStore }] = await Promise.all([
    import('../WorkbenchLayout.vue'),
    import('../../../stores/workbenchIdentity'),
  ])

  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  await router.push(path)
  await router.isReady()

  const wrapper = mount(WorkbenchLayout, { global: { plugins: [router, pinia] } })
  await flushPromises()
  await flushPromises()
  return { wrapper, router, store: useWorkbenchIdentityStore() }
}

beforeEach(() => {
  vi.stubGlobal('fetch', bothSessionsFetch())
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('工作台外壳的导航', () => {
  it('员工这一档：今天 / 后勤（配方 + 备货计划）/ 我的 —— 店长那几页点不到', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())
    const { wrapper } = await mountShell('/workbench/me/today')

    const items = wrapper.findAll('.wb-nav-item')
    // 票 08：后勤那一格是**一格两门** —— 配方与备货计划各占一半，都在同一格（同一颗
    // 胶囊）里，员工两项都看得见（spec 故事 8）。一格两门时标题取各自那一页在清单里的
    // 名字（「选择岗位」/「备货计划」）：同一颗胶囊里写两遍「后勤」看不出哪一半是哪一页。
    // 2026-10-05 起员工这一档还多一格「卫生」（他们每天要做的活，一级入口）。
    // 员工那一档：第二格仍是「卫生」（员工端那一格名字没改）。
    expect(items.map((item) => item.text())).toEqual(['今天', '卫生', '选择岗位', '备货计划', '我的'])
    expect(items.map((item) => item.attributes('href'))).toEqual([
      '/workbench', '/workbench/me/clean', '/workbench/kitchen/recipe', PREP_PLAN_PATH,
      '/workbench/me/today',
    ])
    expect(wrapper.findAll('.wb-nav-cell.is-multi')).toHaveLength(1)
    // 员工点不到店长那几页（它们在清单里是 admin，导航按身份过滤）。
    expect(wrapper.find('a[href="/workbench/floor/daily"]').exists()).toBe(false)
    // 配方管理那一页也不在导航里（admin）。
    expect(wrapper.find('a[href="/workbench/kitchen/recipe/manage"]').exists()).toBe(false)
  })

  it('站在备货计划上：「后勤」那一格仍然亮（高亮按页面的组算，不按路径前缀比）', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())
    const { wrapper } = await mountShell(PREP_PLAN_PATH)

    // 整格亮：后勤那一格里两扇门都算"当前这一格"（同一组），`aria-current` 也挂在两半上
    // —— 与单条目那一格的老口径一致（外壳的高亮判据是页面的 `group`）。
    const on = wrapper.findAll('.wb-nav-item.is-on')
    expect(on.map((item) => item.text())).toEqual(['选择岗位', '备货计划'])
    expect(on.map((item) => item.attributes('aria-current'))).toEqual(['page', 'page'])

    // 但只有指到这一页的那半贴着"当前"：另一半不高亮成当前项。
    const current = wrapper.findAll('.wb-nav-item.is-current')
    expect(current).toHaveLength(1)
    expect(current[0].text()).toBe('备货计划')
    expect(current[0].attributes('href')).toBe(PREP_PLAN_PATH)

    const cell = wrapper.get('.wb-nav-cell.is-multi')
    expect(cell.findAll('.wb-nav-item')).toHaveLength(2)
    expect(cell.classes()).toContain('is-on')
  })

  it('整组都亮：站在「整月」上，「我的」那一格仍是当前项（首页那一格不亮）', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())
    const { wrapper } = await mountShell('/workbench/me/month')

    const items = wrapper.findAll('.wb-nav-item')
    const on = items.filter((item) => item.classes().includes('is-on'))
    expect(on).toHaveLength(1)
    expect(on[0].text()).toBe('我的')
    expect(on[0].attributes('aria-current')).toBe('page')
  })

  it('点一下真的走到那一格（外壳是路由链接，不是摆设）', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())
    const { wrapper, router } = await mountShell('/workbench/me/month')

    // 2026-10-05 起员工那一档的顺序是：今天 / 卫生 / 后勤（两门）/ 我的 ——
    // 第二格是「卫生」，后勤从第二格挪到了第三、四格。
    await wrapper.findAll('.wb-nav-item')[2].trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/workbench/kitchen/recipe')
  })

  it('顶栏挂着身份切换器，显示的是**当前**那一档（「超级管理员」不写成「管理员」）', async () => {
    const { wrapper } = await mountShell('/workbench/me/today')

    const switcher = wrapper.get('.wb-id')
    expect(switcher.text()).toContain('超级管理员')
    expect(switcher.text()).toContain('共享账号')
    // 2026-10-05 裁定：一行只显示当前身份 —— 这一台是双会话，另一档（员工）收进点开的
    // 菜单里，所以这里**不该**出现「员工」；菜单要到点开之后才有。
    expect(switcher.text()).not.toContain('员工')
    expect(wrapper.find('.wb-id-menu').exists()).toBe(false)
    // 词表打架的那一个词不出现在顶栏上。
    expect(switcher.text()).not.toMatch(/管理员（共享账号）/)
  })

  it('店长的会话挂在员工页上时，顶栏仍是店长那一档（导航不跟着页面走）', async () => {
    const { wrapper, store } = await mountShell('/workbench/me/today')

    expect(store.identity).toBe('super')
    expect(wrapper.get('.wb-id-current').text()).toContain('超级管理员')
    // 导航面按**他这一档**渲染：票 06 之后是首页 / 人事 / 卫生三格，各自的落点是共享
    // 常量（而不是因为"这一页是员工页"才藏起来）。页面内容按自己的 401 处理 —— 切换器
    // 不改权限、也不改守卫。票 08 起后勤那一格是两扇门（配方 + 备货计划）。
    const items = wrapper.findAll('.wb-nav-item')
    expect(items.map((item) => item.text())).toEqual([
      '今天', '人事', '卫生验收', '选择岗位', '备货计划',
    ])
    expect(items.map((item) => item.attributes('href'))).toEqual([
      '/workbench', WORKBENCH_HR_HOME, WORKBENCH_FIELD_HOME,
      '/workbench/kitchen/recipe', PREP_PLAN_PATH,
    ])
  })

  it('在顶栏切成员工：导航面当场跟着换', async () => {
    const { wrapper, store } = await mountShell('/workbench/me/today')

    // 切换器现在**只显示当前身份**（2026-10-05 裁定），另一档收在点开的菜单里：
    // 先点当前这一档，再选菜单里那一项。
    await wrapper.get('.wb-id-current').trigger('click')
    await flushPromises()
    await wrapper.get('.wb-id-menu-item').trigger('click')
    await flushPromises()

    expect(store.identity).toBe('staff')
    expect(wrapper.findAll('.wb-nav-item').map((item) => item.text())).toEqual([
      // 员工那一格仍叫「卫生」（③-2 改的是超管那一格：卫生 → 卫生验收）。
      '今天', '卫生', '选择岗位', '备货计划', '我的',
    ])
  })

  it('切成员工后仍站在店长的页上：送回员工那一档的第一格（不是停在别人的页上）', async () => {
    // 起点是店长的页（现场组的落点）—— 顶栏这一档与导航面按他算。
    const { wrapper, router, store } = await mountShell('/workbench/floor/daily')
    expect(store.identity).toBe('super')
    expect(wrapper.find('a[href="/workbench/floor/daily"]').exists()).toBe(true)

    // 切换器现在**只显示当前身份**（2026-10-05 裁定），另一档收在点开的菜单里：
    // 先点当前这一档，再选菜单里那一项。
    await wrapper.get('.wb-id-current').trigger('click')
    await flushPromises()
    await wrapper.get('.wb-id-menu-item').trigger('click')
    await flushPromises()
    await flushPromises()

    // 员工这一档的第一格是首页（`/workbench`，`both`），这一页不是他看的，于是人被送到那儿
    // （不是被守卫甩走、也不是停在别人的页上）。
    expect(router.currentRoute.value.path).toBe('/workbench')
  })

  it('顶栏有一扇回管理后台的门（spec 故事 11：两边各留一个入口、双向）', async () => {
    // 工作台首页 / 后勤 / 「我的」这几页走的都是这个壳；人事与现场那两个壳各自也有
    // 一颗「‹ 后台」。少了它，店长站在子应用首页回不去后台（code-review 指出的缺口）。
    const { wrapper } = await mountShell('/workbench')

    const back = wrapper.find('.wb-back')
    expect(back.exists()).toBe(true)
    expect(back.text()).toContain('后台')
    expect(back.attributes('href')).toBe('/')
  })
})
