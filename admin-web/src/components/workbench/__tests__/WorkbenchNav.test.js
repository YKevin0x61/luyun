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
// 于是「今天」在每一页都点得到（B2）。这里只压它自己：按身份过滤、一格两门、高亮判据。
// 壳里怎么摆（换行、横滑、贴哪边）是各壳的事，不在这一条里断。
const ROUTES = [
  { path: '/workbench', component: { template: '<div>今天</div>' } },
  { path: '/workbench/hr/calendar', component: { template: '<div>月历</div>' } },
  { path: '/workbench/floor/daily', component: { template: '<div>日常</div>' } },
  { path: '/workbench/kitchen/recipe', component: { template: '<div>配方</div>' } },
  { path: '/workbench/kitchen/prep-plan', component: { template: '<div>备货</div>' } },
  { path: '/workbench/kitchen/prep-review', component: { template: '<div>复盘</div>' } },
  { path: '/workbench/kitchen/prep-rules', component: { template: '<div>换算规则</div>' } },
  { path: '/workbench/kitchen/prep-items', component: { template: '<div>备货品</div>' } },
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

describe('工作台级导航（管理端：今天 / 人事 / 现场 / 后勤 / 任务；员工端：今天 / 卫生 / 后勤 / 我的）', () => {
  it('店长这一档：五格都在，「今天」指 /workbench（B2 那条回首页的路）', async () => {
    const wrapper = await mountNav('/workbench/hr/calendar', 'super')

    // 后勤那一格是一组多门：配方（选择岗位）/ 备货计划 / 备货复盘 / 换算规则 / 备货品 / 损耗分析 ——
    // 前两条 `both`，其余只给管理端，所以店长这一档全在。
    expect(wrapper.findAll('.wb-nav-item').map((item) => item.text())).toEqual([
      '今天', '人事', '现场', '选择岗位', '备货计划', '备货复盘', '换算规则', '备货品', '损耗分析',
      '员工餐采购', '任务',
    ])
    expect(wrapper.get('a[href="/workbench"]').text()).toBe('今天')
    // 站在人事组里，「人事」那一格亮。
    expect(wrapper.findAll('.wb-nav-item.is-on').map((item) => item.attributes('href')))
      .toEqual(['/workbench/hr/calendar'])
    expect(wrapper.get('nav').attributes('aria-label')).toBe('工作台导航')
  })

  it('员工这一档：只有他看得见的几格（「卫生」「我的」在，人事与店长那格不在）', async () => {
    const wrapper = await mountNav('/workbench/staff/today', 'staff')

    const hrefs = wrapper.findAll('.wb-nav-item').map((item) => item.attributes('href'))
    expect(hrefs).toEqual([
      '/workbench', '/workbench/staff/clean', '/workbench/kitchen/recipe',
      '/workbench/kitchen/prep-plan', '/workbench/kitchen/staff-meal',
      '/workbench/staff/today',
    ])
    expect(wrapper.find('a[href="/workbench/floor/daily"]').exists()).toBe(false)
  })

  it('后勤那一格是一格多门：同一颗胶囊里的多条链接，只有指着这一页的那半算「当前」', async () => {
    const wrapper = await mountNav('/workbench/kitchen/prep-plan', 'super')

    const cell = wrapper.get('.wb-nav-cell.is-multi')
    expect(cell.findAll('.wb-nav-item')).toHaveLength(7)
    expect(cell.classes()).toContain('is-on')
    expect(wrapper.findAll('.wb-nav-item.is-current').map((item) => item.attributes('href')))
      .toEqual(['/workbench/kitchen/prep-plan'])
  })

  it('身份还没探出来时一格都不渲染（不给一个可能点不通的入口）', async () => {
    const wrapper = await mountNav('/workbench', null)

    expect(wrapper.findAll('.wb-nav-item')).toHaveLength(0)
  })

  // 平板 / 小笔电（721–1024）走查发现的真缺陷：九扇门在 834 宽要 935px，顶栏被裁、
  // 「换算规则 / 备货品 / 损耗分析」点不到。修法是一格多门（后勤）收成一扇「后勤」组门，
  // 组内换页交给组自己的页内导航。jsdom 不解析媒体查询，所以**默认态**两样都在 DOM 里
  // （CSS 决定谁显示），这里只断那颗组门指对地方、且不占 `.wb-nav-item` 这个一级门的类。
  it('多门那一格带一颗组门：指组的落点、按组名命名、不混进一级门', async () => {
    const wrapper = await mountNav('/workbench/kitchen/prep-plan', 'super')

    const group = wrapper.get('.wb-nav-group')
    expect(group.text()).toBe('后勤')
    expect(group.attributes('href')).toBe('/workbench/kitchen/recipe')
    expect(group.classes()).not.toContain('wb-nav-item')
    // 组门与组里六扇门都在；当前格亮，组门跟着组走。
    expect(wrapper.get('.wb-nav-cell.is-multi').findAll('.wb-nav-group')).toHaveLength(1)
    expect(wrapper.get('.wb-nav-cell.is-multi').classes()).toContain('is-on')
  })

  it('员工这一档同样收：后勤组门在，六扇门默认也还在 DOM 里（CSS 收，不是删）', async () => {
    const wrapper = await mountNav('/workbench/staff/today', 'staff')

    const group = wrapper.get('.wb-nav-group')
    expect(group.text()).toBe('后勤')
    expect(group.attributes('href')).toBe('/workbench/kitchen/recipe')
    // 员工这一档能见的后勤门：配方 / 备货计划 / 员工餐采购（其余只给管理端）。
    expect(wrapper.get('.wb-nav-cell.is-multi').findAll('.wb-nav-item')).toHaveLength(3)
  })

  // 媒体查询在 jsdom 里不生效，断"1024 档把多门收起来"只能按源码断（仓库先例见
  // `views/workbench/__tests__/shellMobileChrome.test.js` 文件头：布局事实靠浏览器探针，
  // "有没有写这条规则"靠源码）。
  it('1024 档的样式规则：多门只露组门、组门 44px、一级门在触屏档抬到 44px', () => {
    const source = read('../WorkbenchNav.vue')
    const tablet = source.match(/@media \(max-width: 1024px\) \{([\s\S]*?)\n\}/)
    expect(tablet, '缺少 1024 档的媒体查询').toBeTruthy()

    const block = tablet[1]
    expect(block).toMatch(/\.wb-nav-cell\.is-multi > \.wb-nav-item\s*\{\s*display:\s*none/)
    expect(block).toMatch(/\.wb-nav-cell\.is-multi > \.wb-nav-group\s*\{\s*display:\s*inline-flex/)
    expect(block).toMatch(/\.wb-nav-item\s*\{[^}]*min-height:\s*44px/)
    expect(block).toMatch(/\.wb-nav-group\s*\{[^}]*min-height:\s*44px/)
    // 默认（桌面）那一段组门是隐藏的：桌面不新增第二颗胶囊。
    expect(source).toMatch(/\.wb-nav-group \{ display: none; \}/)
  })
})
