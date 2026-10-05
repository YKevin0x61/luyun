// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

// 工作台**级**导航那一颗组件（B1 的小步）：三个壳（工作台 / 人事 / 现场）渲染的是同一颗，
// 于是「今天」在每一页都点得到（B2）。这里只压它自己：按身份过滤、一格两门、高亮判据。
// 壳里怎么摆（换行、横滑、贴哪边）是各壳的事，不在这一条里断。
const ROUTES = [
  { path: '/workbench', component: { template: '<div>今天</div>' } },
  { path: '/workbench/hr/calendar', component: { template: '<div>月历</div>' } },
  { path: '/workbench/floor/daily', component: { template: '<div>日常</div>' } },
  { path: '/workbench/kitchen/recipe', component: { template: '<div>配方</div>' } },
  { path: '/workbench/kitchen/prep-plan', component: { template: '<div>备货</div>' } },
  { path: '/workbench/me/today', component: { template: '<div>我的</div>' } },
]

async function mountNav(path, identity) {
  vi.resetModules()
  localStorage.clear()
  const [{ default: WorkbenchNav }, { useWorkbenchIdentityStore }] = await Promise.all([
    import('../WorkbenchNav.vue'),
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
  const wrapper = mount(WorkbenchNav, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) })))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.resetModules()
})

describe('工作台级导航（今天 / 人事 / 现场 / 后勤 / 我的）', () => {
  it('店长这一档：五个门都在，「今天」指 /workbench（B2 那条回首页的路）', async () => {
    const wrapper = await mountNav('/workbench/hr/calendar', 'super')

    expect(wrapper.findAll('.wb-nav-item').map((item) => item.text())).toEqual([
      '今天', '人事', '现场', '选择岗位', '备货计划',
    ])
    expect(wrapper.get('a[href="/workbench"]').text()).toBe('今天')
    // 站在人事组里，「人事」那一格亮。
    expect(wrapper.findAll('.wb-nav-item.is-on').map((item) => item.attributes('href')))
      .toEqual(['/workbench/hr/calendar'])
    expect(wrapper.get('nav').attributes('aria-label')).toBe('工作台导航')
  })

  it('员工这一档：只有他看得见的几格（「卫生」「我的」在，人事 / 现场不在）', async () => {
    const wrapper = await mountNav('/workbench/me/today', 'staff')

    const hrefs = wrapper.findAll('.wb-nav-item').map((item) => item.attributes('href'))
    expect(hrefs).toEqual([
      '/workbench', '/workbench/me/clean', '/workbench/kitchen/recipe',
      '/workbench/kitchen/prep-plan', '/workbench/me/today',
    ])
    expect(wrapper.find('a[href="/workbench/floor/daily"]').exists()).toBe(false)
  })

  it('后勤那一格是一格两门：同一颗胶囊里的两条链接，只有指着这一页的那半算「当前」', async () => {
    const wrapper = await mountNav('/workbench/kitchen/prep-plan', 'super')

    const cell = wrapper.get('.wb-nav-cell.is-multi')
    expect(cell.findAll('.wb-nav-item')).toHaveLength(2)
    expect(cell.classes()).toContain('is-on')
    expect(wrapper.findAll('.wb-nav-item.is-current').map((item) => item.attributes('href')))
      .toEqual(['/workbench/kitchen/prep-plan'])
  })

  it('身份还没探出来时一格都不渲染（不给一个可能点不通的入口）', async () => {
    const wrapper = await mountNav('/workbench', null)

    expect(wrapper.findAll('.wb-nav-item')).toHaveLength(0)
  })
})
