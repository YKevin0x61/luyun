// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'

// 工作台外壳在手机档上的三条（B6 点击目标 / D7 员工不渲染「‹ 后台」/ D8 顶栏别掉成三层），
// 外加 D9：员工卫生页那条页内条退化成标题条之后，牌子与第二颗退出都不在了。
//
// 触控尺寸与换行都是**布局**（jsdom 不做布局、也不解析媒体查询），所以那部分按源码断：
// 仓库里已有先例（`groupDoors.test.js` 的花名册内边距、`hygieneAdminCss.test.js` 的
// 样式契约）。断的是"720 档那条规则里写了 min-height: 44px"这种可复核的事实。
const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')

const SHELL = read('../WorkbenchLayout.vue')
const NAV = read('../../../components/workbench/WorkbenchNav.vue')
const TABBAR = read('../../../components/workbench/WorkbenchTabBar.vue')
const EXIT_BUTTON = read('../../../components/workbench/WorkbenchExitButton.vue')
const SWITCHER = read('../../../components/workbench/WorkbenchIdentitySwitcher.vue')
const HR_SHELL = read('../../scheduling/SchedulingLayout.vue')
const HY_SHELL = read('../../hygiene/HygieneAdminLayout.vue')
const STAFF_HOME = read('../../hygiene/HygieneHomeView.vue')
// 现场壳的共享样式：两份逐字节一致的副本（`utils/__tests__/hygieneAdminCss.test.js` 钉着），
// 这里读正本。
const HYGIENE_CSS = read('../../../../public/hygiene-admin.css')

/** 取出文件里所有的 `@media (max-width: 720px) { ... }` 块（按大括号配对，不靠正则猜边界）。
 *  一个文件里可能有好几段手机档规则（共享样式表里就有），断的是"其中某一段里有这条"——
 *  不绑死"第几段"，免得别人在后面再补一段就红。 */
function mobileBlocks(source) {
  const marker = '@media (max-width: 720px)'
  const blocks = []
  let from = 0
  for (;;) {
    const start = source.indexOf(marker, from)
    if (start === -1) break
    const open = source.indexOf('{', start)
    let depth = 0
    let end = -1
    for (let i = open; i < source.length; i += 1) {
      if (source[i] === '{') depth += 1
      else if (source[i] === '}') {
        depth -= 1
        if (depth === 0) { end = i; break }
      }
    }
    if (end === -1) throw new Error('大括号不配对')
    blocks.push(source.slice(open, end + 1))
    from = end + 1
  }
  expect(blocks.length, `${marker} 一段都没有`).toBeGreaterThan(0)
  return blocks
}

/** 「这些文件里的手机档规则中，有一段满足这个断言」——把"在哪一段"这件事留给写规则的人。 */
function expectMobileRule(source, pattern, label = '') {
  const blocks = mobileBlocks(source)
  expect(
    blocks.some((block) => pattern.test(block)),
    `${label || String(pattern)} 不在任何一段 ≤720px 规则里`,
  ).toBe(true)
}

const ROUTES = [
  { path: '/workbench', component: { template: '<div>今天</div>' } },
  { path: '/workbench/me/today', component: { template: '<div>我的</div>' } },
  { path: '/workbench/hr/calendar', component: { template: '<div>月历</div>' } },
  { path: '/workbench/floor/daily', component: { template: '<div>日常</div>' } },
  { path: '/workbench/kitchen/recipe', component: { template: '<div>配方</div>' } },
  { path: '/workbench/kitchen/prep-plan', component: { template: '<div>备货</div>' } },
]

async function mountShell(path, identity) {
  vi.resetModules()
  localStorage.clear()
  const [{ default: WorkbenchLayout }, { useWorkbenchIdentityStore }] = await Promise.all([
    import('../WorkbenchLayout.vue'),
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
  const wrapper = mount(WorkbenchLayout, { global: { plugins: [router, pinia] } })
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

describe('D7：「‹ 后台」对员工是一扇永远关着的门，就不该渲染', () => {
  it('员工这一档没有「‹ 后台」，但「今天」还在（回得去自己的首页）', async () => {
    const wrapper = await mountShell('/workbench/me/today', 'staff')

    expect(wrapper.find('.wb-back').exists()).toBe(false)
    expect(wrapper.get('a[href="/workbench"]').text()).toBe('今天')
  })

  it('店长那一档照旧：那一扇回管理后台的门在（spec 故事 11 的双向入口）', async () => {
    const wrapper = await mountShell('/workbench', 'super')

    const back = wrapper.get('.wb-back')
    expect(back.text()).toContain('后台')
    expect(back.attributes('href')).toBe('/')
  })
})

describe('D8 / B6：手机档顶栏是两行、目标 ≥44px', () => {
  it('退出并回第一行、导航整条下沉一行（三层的成因是第一行差十几个像素）', () => {
    // 第一行那三件（‹后台 / 切换器 / 退出）在 390 上原来放不下：切换器右边框与内边距
    // 15px、顶栏内边距与间隙 10px，加起来正好把「退出」挤到第二行（实测 104px 三层）。
    expectMobileRule(SHELL, /\.wb-id-switcher\s*\{[^}]*padding-right:\s*0/, '切换器让出右边距')
    expectMobileRule(SHELL, /\.wb-id-switcher\s*\{[^}]*border-right:\s*0/, '切换器去掉右边框')
    // 退出留在第一行靠的是 `margin-left: auto`。它**不在 ≤720 段**：选择器是
    // `:deep(.wb-exit)`，写在基线里（窄屏一样生效）。原来那条挂在外面的
    // `.wb-exit-btn` 选不中元素 —— `WorkbenchExitButton` 是多根组件（按钮 + 确认框），
    // 父级传下来的 class 不透传，所以那条 auto 一直是死规则、退出从来没贴过右端。
    expect(SHELL).toMatch(/:deep\(\.wb-exit\)\s*\{[^}]*margin-left:\s*auto/)
    // C 方向（2026-10-05 用户裁定）：工作台级导航在手机档**下到底栏**，顶栏那一条收起 ——
    // 同一排入口不在顶上和底下同时出现。底栏自己只在 ≤720 渲染（桌面档 `display: none`），
    // 钉在视口底并守 56px 触控下限、给 iPhone 的 home indicator 留安全区。
    expectMobileRule(SHELL, /\.wb-nav\s*\{\s*display:\s*none/, '顶栏那条导航在手机档收起')
    expect(TABBAR).toMatch(/\.wb-tabbar\s*\{\s*display:\s*none/)
    expect(TABBAR).toMatch(/position:\s*fixed/)
    expect(TABBAR).toMatch(/min-height:\s*56px/)
    expect(TABBAR).toMatch(/env\(safe-area-inset-bottom/)
    // 顶栏自己换行（两行），不是逐项掉行。
    expectMobileRule(SHELL, /\.wb-top\s*\{[^}]*flex-wrap:\s*wrap/, '顶栏两行')
  })

  it('外壳四件点击目标 ≥44px：‹后台 / 导航 tab / 退出 / 切换器', () => {
    expectMobileRule(SHELL, /\.wb-back\s*\{[^}]*min-height:\s*44px/, '‹后台')
    expectMobileRule(NAV, /\.wb-nav-item\s*\{[^}]*min-height:\s*44px/, '导航 tab')
    expectMobileRule(EXIT_BUTTON, /\.wb-exit\s*\{[^}]*min-height:\s*44px/, '退出高度')
    expectMobileRule(EXIT_BUTTON, /\.wb-exit\s*\{[^}]*min-width:\s*44px/, '退出宽度')
    // 身份那一颗的 44px 写在**基线**里（不再分桌面/手机两档 —— 它现在是一颗无框的文字按钮，
    // 底下的可点区域一直守 44px），所以这条不按媒体查询断。
    expect(SWITCHER).toMatch(/\.wb-id-current\s*\{[^}]*min-height:\s*44px/)
  })

  it('人事壳同一条账：‹后台与两排导航胶囊在手机档抬到 44px', () => {
    expectMobileRule(HR_SHELL, /\.sched-back\s*\{[^}]*min-height:\s*44px/, '‹后台')
    expectMobileRule(HR_SHELL, /\.sched-nav-item\s*\{[^}]*min-height:\s*44px/, '导航胶囊')
    // 两条导航（工作台级 + 本组四页）在窄屏是**同一条**横滑带子，不各占一行。
    expect(HR_SHELL).toMatch(/\.sched-navbar\s*\{[^}]*display:\s*flex/)
    expectMobileRule(HR_SHELL, /\.sched-navbar\s*\{[^}]*flex-basis:\s*100%/, '导航带下沉一行')
  })

  it('共享件也补齐：现场壳的「后台」、表单按钮与输入框（花名册那种一屏 200+ 控件）', () => {
    expectMobileRule(HYGIENE_CSS, /\.hygiene-admin \.hy-back[^{]*\{[^}]*min-height:\s*44px/, '现场壳的后台')
    expectMobileRule(HYGIENE_CSS, /\.hygiene-admin \.btn,[\s\S]*?min-height:\s*44px/, '表单按钮')
    expectMobileRule(HYGIENE_CSS, /\.hygiene-admin \.hy-person-fields \.input,/, '花名册行内输入框')
    expectMobileRule(HYGIENE_CSS, /\.hygiene-work \.hy-work-today\s*\{[^}]*min-height:\s*44px/, '‹今天')
  })

  it('外壳里那几条布局规则真的落得到子组件上（scoped 要挂到子组件根节点）', async () => {
    // 导航搬进 `WorkbenchNav` 之后，`.wb-nav { margin-left: auto }`、「整条下沉一行」
    // 这些仍然挂在外壳的 scoped 样式里：它们要生效，子组件的根节点必须带上外壳的
    // 作用域标记（Vue 就是这么做的）。jsdom 不做布局，所以只断这个机制在。
    const wrapper = await mountShell('/workbench/me/today', 'super')
    const scopeIds = (selector) => wrapper
      .get(selector)
      .element
      .getAttributeNames()
      .filter((name) => name.startsWith('data-v-'))

    const shellIds = scopeIds('.wb-top')
    expect(shellIds.length).toBeGreaterThan(0)
    expect(scopeIds('.wb-nav').some((id) => shellIds.includes(id))).toBe(true)
  })
})

describe('多根组件不透传 class：三个外壳都不给退出按钮挂 class', () => {
  it('直接挂 <WorkbenchExitButton />，靠 :deep(.wb-exit) 够那颗按钮', () => {
    // `WorkbenchExitButton` 的模板是多根（`<button>` + 那个「还有照片没传完」的
    // `<ConfirmDialog>`）：外面挂的 class 传不进按钮，Vue 还会报
    // `Extraneous non-props attributes`。原来三个壳各自挂了一颗
    // （`.wb-exit-btn` / `.sched-exit` / `.hy-exit`），规则全是死的。
    for (const [name, source] of [['工作台壳', SHELL], ['人事壳', HR_SHELL], ['现场壳', HY_SHELL]]) {
      expect(source, name).toMatch(/<WorkbenchExitButton\s*\/>/)
      expect(source, name).not.toMatch(/<WorkbenchExitButton[^>]*class=/)
    }
  })

  it('挂载外壳不再出那条 attrs 继承警告（原来那颗 class 就是警告的来源）', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    await mountShell('/workbench', 'super')

    const hits = warn.mock.calls.filter(([first]) =>
      String(first).includes('Extraneous non-props attributes'))
    expect(hits).toEqual([])
  })
})

describe('D9：员工卫生页那条页内条退化成标题条', () => {
  it('牌子（红色「台」+「工作台」）与第二颗退出都不在了，只留那颗只读胶囊', () => {
    // 品牌与退出归外壳顶栏。页内那条 `‹ 今天` 在 2026-10-05 的卫生页重构里去掉了 ——
    // 底部工作台底栏的「我的」指向的正是 `/workbench/me/today`，同一个目的地不该有两个
    // 入口（PWA 独立窗口那条回程的需求由底栏那一格满足）。
    expect(STAFF_HOME).not.toMatch(/class="hy-brand"/)
    expect(STAFF_HOME).not.toMatch(/<StaffExitButton/)
    expect(STAFF_HOME).not.toMatch(/class="hy-work-today"/)
    expect(STAFF_HOME).toMatch(/class="hy-work-shift"/)
  })

  it('那颗只读胶囊不再被 50% 的宽度压到比内容还窄（原来 scrollWidth 187 / clientWidth 177）', () => {
    expect(HYGIENE_CSS).toMatch(/\.hygiene-work \.hy-work-shift\s*\{[^}]*max-width:\s*100%/)
  })
})
