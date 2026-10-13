// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { WORKBENCH_FIELD_HOME, WORKBENCH_HR_HOME } from '../../../utils/workbenchCopy.js'
import { PREP_PLAN_PATH } from '../../../utils/prepPlanPaths.js'
import { RECIPE_DETAIL_PATH, RECIPE_PRINT_PATH } from '../../../utils/recipePaths.js'
import { PAGE_ROUTES, pageRow } from '../../../router/pageRoutes.js'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')
/** 一份样式源码里某条规则的声明块（同一个选择器可能出现多次，全给出来）。 */
function ruleBodies(source, selector) {
  const bodies = []
  let from = 0
  for (;;) {
    const at = source.indexOf(`${selector} {`, from)
    if (at === -1) return bodies
    const end = source.indexOf('}', at)
    bodies.push(source.slice(at, end + 1))
    from = end + 1
  }
}

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
  // 票 02（内容区）：opt-out 两个字段的落点页 —— 配方阅读面「自带底栏」、配方打印「铺满」。
  // 它们本来就是路由里的页面（这里只补两条供挂载用）。
  { path: RECIPE_DETAIL_PATH, component: { template: '<div>配方详情</div>' }, meta: { audience: 'both', standalone: true } },
  { path: RECIPE_PRINT_PATH, component: { template: '<div>配方打印</div>' }, meta: { audience: 'both', standalone: true } },
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
    // 会话探针（未登录也是 200）：判据在 body 里，不再拿 /staff/me 的 401 当答案。
    if (path.includes('/api/hygiene/staff/session')) return jsonResponse({ authenticated: true, employee: { name: '张三' } })
    if (path.includes('/api/hygiene/staff/me')) return jsonResponse({ employee: { name: '张三' } })
    return jsonResponse({}, 404)
  })
}

/** 只有员工会话：员工自己的手机。 */
function staffOnlyFetch() {
  return vi.fn(async (url) => {
    const path = String(url)
    if (path.includes('/api/auth/status')) return jsonResponse({ logged_in: false, initialized: true })
    // 会话探针（未登录也是 200）：判据在 body 里，不再拿 /staff/me 的 401 当答案。
    if (path.includes('/api/hygiene/staff/session')) return jsonResponse({ authenticated: true, employee: { name: '张三' } })
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

// ── 内容区让位的两个输入（票 03）──────────────────────────────────────────────
// 两个真实来源在 jsdom 里都量不到：底栏的几何要布局，媒体查询要解析 CSS。所以壳问
// `matchMedia`、读令牌层的 `--safe-b`，测试替这两处回答 —— 断言的是壳**算出来**的那个数。

/** 手机档：底栏那条只在 ≤720px 渲染（`WorkbenchTabBar.vue` 自己的媒体查询）。 */
function stubMobileTier({ matches = true } = {}) {
  vi.stubGlobal('matchMedia', (query) => ({
    media: query, matches, addEventListener() {}, removeEventListener() {},
  }))
}

/** 两个档位各答各的（票 04 起壳问两条：720 的底栏档、900 的内边距宽档）。 */
function stubTiers({ mobile = false, wide = false } = {}) {
  vi.stubGlobal('matchMedia', (query) => ({
    media: query,
    matches: /min-width/.test(query) ? wide : mobile,
    addEventListener() {}, removeEventListener() {},
  }))
}

/** 安全区：生产里令牌层写的是 `env(safe-area-inset-bottom, 0px)`，浏览器自己解成 px；
 *  这里直接把它摆成算好的值 —— 注入点就是令牌层那一根（壳读的就是它）。 */
function stubSafeBottom(raw) {
  document.documentElement.style.setProperty('--safe-b', raw)
}

/** 内容容器上算出来的下 inset，例如 `57px` / `91px` / `0px`。 */
function insetOf(wrapper, name = '--wb-inset-bottom') {
  return wrapper.get('#workbench-main').element.style.getPropertyValue(name)
}
afterEach(() => {
  document.documentElement.style.removeProperty('--safe-b')
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

// 票 02（内容区）：opt-out 不再是一份**手写在壳里的路径名单**，而是页面清单上的两个可选
// 字段 —— 壳只读清单渲染。两件事各管各的，`ownBottomBar` 不再隐含 `flush`（ADR 0106
// 决策 5）。这组用例断的是**渲染出来的东西**（底栏在不在、让位的钩子在不在），不是源码
// 写法：清单那一行改一下，断言就跟着变。
describe('opt-out 两个字段从页面清单派生（票 02）', () => {
  /** 清单里今天真实使用某一页的路径 —— 这一组用例的**输入就是清单自身**：
   *  手写名单满足不了它（名单与清单是两份），这正是红→绿那一步。 */
  function pagesUsing(field) {
    return PAGE_ROUTES.filter((row) => row[field] === true).map((row) => row.path)
  }

  it('清单里点名的「自带底栏」页不渲染工作台底栏，其余页面照旧渲染', async () => {
    const ownBarPages = pagesUsing('ownBottomBar')
    // 防空转：这一组要真的有人在用，否则下面两个循环都会空转着绿。
    expect(ownBarPages, '清单里没有一页用 ownBottomBar').toContain(RECIPE_DETAIL_PATH)
    expect(ownBarPages).not.toContain(PREP_PLAN_PATH)

    for (const path of ownBarPages) {
      const { wrapper } = await mountShell(path)
      // 手机档底栏与页内那条 `.sop-bottom-bar` 只差 4px，两条都在就是上面那条被整个盖住
      // （2026-10-08 用户裁定）—— 所以这一页整条不渲染。
      expect(wrapper.find('.wb-tabbar').exists(), `${path} 不该叠工作台底栏`).toBe(false)
    }

    const { wrapper } = await mountShell(PREP_PLAN_PATH)
    expect(wrapper.find('.wb-tabbar').exists()).toBe(true)
  })

  it('「自带底栏」页把手机档那份底部让位还回去，其余页面留着', async () => {
    stubMobileTier()
    const ownBar = await mountShell(RECIPE_DETAIL_PATH)
    // jsdom 不做布局、也不解析媒体查询，所以断的是**壳算出来并挂到内容容器上的那个数**
    // （票 03 起 inset 是壳算的：底栏没渲染就不留它的高度）—— 规则本身由 shellMobileChrome
    // 那组断。
    expect(insetOf(ownBar.wrapper)).toBe('0px')

    const plain = await mountShell(PREP_PLAN_PATH)
    expect(insetOf(plain.wrapper)).toBe('57px')
  })

  it('两个字段互不隐含：配方打印页只「铺满」、不自带底栏', async () => {
    // ADR 0106 决策 5 点名的那个反例：配方打印是 A4 预览页（要铺满），它顶上有自己那条
    // 打印工具条、底下没有自带底栏 —— 于是工作台底栏照渲染、让位照留。
    expect(pageRow(RECIPE_PRINT_PATH).flush, '配方打印该标 flush').toBe(true)
    expect(pageRow(RECIPE_PRINT_PATH).ownBottomBar).toBeUndefined()

    stubMobileTier()
    const { wrapper } = await mountShell(RECIPE_PRINT_PATH)
    expect(wrapper.find('.wb-tabbar').exists()).toBe(true)
    // **票 04 起「铺满」被消费**：横向与上内边距算出来是 0（A4 预览按纸张宽度排版，
    // 多留一圈就摆不下）；底下那条让位照留 57px（铺满只免横向与上，不免底栏那一条）。
    expect(insetOf(wrapper)).toBe('57px')
  })

  it('「铺满」的判据是这个字段，不是"组恰好是 0"', async () => {
    // 后勤组今天本来就是 0，光断打印页看不出这条字段有没有被消费 —— 所以拿一个
    // **组值非零**的组做对照：同一份壳，铺满的页 0、同壳里非铺满的页拿到组值。
    stubTiers({ mobile: true, wide: true })
    const print = await mountShell(RECIPE_PRINT_PATH)
    expect(insetOf(print.wrapper, '--wb-inset-top')).toBe('0px')
    expect(insetOf(print.wrapper, '--wb-inset-inline')).toBe('0px')

    const plain = await mountShell('/workbench/floor/daily')
    expect(insetOf(plain.wrapper, '--wb-inset-top')).not.toBe('0px')
  })
})

// 票 03（内容区）：壳第一次持有内容容器与下 inset。三根变量（`--wb-inset-top` /
// `--wb-inset-bottom` / `--wb-inset-inline`）只在壳内部定义与消费，**不发布到
// `<html data-workbench>`**（那是主题层，ADR 0106 决策 4）；下 inset 的判据是
// 「本屏此刻渲染了底栏吗」——**不看 `audience`**（2026-10-13 基线实测：管理端手机档
// 同样有那条 57px 底栏）。
//
// 这里的断言全是**渲染出来的数**（`<main>` 上那三个自定义属性）：底栏高与安全区都是
// 壳的输入，测试把值注入进去，不量真实几何（jsdom 也不做布局）。
describe('内容区的下 inset（票 03）', () => {
  it('管理端手机档：底栏在屏上，下 inset = 57px（不许被算成 0）', async () => {
    stubMobileTier()
    const { wrapper } = await mountShell('/workbench/floor/daily')

    expect(insetOf(wrapper)).toBe('57px')
  })

  it('员工端手机档：同一笔账 —— 两档的 inset 一样，判据里没有 audience', async () => {
    stubMobileTier()
    const admin = await mountShell('/workbench/floor/daily')
    vi.stubGlobal('fetch', staffOnlyFetch())
    const staff = await mountShell('/workbench/me/today')

    expect(insetOf(staff.wrapper)).toBe('57px')
    expect(insetOf(staff.wrapper)).toBe(insetOf(admin.wrapper))
  })

  it('安全区是可注入的输入：34px → 57 + 34 = 91px', async () => {
    stubMobileTier()
    stubSafeBottom('34px')
    const { wrapper } = await mountShell('/workbench/me/today')

    expect(insetOf(wrapper)).toBe('91px')
  })

  it('桌面档：底栏 `display: none`，下 inset 与今天一致（无下留白 ⇒ 0）', async () => {
    // jsdom 里没有 `matchMedia`（等于桌面档），这里显式再钉一次 false 的那条路。
    stubMobileTier({ matches: false })
    const { wrapper } = await mountShell('/workbench/floor/daily')

    expect(insetOf(wrapper)).toBe('0px')
  })

  it('拖窗口跨过 720px：让位跟着变（不是只在挂载时算一次）', async () => {
    // 底栏跟不跟着出现是 CSS 的事（≤720px 才 `display: flex`），所以壳必须订阅那条媒体查询
    // 回调重算 —— 只在挂载时算一次的话，桌面窗口被拖窄之后底栏冒出来、内容区却不给它让位。
    // **票 04 起壳订阅两条**（720 的底栏档 + 900 的内边距宽档），所以这里按 query 分开记：
    // 这一条驱动的是底栏那一条，另一条的监听在下面「宽档」那条里断。
    const added = []
    const removed = []
    let mobileMatches = false
    vi.stubGlobal('matchMedia', (query) => ({
      media: query,
      get matches() { return /max-width/.test(query) ? mobileMatches : false },
      addEventListener(type, fn) { if (type === 'change') added.push({ query, fn }) },
      removeEventListener(type, fn) { if (type === 'change') removed.push({ query, fn }) },
    }))
    const { wrapper } = await mountShell('/workbench/floor/daily')
    const mobileHandlers = added.filter((it) => /max-width/.test(it.query))
    expect(mobileHandlers, '挂载时就该订阅底栏那条媒体查询').toHaveLength(1)
    expect(insetOf(wrapper)).toBe('0px')

    mobileMatches = true
    mobileHandlers.forEach(({ fn }) => fn({ matches: true }))
    await flushPromises()
    expect(insetOf(wrapper)).toBe('57px')

    mobileMatches = false
    mobileHandlers.forEach(({ fn }) => fn({ matches: false }))
    await flushPromises()
    expect(insetOf(wrapper)).toBe('0px')

    wrapper.unmount()
    expect(removed, '卸载时要退订（别在别人的页上留监听）').toEqual(added)
  })

  it('三根度量都套在内容容器上，且不外发：`<html>` 与壳根都没有它们', async () => {
    stubMobileTier()
    const { wrapper } = await mountShell('/workbench/me/today')
    const main = wrapper.get('#workbench-main').element

    for (const name of ['--wb-inset-top', '--wb-inset-bottom', '--wb-inset-inline']) {
      expect(main.style.getPropertyValue(name), `${name} 该挂在内容容器上`).not.toBe('')
    }
    // 不发布到 `<html data-workbench>`（令牌层是主题的东西）——
    // `<html>` 上挂着的只有 `data-workbench` 这个开关本身。
    expect(document.documentElement.style.getPropertyValue('--wb-inset-bottom')).toBe('')
    expect(document.body.style.getPropertyValue('--wb-inset-bottom')).toBe('')
    // 壳根与顶栏也读不到（它们是内容容器的祖先/兄弟，自定义属性只往下继承）。
    const root = wrapper.get('.wb-shell').element
    expect(root.style.getPropertyValue('--wb-inset-bottom')).toBe('')
    expect(wrapper.get('.wb-top').element.style.getPropertyValue('--wb-inset-bottom')).toBe('')
  })

  it('自带底栏的页：底栏整条不渲染，下 inset 0（安全区也不加）', async () => {
    stubMobileTier()
    stubSafeBottom('34px')
    const { wrapper } = await mountShell(RECIPE_DETAIL_PATH)

    expect(wrapper.find('.wb-tabbar').exists()).toBe(false)
    expect(insetOf(wrapper)).toBe('0px')
  })
})

// 票 04（内容区）：横向与上内边距按页面清单里的 `group` 查表给（ADR 0106 决策 3），
// 页面与共享样式表不再出这一份。三组数值不同是现状的事实，本票**不归一**：
// 每一行的取值就是收口前那一处的原文，浏览器解算式的时候看到的还是同一个数。
//
// 这一组**三组各一条**（票面验收 5），每条断两件事：
//   ① 壳给这一组算出来的那两根变量（渲染出来的 inline style，jsdom 能读）；
//   ② 这一组原来那一份内边距在页面上**不再有**（页面 / 共享样式表那一侧，按源码断 ——
//      jsdom 不做布局、也不解析 SFC 的 `<style>`）。
describe('内容区的横向与上内边距按组给（票 04）', () => {
  it('现场八页：值来自共享样式表那条 `.hy-main`，那一侧的上 / 横向已经交出去', async () => {
    stubTiers({ mobile: true, wide: false })
    const { wrapper } = await mountShell('/workbench/floor/daily')

    // base 档（<900px）：`padding: var(--hy-page)` + 左右 `max(...)`
    expect(insetOf(wrapper, '--wb-inset-top')).toBe('var(--hy-page)')
    expect(insetOf(wrapper, '--wb-inset-inline'))
      .toBe('max(var(--hy-page), var(--safe-l), var(--safe-r))')

    // 宽档（≥900px）：共享样式表那条媒体查询里的上 / 横向 clamp（第三个数 2rem 是**下**
    // 内边距，属于页面正文自己的收尾留白，不搬）。
    stubTiers({ mobile: false, wide: true })
    const wide = await mountShell('/workbench/floor/daily')
    expect(insetOf(wide.wrapper, '--wb-inset-top')).toBe('clamp(1.1rem, 2.2vw, 2rem)')
    expect(insetOf(wide.wrapper, '--wb-inset-inline')).toBe('clamp(1.2rem, 3vw, 2.6rem)')

    // 页面侧：`.hygiene-admin .hy-main` 只剩页面正文自己的下内边距与宽度约束。
    const bodies = ruleBodies(read('../../../../public/hygiene-admin.css'),
      '.hygiene-admin .hy-main')
    expect(bodies.length, '共享样式表里没有 `.hy-main` 这条了？').toBeGreaterThan(0)
    for (const body of bodies) {
      expect(body, `.hy-main 还在出横向 / 上内边距：${body}`)
        .not.toMatch(/padding(-top|-left|-right)?\s*:(?!\s*2rem)/)
      expect(body).toMatch(/padding-bottom\s*:/)
    }
  })

  it('人事组：组壳那条补内边距退场，值随它落到花名册自己的样式里（数字一字不差）', async () => {
    stubTiers({ mobile: true, wide: false })
    const { wrapper } = await mountShell('/workbench/hr/roster')

    // 组里另外五页（月历 / 待办 / 班次表 / 人事提醒 / 加班统计）的根自带内边距，
    // 组级给一个非零值会把它们推走 —— 所以壳给人事组 0，花名册那份跟着老规则走。
    expect(insetOf(wrapper, '--wb-inset-top')).toBe('0px')
    expect(insetOf(wrapper, '--wb-inset-inline')).toBe('0px')

    // 页面侧：组壳里不再有内边距规则；花名册自己那一份与老规则逐字相同（含宽档那条）。
    const shell = read('../../scheduling/SchedulingLayout.vue')
    expect(shell).not.toMatch(/padding/)
    const roster = read('../../hygiene/HygieneRosterView.vue')
    expect(roster).toMatch(/\.roster-page\s*\{[^}]*padding:\s*var\(--hy-page\)/)
    expect(roster).toMatch(/padding-left:\s*max\(var\(--hy-page\),\s*var\(--safe-l\)\)/)
    expect(roster).toMatch(/padding-right:\s*max\(var\(--hy-page\),\s*var\(--safe-r\)\)/)
    expect(roster).toMatch(/padding:\s*clamp\(1\.1rem,\s*2\.2vw,\s*2rem\)\s+clamp\(1\.2rem,\s*3vw,\s*2\.6rem\)\s+2rem/)
  })

  it('工作台首页（`home`）：值来自页面自己那条 `.wbh`，页面侧只剩正文自己的下内边距', async () => {
    // 票面的「三组」只点了现场 / 人事 / 我的，而清单里会套统一壳的组不止三个 ——
    // 首页这一组（一页）同样收口：值与 `.wbh` 原来那条逐字相同，两个档通用。
    stubTiers({ mobile: true, wide: false })
    const { wrapper } = await mountShell('/workbench')

    expect(insetOf(wrapper, '--wb-inset-top')).toBe('max(var(--hy-page, 18px), var(--safe-t))')
    expect(insetOf(wrapper, '--wb-inset-inline'))
      .toBe('max(var(--hy-page, 18px), var(--safe-l), var(--safe-r))')

    const body = ruleBodies(read('../../workbench/WorkbenchHomeView.vue'), '.wbh')
      .find((text) => text.includes('.wbh {'))
    expect(body, 'WorkbenchHomeView 里没有 `.wbh` 的基线规则了？').toBeTruthy()
    expect(body).not.toMatch(/padding\s*:/)
    expect(body).toMatch(/padding-bottom:\s*calc\(40px \+ var\(--safe-b\)\)/)
  })

  it('我的组：四页各由自己的容器给（今天 / 整月 / 卫生的根是 0，页头自己带），壳给 0', async () => {
    vi.stubGlobal('fetch', staffOnlyFetch())
    stubTiers({ mobile: true, wide: false })
    const { wrapper } = await mountShell('/workbench/me/today')

    expect(insetOf(wrapper, '--wb-inset-top')).toBe('0px')
    expect(insetOf(wrapper, '--wb-inset-inline')).toBe('0px')

    // 页面侧：`.today-page` 只有下内边距（收尾留白 + 安全区），横向与上在 `.tTop` 上 ——
    // 那一份是页头自己的，不是「内容区内边距」（搬它会把页内其它几块一起推走）。
    const body = ruleBodies(read('../../today/TodayView.vue'), '.today-page')
      .find((text) => text.includes('.today-page {'))
    expect(body, 'TodayView 里没有 `.today-page` 的基线规则了？').toBeTruthy()
    expect(body).not.toMatch(/padding\s*:/)
    expect(body).toMatch(/padding-bottom:\s*calc\(80px \+ var\(--safe-b\)\)/)
  })
})
