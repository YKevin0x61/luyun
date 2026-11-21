// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')

// 工作台**级**导航那一颗组件（B1 的小步）：三个壳（工作台 / 人事 / 现场）渲染的是同一颗，
// 于是「今天」在每一页都点得到（B2）。这里只压它自己：按身份过滤、后勤下拉、高亮判据。
// 壳里怎么摆（换行、横滑、贴哪边）是各壳的事，不在这一条里断。
const ROUTES = [
  { path: '/workbench', component: { template: '<div>今天</div>' } },
  { path: '/workbench/hr/calendar', component: { template: '<div>月历</div>' } },
  { path: '/workbench/floor/daily', component: { template: '<div>日常</div>' } },
  { path: '/workbench/kitchen/recipe', component: { template: '<div>配方</div>' } },
  { path: '/workbench/kitchen/prep-plan', component: { template: '<div>备货</div>' } },
  { path: '/workbench/kitchen/prep-plan/items', component: { template: '<div>备货品</div>' } },
  { path: '/workbench/kitchen/prep-plan/rules', component: { template: '<div>换算规则</div>' } },
  { path: '/workbench/kitchen/prep-plan/review', component: { template: '<div>复盘</div>' } },
  { path: '/workbench/kitchen/staff-meal', component: { template: '<div>员工餐</div>' } },
  { path: '/workbench/kitchen/purchase', component: { template: '<div>厨房采购</div>' } },
  { path: '/workbench/kitchen/loss', component: { template: '<div>损耗</div>' } },
  { path: '/workbench/staff/today', component: { template: '<div>我的</div>' } },
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
  return { wrapper, router }
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) })))
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.resetModules()
})

describe('工作台级导航（管理端：今天 / 人事 / 现场 / 后勤 / 任务；员工端：今天 / 卫生 / 后勤 / 我的）', () => {
  it('店长这一档：五个一级格都在，多页组各是一颗下拉钮，「今天」仍指 /workbench', async () => {
    const { wrapper } = await mountNav('/workbench/hr/calendar', 'super')

    // 只有一页的「今天」保留直链；人事 / 现场 / 后勤 / 任务 组内都有多页，收成下拉钮。
    expect(wrapper.findAll('.wb-nav-item').map((item) => item.text())).toEqual(['今天'])
    expect(wrapper.findAll('.wb-nav-trigger').map((item) => item.text().replace('▾', '').trim()))
      .toEqual(['人事', '现场', '后勤', '任务'])
    expect(wrapper.get('a[href="/workbench"]').text()).toBe('今天')
    // 站在人事组里，「人事」那一格亮（亮在格上，不在链接上 —— 下拉那一格只有一条线）。
    expect(wrapper.get('.wb-nav-cell.is-on .wb-nav-trigger').text()).toContain('人事')
    expect(wrapper.get('nav').attributes('aria-label')).toBe('工作台导航')
  })

  it('员工这一档：只有他看得见的几格（「卫生」直链，「我的」「后勤」是下拉）', async () => {
    const { wrapper } = await mountNav('/workbench/staff/today', 'staff')

    const hrefs = wrapper.findAll('.wb-nav-item').map((item) => item.attributes('href'))
    expect(hrefs).toEqual(['/workbench', '/workbench/staff/clean'])
    expect(wrapper.find('a[href="/workbench/floor/daily"]').exists()).toBe(false)

    const kitchen = wrapper.findAll('.wb-nav-cell.is-dropdown')
      .find((cell) => cell.text().includes('后勤'))
    await kitchen.get('.wb-nav-trigger').trigger('click')
    // 员工这一档能见的后勤门：配方 / 备货计划 / 员工餐采购（损耗只给管理端）。
    expect(kitchen.findAll('.wb-nav-menu-item').map((item) => item.attributes('href'))).toEqual([
      '/workbench/kitchen/recipe',
      '/workbench/kitchen/prep-plan',
      '/workbench/kitchen/staff-meal',
    ])
  })

  it('后勤下拉：点开是五张单，按页面清单命名；点按钮本身不导航', async () => {
    const { wrapper, router } = await mountNav('/workbench/kitchen/prep-plan', 'super')

    const cell = wrapper.findAll('.wb-nav-cell.is-dropdown')
      .find((row) => row.text().includes('后勤'))
    expect(cell.classes()).toContain('is-on')
    expect(cell.find('.wb-nav-menu').exists()).toBe(false)
    expect(cell.get('.wb-nav-trigger').attributes('aria-expanded')).toBe('false')

    await cell.get('.wb-nav-trigger').trigger('click')
    expect(cell.get('.wb-nav-trigger').attributes('aria-expanded')).toBe('true')
    expect(cell.findAll('.wb-nav-menu-item').map((item) => item.text())).toEqual([
      '配方', '备货计划', '员工餐采购', '厨房采购', '损耗分析',
    ])
    // 站在备货计划上：只有这一项亮，整格也亮。
    expect(cell.findAll('.wb-nav-menu-item.is-current').map((item) => item.attributes('href')))
      .toEqual(['/workbench/kitchen/prep-plan'])
    // 「后勤」按钮只负责展开，不切页面。
    expect(router.currentRoute.value.path).toBe('/workbench/kitchen/prep-plan')
  })

  it('子路由前缀：站在 /prep-plan/items 上，下拉里的「备货计划」仍算当前', async () => {
    const { wrapper } = await mountNav('/workbench/kitchen/prep-plan/items', 'super')

    const cell = wrapper.findAll('.wb-nav-cell.is-dropdown')
      .find((row) => row.text().includes('后勤'))
    expect(cell.classes()).toContain('is-on')
    await cell.get('.wb-nav-trigger').trigger('click')
    expect(cell.findAll('.wb-nav-menu-item.is-current').map((item) => item.attributes('href')))
      .toEqual(['/workbench/kitchen/prep-plan'])
  })

  it('人事 / 现场 / 任务组内多页时也收下拉，菜单项来自页面清单', async () => {
    const { wrapper } = await mountNav('/workbench/hr/calendar', 'super')

    const cells = wrapper.findAll('.wb-nav-cell.is-dropdown')
    const hr = cells.find((cell) => cell.text().includes('人事'))
    const floor = cells.find((cell) => cell.text().includes('现场'))
    const tasks = cells.find((cell) => cell.text().includes('任务'))
    expect(hr && floor && tasks).toBeTruthy()

    await hr.get('.wb-nav-trigger').trigger('click')
    expect(hr.findAll('.wb-nav-menu-item')).toHaveLength(8)
    expect(hr.findAll('.wb-nav-menu-item').map((item) => item.text())).toContain('排班月历')
    expect(hr.findAll('.wb-nav-menu-item').map((item) => item.text())).toContain('节假日日历')

    await floor.get('.wb-nav-trigger').trigger('click')
    expect(floor.findAll('.wb-nav-menu-item')).toHaveLength(9)
    expect(floor.findAll('.wb-nav-menu-item').map((item) => item.text())).toContain('日常验收')

    await tasks.get('.wb-nav-trigger').trigger('click')
    expect(tasks.findAll('.wb-nav-menu-item').map((item) => item.text())).toEqual([
      '任务', '定期任务',
    ])
  })

  it('点外面 / Esc 关闭下拉', async () => {
    const { wrapper } = await mountNav('/workbench/kitchen/prep-plan', 'super')

    await wrapper.get('.wb-nav-trigger').trigger('click')
    expect(wrapper.find('.wb-nav-menu').exists()).toBe(true)

    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    expect(wrapper.find('.wb-nav-menu').exists()).toBe(false)

    await wrapper.get('.wb-nav-trigger').trigger('click')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(wrapper.find('.wb-nav-menu').exists()).toBe(false)
  })

  it('身份还没探出来时一格都不渲染（不给一个可能点不通的入口）', async () => {
    const { wrapper } = await mountNav('/workbench', null)

    expect(wrapper.findAll('.wb-nav-item')).toHaveLength(0)
    expect(wrapper.find('.wb-nav-trigger').exists()).toBe(false)
  })

  // 媒体查询在 jsdom 里不生效，断"1024 档的下拉按钮 44px"只能按源码断（仓库先例见
  // `views/workbench/__tests__/shellMobileChrome.test.js` 文件头）。
  it('1024 档的样式规则：下拉钮与菜单项 44px，不再有旧的组门规则', () => {
    const source = read('../WorkbenchNav.vue')
    const tablet = source.match(/@media \(max-width: 1024px\) \{([\s\S]*?)\n\}/)
    expect(tablet, '缺少 1024 档的媒体查询').toBeTruthy()

    const block = tablet[1]
    expect(block).toMatch(/\.wb-nav-trigger\s*\{[^}]*min-height:\s*44px/)
    expect(block).toMatch(/\.wb-nav-menu-item\s*\{[^}]*min-height:\s*44px/)
    // 旧的一格多门组门（`.wb-nav-group`）已经退场：后勤只剩一颗下拉钮。
    expect(source).not.toMatch(/wb-nav-group/)
    expect(source).not.toMatch(/is-multi/)
  })
})
