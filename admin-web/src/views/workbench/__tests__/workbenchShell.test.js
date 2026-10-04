// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

// 工作台外壳（票 03 立、票 04 接上身份）：
// - 导航跟着**此刻的身份**走，不跟着"这一页是谁的"。店长的会话挂在「我的」页上时，
//   顶栏照样是他的那一档与他的导航面（人事 / 现场那两组在票 05 落位后自动出现）。
// - 顶栏那颗切换器换一档，导航面跟着换。
// 断言的都是渲染出来的东西（有几格、点去哪、亮不亮），不是组件的内部结构。

const ROUTES = [
  { path: '/workbench/me/today', component: { template: '<div>今天</div>' }, meta: { audience: 'staff', standalone: true } },
  { path: '/workbench/me/month', component: { template: '<div>整月</div>' }, meta: { audience: 'staff', standalone: true } },
  // 店长那几页与清单里一样带 `audience: 'admin'`（这里只压外壳，页面本体给个占位）。
  { path: '/workbench/daily', component: { template: '<div>日常验收</div>' }, meta: { audience: 'admin', standalone: true } },
  { path: '/workbench/roster', component: { template: '<div>花名册</div>' }, meta: { audience: 'admin', standalone: true } },
]

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 店长那一档的导航项：票 05 落位之前工作台里还没有他的组，这里借清单里一页**店长
 *  专属**的（`/workbench/daily`）临时插一格，用来压「切档之后人被送回自己那一档」。 */
const ADMIN_GROUP = { key: 'daily', label: '日常验收', to: '/workbench/daily' }

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

/** 每个用例换一份崭新的模块图（`authStatus` 有 10 秒状态缓存），并给一份空的记忆。
 *
 *  `prepare` 在 `resetModules` **之后**、挂载**之前**跑：要动模块级常量（借一格导航项）
 *  的用例得在这里动手，拿到的才是组件实际用的那一份模块图。 */
async function mountShell(path, { prepare } = {}) {
  vi.resetModules()
  localStorage.clear()
  if (prepare) await prepare()
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
  // 借来的那一格用完还回去（导航表是模块级常量，别漏给下一个用例）。
  // 注意跟着 `vi.resetModules()` 走：用例里 import 的是**当前那一份**模块图，
  // 所以清理也拿当轮的那一份。
  return import('../../../utils/workbenchNav').then(({ WORKBENCH_NAV_GROUPS }) => {
    const borrowed = WORKBENCH_NAV_GROUPS.indexOf(ADMIN_GROUP)
    if (borrowed >= 0) WORKBENCH_NAV_GROUPS.splice(borrowed, 1)
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })
})

describe('工作台外壳的导航', () => {
  it('员工这一档只有「我的」一格，指到「今天」', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())
    const { wrapper } = await mountShell('/workbench/me/today')

    const items = wrapper.findAll('.wb-nav-item')
    expect(items).toHaveLength(1)
    expect(items[0].text()).toBe('我的')
    expect(items[0].attributes('href')).toBe('/workbench/me/today')
    // 员工点不到店长那几页（它们在清单里是 admin，导航按身份过滤）。
    expect(wrapper.find('a[href="/workbench/daily"]').exists()).toBe(false)
  })

  it('整组都亮：站在「整月」上，「我的」那一格仍是当前项', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())
    const { wrapper } = await mountShell('/workbench/me/month')

    const item = wrapper.get('.wb-nav-item')
    expect(item.classes()).toContain('is-on')
    expect(item.attributes('aria-current')).toBe('page')
  })

  it('点一下真的走到那一格（外壳是路由链接，不是摆设）', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())
    const { wrapper, router } = await mountShell('/workbench/me/month')

    await wrapper.get('.wb-nav-item').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/workbench/me/today')
  })

  it('顶栏挂着身份切换器，两档都在（「超级管理员」不写成「管理员」）', async () => {
    const { wrapper } = await mountShell('/workbench/me/today')

    const switcher = wrapper.get('.wb-id')
    expect(switcher.text()).toContain('超级管理员')
    expect(switcher.text()).toContain('共享账号')
    expect(switcher.text()).toContain('员工')
    // 词表打架的那一个词不出现在顶栏上。
    expect(switcher.text()).not.toMatch(/管理员（共享账号）/)
  })

  it('店长的会话挂在员工页上时，顶栏仍是店长那一档（导航不跟着页面走）', async () => {
    const { wrapper, store } = await mountShell('/workbench/me/today')

    expect(store.identity).toBe('super')
    expect(wrapper.get('.wb-id .is-on').text()).toContain('超级管理员')
    // 导航面按**他这一档**渲染：人事 / 现场那两组要等票 05 落位，所以此刻一格都没有
    // （而不是因为"这一页是员工页"才藏起来）。页面内容按自己的 401 处理 —— 切换器
    // 不改权限、也不改守卫。
    expect(wrapper.findAll('.wb-nav-item')).toHaveLength(0)
  })

  it('在顶栏切成员工：导航面当场跟着换', async () => {
    const { wrapper, store } = await mountShell('/workbench/me/today')

    const staffOption = wrapper.findAll('.wb-id-opt').find((n) => n.text().includes('员工'))
    await staffOption.trigger('click')
    await flushPromises()

    expect(store.identity).toBe('staff')
    expect(wrapper.findAll('.wb-nav-item')).toHaveLength(1)
    expect(wrapper.get('.wb-nav-item').text()).toBe('我的')
  })

  it('切成员工后仍站在店长的页上：送回员工那一档的首页（不是停在别人的页上）', async () => {
    // 借一格店长专属的导航项（票 05 把人事 / 现场并进来之前工作台里还没有他的组）。
    // 起点是店长的页 —— 顶栏这一档与导航面按他算。
    const { wrapper, router, store } = await mountShell('/workbench/daily', {
      prepare: async () => {
        const { WORKBENCH_NAV_GROUPS } = await import('../../../utils/workbenchNav')
        WORKBENCH_NAV_GROUPS.push(ADMIN_GROUP)
      },
    })
    expect(store.identity).toBe('super')
    expect(wrapper.find('a[href="/workbench/daily"]').exists()).toBe(true)

    const staffOption = wrapper.findAll('.wb-id-opt').find((n) => n.text().includes('员工'))
    await staffOption.trigger('click')
    await flushPromises()
    await flushPromises()

    // 员工这一档的导航落点是「我的」，这一页不是他看的，于是人被送到那儿
    // （不是被守卫甩走、也不是停在别人的页上）。
    expect(router.currentRoute.value.path).toBe('/workbench/me/today')
    expect(wrapper.get('.wb-nav-item').text()).toBe('我的')
  })
})
