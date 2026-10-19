// @vitest-environment jsdom
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
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
//
// ── 两类东西的界线（票 07 / ADR 0106 Consequences）─────────────────────────────
// 本文件里那几条「不许出现 X」是**有意为之的源码断言**，不是偷懒的行为测试：
//   · 安全区只在令牌层写一遍（`env(safe-area-inset-*)` 不许在页面里直写）；
//   · 页面区之外不许有第二个滚动容器（`.hy-main` / `.hy-work-main` 不自己滚）；
//   · 三根内容区度量只在统一壳里（没有第二处定义、壳外没有消费者）。
// 它们盯的是「一个名字 / 一条声明有没有被写进源码」这种**文本判定即结论**的事实 ——
// 写没写 `overflow-y: auto` 不需要跑起来看。这跟「拿源码文本去断**行为**」是两回事：
// 「这一页滚得动」「底栏没盖住最后一块内容」「内边距落在屏幕上是不是那个数」必须靠浏览器里
// 量（`.scratch/workbench-content-area/baseline/checks-0N/` 那套 Playwright 探针；
// 票 07 那一轮在 `checks-07/`），jsdom 不做布局、也解析不了媒体查询。
// 两类混起来写，下一个维护者就会以为文本断言能替代真机核对，或者反过来把这类断言当反模式
// 清掉。**同一条界线在 `src/styles/__tests__/designGuard.test.js` 的文件头也写了一遍**
// （那边管层级棘轮与裸 z-index 数字），两处说的是同一件事。
const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, rel), 'utf8')

/** 剥掉源码里的注释，三种都剥（票 07）：
 *   · Vue 模板注释（`<!--` 到 `-->`）；
 *   · 块注释（斜杠星号那一对，CSS 与 JS 里都有）；
 *   · 行注释（双斜杠到行尾）。
 *
 *  **为什么要这么一层**（票 07 修掉的那条假红）：「三根名字只出现在统一壳里」这条的判据是
 *  「这个名字有没有被写进源码」，而**注释里追述一句**既不是定义也不是消费 —— 它却会把这条
 *  判红。2026-10-13 实测过：并行工作流在 `SchedulingCalendarView.vue` 的一句注释里写了
 *  `--wb-inset-bottom`，于是全量 `npm test` 一直红这一条（HEAD 里 0 次、工作区 1 次，
 *  与本组票无关）；本文件里那条「假红复现」的变异记录在
 *  `.scratch/workbench-content-area/baseline/checks-07/`。
 *
 *  剥掉注释之后这条仍然盯得住真违规：**第二处定义**（`--wb-inset-*:` 那种声明）与
 *  **壳外的消费者**（`var(--wb-inset-*)`）都写在声明 / 表达式里，剥注释剥不掉它们 ——
 *  要挡的正是那两样（ADR 0106 决策 4）。
 *
 *  引号里的内容**不剥**：壳自己就是用字符串 `'--wb-inset-top'` 定义这三根的，
 *  字符串里出现这个名字同样是「写进了源码」。
 *
 *  实现按字符扫一遍、不靠正则猜边界；唯一的近似是 JS 正则字面量里的双斜杠会被当成行注释，
 *  把同一行后面的内容一起剥掉 —— 那只会**漏判**（假绿），不会误判，方向是安全的那一侧。 */
function stripComments(source) {
  let out = ''
  let quote = null
  for (let i = 0; i < source.length;) {
    const ch = source[i]
    const next = source[i + 1]
    if (quote) {
      out += ch
      if (ch === '\\') { out += next ?? ''; i += 2; continue }
      if (ch === quote) quote = null
      i += 1
      continue
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; out += ch; i += 1; continue }
    if (ch === '/' && next === '/') { while (i < source.length && source[i] !== '\n') i += 1; continue }
    if (ch === '/' && next === '*') {
      i += 2
      while (i < source.length && !(source[i] === '*' && source[i + 1] === '/')) i += 1
      i += 2
      continue
    }
    if (ch === '<' && source.startsWith('<!--', i)) {
      const end = source.indexOf('-->', i)
      i = end === -1 ? source.length : end + 3
      continue
    }
    out += ch
    i += 1
  }
  return out
}

// ③-2（三壳合一）：壳级样式与手机档那一条现在只有一份，在统一壳里；三个布局都是它的薄包装。
const SHELL = read('../WorkbenchShell.vue')
const TABBAR = read('../../../components/workbench/WorkbenchTabBar.vue')
const EXIT_BUTTON = read('../../../components/workbench/WorkbenchExitButton.vue')
const SWITCHER = read('../../../components/workbench/WorkbenchIdentitySwitcher.vue')
// 方案 C（2026-10-08）：手机档顶部那一行（组名 / 身份 / 更多）。
// 同日第二版：页面名退出顶栏，换页入口挪到组名上（组名因此也是按钮）。
const MOBILE_HEAD = read('../../../components/workbench/WorkbenchMobileHead.vue')
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
  { path: '/workbench/staff/today', component: { template: '<div>我的</div>' } },
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
    const wrapper = await mountShell('/workbench/staff/today', 'staff')

    expect(wrapper.find('.wb-back').exists()).toBe(false)
    // ③-2：工作台级入口在手机档由底栏那一条承担（顶栏那排 `display: none`）。
    expect(wrapper.get('.wb-tabbar a[href="/workbench"]').text()).toBe('今天')
  })

  it('店长那一档照旧：那一扇回管理后台的门在（spec 故事 11 的双向入口）', async () => {
    const wrapper = await mountShell('/workbench', 'super')

    const back = wrapper.get('.wb-back')
    expect(back.text()).toContain('后台')
    expect(back.attributes('href')).toBe('/')
  })
})

describe('方案 C / B6：手机档顶部只剩一行，目标 ≥44px', () => {
  it('顶上那一条整个收进页头一行（2026-10-08）', () => {
    // 工作台级导航（今天 / 人事 / 卫生 / 产品 / 我的）在手机档**下到底栏**（C 方向，
    // 2026-10-05）：同一排入口不在顶上和底下同时出现。底栏只在 ≤720 渲染，钉在视口底、
    // 守 56px 触控下限、给 iPhone 的 home indicator 留安全区。
    expect(TABBAR).toMatch(/\.wb-tabbar\s*\{\s*display:\s*none/)
    expect(TABBAR).toMatch(/position:\s*fixed/)
    expect(TABBAR).toMatch(/min-height:\s*56px/)
    expect(TABBAR).toMatch(/env\(safe-area-inset-bottom/)
    // 方案 C：连顶上那一条（`‹后台 + 牌子 + 切换器 + 退出`）在手机档也收掉 —— 三件事都进
    // `WorkbenchMobileHead` 那一行：换页进组内下拉、退出与「回管理后台」进 `⋮`。
    // 后勤（产品）组的配方页原来还自带一条 133px 的白色工具栏，叠起来是 182px 的「两条顶栏」。
    expectMobileRule(SHELL, /\.wb-top\s*\{\s*display:\s*none/, '顶栏在手机档收起')
    expect(SHELL).toMatch(/components\/workbench\/WorkbenchMobileHead\.vue/)
    expect(SHELL).toMatch(/<WorkbenchMobileHead[^>]*\/>/)
    // 底栏是 `fixed`，内容区得自己让出那一条的高度（57px = 底栏自身 + 1px 边框）。
    // **票 03 起这份让位不写在媒体查询里**：底栏高 + 安全区由壳算成一个数挂在内容容器上
    // （`--wb-inset-bottom`，桌面档算出来就是 0），规则只剩「谁消费」这一条 —— 否则
    // 「本屏渲染了底栏吗」会有两处各答一半（媒体查询一处、壳的算法一处）。
    expect(SHELL).toMatch(/\.wb-main\s*\{[^}]*padding:[^}]*var\(--wb-inset-bottom/)
    // 退出那颗的「贴右端」仍写在基线里（桌面档那条顶栏还在用它）：`:deep(.wb-exit)` 是必须的
    // —— `WorkbenchExitButton` 是多根组件（按钮 + 确认框），父级传下来的 class 不透传。
    expect(SHELL).toMatch(/:deep\(\.wb-exit\)\s*\{[^}]*margin-left:\s*auto/)
  })

  it('外壳（含页头）点击目标 ≥44px：组名（换页入口）/ 更多 / 下拉项 / 退出 / 切换器', () => {
    // 顶栏收进页头之后，手机上真正要点的是页头里这几件 —— 逐个守住下限。
    // 页头那三件写在**基线**里（所以不按媒体查询断）：整条只在手机档渲染
    // （`.wmh` 平时是 `display: none`），尺寸规则没有分档的必要。
    // 组名兼换页入口（2026-10-08 第二版）：页面名已退出顶栏，热区仍照 44px 守。
    expect(MOBILE_HEAD).toMatch(/\.wmh-grp\s*\{[^}]*min-height:\s*44px/)
    expect(MOBILE_HEAD).toMatch(/\.wmh-more\s*\{[^}]*width:\s*44px/)
    expect(MOBILE_HEAD).toMatch(/\.wmh-item\s*\{[^}]*min-height:\s*48px/)
    expectMobileRule(EXIT_BUTTON, /\.wb-exit\s*\{[^}]*min-height:\s*44px/, '退出高度')
    expectMobileRule(EXIT_BUTTON, /\.wb-exit\s*\{[^}]*min-width:\s*44px/, '退出宽度')
    // 身份那一颗的 44px 写在**基线**里（不再分桌面/手机两档 —— 它现在是一颗无框的文字按钮，
    // 底下的可点区域一直守 44px），所以这条不按媒体查询断。
    expect(SWITCHER).toMatch(/\.wb-id-current\s*\{[^}]*min-height:\s*44px/)
  })

  it('人事壳同一条账：手机档顶部整个收进页头一行（2026-10-08）', () => {
    // 顶上那一条在手机档 `display: none`：本组四页与退出 / 回后台都进 `WorkbenchMobileHead`。
    // （原来那条"‹后台与两排导航胶囊抬到 44px"的账随之作废 —— 那些目标在手机档不再渲染；
    //  页头自己的触控下限在上一段里逐个断着。）
    expectMobileRule(SHELL, /\.wb-top\s*\{\s*display:\s*none/, '顶栏在手机档收起')
    expect(SHELL).toMatch(/components\/workbench\/WorkbenchMobileHead\.vue/)
    expect(SHELL).toMatch(/<WorkbenchMobileHead[^>]*\/>/)
    // 桌面档那条带子照旧：工作台级一排 + 本组四页共处一行（收的是手机档那一份）。
    expect(SHELL).toMatch(/<WorkbenchRail/)
    // 底栏仍是手机档的主导航：钉在视口底，内容让出那一条（票 03 起那个数由壳算，
    // 见上面「顶上那一条整个收进页头一行」里那条消费点）。
    expect(SHELL).toMatch(/\.wb-main\s*\{[^}]*padding:[^}]*var\(--wb-inset-bottom/)
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
    const wrapper = await mountShell('/workbench/staff/today', 'super')
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

// 票 03（内容区）：三根让位度量（`--wb-inset-top` / `--wb-inset-bottom` /
// `--wb-inset-inline`）是**壳的私事**，不是主题令牌（ADR 0106 决策 4）—— 只在壳内部定义与
// 消费：壳外读不到、不发布到 `<html data-workbench>`、没有第二处定义。
// 「读不到」由 `workbenchShell.test.js` 那组 DOM 契约断（挂上之后 `<html>` 与壳根上都没有）；
// 这里断**定义点只有一个**，以及壳问的档位与底栏自己的媒体查询说的是同一个数。
describe('内容区的三根度量只在壳里（票 03）', () => {
  const INSETS = ['--wb-inset-top', '--wb-inset-bottom', '--wb-inset-inline']
  // 仓库根（`admin-web/`）：这个文件在 `src/views/workbench/__tests__/` 下。
  const ROOT = join(here, '../../../..')

  /** 扫 `src/` 与 `public/` 的源码（跳过 `__tests__` / dist）：谁提到了这三根名字。
   *  **先剥注释**（票 07）：注释里追述一句变量名既不是定义也不是消费，不该把这条判红 ——
   *  理由与那次假红的实测见文件顶部 `stripComments` 的注释。 */
  function sourcesMentioning(names) {
    const hits = []
    const walk = (dir) => {
      for (const name of readdirSync(dir)) {
        const p = join(dir, name)
        if (statSync(p).isDirectory()) {
          if (!/(__tests__|node_modules|dist)$/.test(name)) walk(p)
        } else if (/\.(vue|js|css)$/.test(name)) {
          const src = stripComments(readFileSync(p, 'utf8'))
          if (names.some((n) => src.includes(n))) hits.push(relative(ROOT, p))
        }
      }
    }
    for (const dir of ['src', 'public']) walk(join(ROOT, dir))
    return hits.sort()
  }

  it('三根名字只出现在统一壳这一个文件里（页面 / 样式表 / 令牌层一处都没有）', () => {
    // 唯一来源：壳里 `contentInsetStyle` 定义它们、`.wb-main` 消费它们。多一处出现就是
    // 「第二处定义」或壳外的消费者 —— 那正是 ADR 0106 决策 4 要挡的。
    // **这只是「剥掉注释后」的结论**：注释里提到名字不算违规（票 07 改），
    // 声明与消费（含字符串里的名字）照旧算。
    expect(sourcesMentioning(INSETS)).toEqual(['src/views/workbench/WorkbenchShell.vue'])
  })

  it('令牌层不认它们：`html[data-workbench]` 里没有这三根（度量不进全局命名空间）', () => {
    const theme = read('../../../styles/theme.workbench.css')
    for (const name of INSETS) expect(theme).not.toContain(name)
  })

  it('壳问的档位与底栏自己那条媒体查询是同一个数（漂开＝凭空多 / 少 57px）', async () => {
    // 底栏在桌面档是 `display: none`（组件自己那条 ≤720px 媒体查询），所以壳算下 inset 时
    // 必须先问出同一个档位。两边各写一个数、谁也不看着谁，就是「桌面档凭空多出 57px 下留白」
    // 或「手机档少了 57px、最后一块内容被底栏压住」的来源。
    const { MOBILE_MAX_WIDTH_PX } = await import('../../../utils/workbenchInsets.js')
    const bpOf = (src) => [...new Set([...src.matchAll(/max-width:\s*([0-9]+)px/g)].map((m) => m[1]))]

    expect(bpOf(TABBAR), '底栏组件的手机档断点').toEqual([String(MOBILE_MAX_WIDTH_PX)])
    expect(bpOf(SHELL), '壳自己的手机档断点里也要有它').toContain(String(MOBILE_MAX_WIDTH_PX))
  })
})

describe('多根组件不透传 class：三个外壳都不给退出按钮挂 class', () => {
  it('直接挂 <WorkbenchExitButton />，靠 :deep(.wb-exit) 够那颗按钮', () => {
    // `WorkbenchExitButton` 的模板是多根（`<button>` + 那个「还有照片没传完」的
    // `<ConfirmDialog>`）：外面挂的 class 传不进按钮，Vue 还会报
    // `Extraneous non-props attributes`。原来三个壳各自挂了一颗
    // （`.wb-exit-btn` / `.sched-exit` / `.hy-exit`），规则全是死的。
    // ③-2：三壳合一之后唯一那颗退出按钮在统一壳里；两个布局只是包装，别在它们身上找。
    // ③-2：唯一那颗退出按钮在统一壳里（三个布局只是它的包装，别在它们身上找）。
    expect(SHELL).toMatch(/<WorkbenchExitButton\s*\/>/)
    expect(SHELL).not.toMatch(/<WorkbenchExitButton[^>]*class=/)
    for (const [name, source] of [['人事壳（包装）', HR_SHELL], ['现场壳（包装）', HY_SHELL]]) {
      expect(source, name).toMatch(/<WorkbenchShell/)
      expect(source, name).not.toMatch(/<WorkbenchExitButton/)
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
    // 底部工作台底栏的「我的」指向的正是 `/workbench/staff/today`，同一个目的地不该有两个
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

// 票 04（内容区）：横向与上内边距搬进壳之后，两条「不许再出现」型护栏（ADR 0106 决策 8）。
// 断的是源码里可复核的事实 —— 布局与媒体查询 jsdom 都不做，这些是"下一次谁会把它加回来"
// 的哨兵，不是行为测试（W2：用源码文本断行为是另一回事，这条界线写在 ADR 里）。
describe('内容区搬进壳之后的护栏（票 04）', () => {
  /** 一份样式源码里某个选择器的全部声明块（同一个选择器可能出现在好几段媒体查询里）。
   *  **注释先剥掉**：这几条断的是"声明还在不在"，注释里提一句 `overflow-y: auto`
   *  讲清楚为什么删掉，不该把自己判红。 */
  function bodiesOf(source, selector) {
    const stripped = source.replace(/\/\*[\s\S]*?\*\//g, '')
    const bodies = []
    let from = 0
    for (;;) {
      const at = stripped.indexOf(`${selector} {`, from)
      if (at === -1) return bodies
      const end = stripped.indexOf('}', at)
      bodies.push(stripped.slice(at, end + 1))
      from = end + 1
    }
  }

  /** 整份源码去掉注释（断言"文件里不再有 X"时用）。 */
  const withoutComments = (source) => source.replace(/\/\*[\s\S]*?\*\//g, '')

  it('`.hy-main` 只剩页面正文自己的下内边距：横向 / 上内边距已归壳', () => {
    const bodies = bodiesOf(HYGIENE_CSS, '.hygiene-admin .hy-main')
    expect(bodies.length, '共享样式表里没有 `.hy-main` 这条了？').toBe(2)
    for (const body of bodies) {
      // 允许 `padding-bottom`（收尾留白，页面正文自己的）；不许再有上 / 横向。
      expect(body, `.hy-main 还在出横向 / 上内边距：${body}`)
        .not.toMatch(/padding(-top|-left|-right)?\s*:/)
      expect(body).toMatch(/padding-bottom\s*:/)
    }
    // **值也要钉住**（票 08 补）：票 04 把 base 从 `var(--hy-page)` 漂成 `1.4rem` 时没有
    // 任何护栏拦得住 —— 当时只断「有没有 `padding-bottom`」、不断取值，于是 721–899px 那一段
    // 悄悄少了 6.4px 的页内留白（那一段既不被组壳的移动档覆盖、也够不到 ≥900px 的媒体查询）。
    // 两条规则的取值就是这两个，多一个少一个都算漂移。
    const bottoms = bodies.map(
      (body) => (body.match(/padding-bottom\s*:\s*([^;]+);/) || [])[1]?.trim()
    )
    expect(new Set(bottoms), `hy-main 的下内边距取值漂了：${bottoms}`)
      .toEqual(new Set(['var(--hy-page)', '2rem']))
  })

  it('页面区之外没有第二个滚动容器：`.hy-main` 不再声明 `overflow-y: auto`', () => {
    // 2026-10-12「卫生八页滑不动」的形状就是「高度锁 + 内部滚」只留了一半：
    // `.hy-main` 这条 `overflow-y: auto` 是内层滚动声明的那一半，今天只因为高度是
    // `auto` 才无害。整页滚的口径下它必须没有（ADR 0106 决策 1 与 8）。
    // 两份物理副本逐字节相等由 `utils/__tests__/hygieneAdminCss.test.js` 钉着，这里只读正本。
    for (const source of [HYGIENE_CSS]) {
      for (const body of bodiesOf(source, '.hygiene-admin .hy-main')) {
        expect(body, `.hy-main 又在自己滚：${body}`).not.toMatch(/overflow/)
      }
      // 员工端那一页的内容容器同理（`/workbench/staff/clean` 的 `.hy-work-main`）：
      // 高度也是 `auto`（`.hygiene-staff` 是 min-height 的 flex 列），滚不动；
      // 留着就是下一个同形状的半条声明。
      for (const body of bodiesOf(source, '.hygiene-work .hy-work-main')) {
        expect(body, `.hy-work-main 又在自己滚：${body}`).not.toMatch(/overflow/)
      }
    }
  })

  it('两个组壳里不再有内边距规则：只剩 rail 名单 / 脚注 / 实时点', () => {
    // 人事壳那条 `:deep(.roster-page)`（票 05 花名册从卫生搬过来时补的）与现场壳那条
    // 手机档 `:deep(.hy-main)` —— 前者把值交还给页面自己的样式（组里另外五页的根自带
    // 内边距，组级给非零值会把它们推走）。
    const hrShell = withoutComments(HR_SHELL)
    expect(hrShell).not.toMatch(/padding/)
    expect(hrShell).not.toMatch(/:deep\(/)
    // 现场壳那条手机档 `:deep(.hy-main)`（重复表达底栏让位，与壳的 `--wb-inset-bottom`
    // 叠加）已由票 08 删除：底栏让位只剩壳一处。这里把它钉成「不许回来」—— 它一旦回来，
    // 手机档底部就是 57px + `--hy-page` 再叠一份。（`.hy-rail-foot` 自己的
    // `padding: 12px 10px 0` 是脚注的内衬，与内容区无关，所以只禁 `:deep()` 与下内边距。）
    const hyShell = withoutComments(HY_SHELL)
    expect(hyShell, '现场壳又长出 :deep() 规则（内容区归壳，见 ADR 0106 决策 2/3）')
      .not.toMatch(/:deep\(/)
    expect(hyShell, '现场壳又在补下内边距（底栏让位只剩壳一处）')
      .not.toMatch(/padding-bottom/)
  })

  it('壳自己不滚：内容容器的规则里没有 overflow 声明', () => {
    const bodies = bodiesOf(SHELL, '.wb-main')
    expect(bodies.length).toBeGreaterThan(0)
    for (const body of bodies) {
      expect(body, `壳的内容容器在滚：${body}`).not.toMatch(/overflow/)
    }
  })
})

// ══════════════════════════════════════════════════════════════════════════════
// 票 07（安全区）：`env(safe-area-inset-*)` 只在令牌层写一遍 —— ADR 0106 背景里点名的第二处
// 「已经存在却被绕过」（令牌层有 `--safe-t/-b/-l/-r`，页面却各自直写 `env()`），也是
// Consequences 那条「护栏只加『不许出现 X』型」里的第一条。
//
// 判据：令牌层 `theme.workbench.css` 把那四根定义成 `env(safe-area-inset-*)`（它俩是
// 全前端 `env()` 的**唯一**出现处），页面与共享样式表消费令牌、不直写。今天页面侧仍有存量
// 直写（页内底栏 / 浮层 / 拍摄面板 / 页头，它们自己就贴着视口边、要绕开 home indicator）——
// 本票只加护栏、**不夹带实现改动**，所以存量按棘轮钉住、未列出的文件按 strict 0：
// 新写的页面直写 `env()` 会当场红。
//
// 范围：`src/`（除 `__tests__` 与 `src/styles/`）加共享样式表 `public/hygiene-admin.css`。
// `src/styles/` 两份是令牌层自己与管理后台那套（ADR 0106 决策 10：那套不动）；
// `public/recipe.css` 与 KDS 是 ADR 点名的非目标，不扫。
//
// 与上面那三条一样，这是**源码断言**（文本判定即结论），不是行为测试：安全区在真机上
// 解出来是多少 px 只能在浏览器里量（票 07 那一轮在 `baseline/checks-07/`）。
// ══════════════════════════════════════════════════════════════════════════════
describe('安全区只在令牌层写一遍（票 07 / ADR 0106 决策 4）', () => {
  const APP_ROOT = join(here, '../../../..')
  const TOKEN_LAYER = 'src/styles/theme.workbench.css'
  // 数**出现次数**而不是行数：一行里左右各取一次 max 的行内 padding 是两处
  // （`WorkbenchMobileHead` 那条就是）。
  const SAFE_ENV = /env\(\s*safe-area-inset-(top|right|bottom|left)/g
  const SKIP_DIR = /(__tests__|node_modules|dist)$/

  /** 存量直写（**只减不增**）：文件 → 允许的出现次数；未列出的文件必须为 0。
   *  清单里这些是**安全区的正当消费者**（页内底栏 / 浮层 / 拍摄面板 / 页头，都钉在视口边），
   *  把它们收敛成令牌层的 `var(--safe-*)` 是改版面 —— 另开票，不进这张护栏票。
   *  `views/hygiene/HygieneAdminLayout.vue` 那一处（手机档 `:deep(.hy-main)` 里的
   *  `env(safe-area-inset-bottom)`，与壳下 inset 叠加的重复让位）已由票 08 连同那条规则
   *  一起删掉，它随之收敛到 0、从这张表里退出：这个文件现在按 strict 0 盯着。 */
  const LEGACY = {
    'public/hygiene-admin.css': 16,
    'src/components/ImageUploadQueuePanel.vue': 1,
    'src/components/hygiene/HygieneImageLightbox.vue': 2,
    'src/components/workbench/WorkbenchMobileHead.vue': 2,
    'src/components/workbench/WorkbenchTabBar.vue': 1,
    'src/views/PrepPlanView.vue': 1,
    'src/views/hygiene/HygieneRosterView.vue': 3,
    'src/views/scheduling/SchedulingCalendarView.vue': 2,
  }

  /** 「页面侧」的全部源码：`src/`（跳过 `__tests__` / dist / `src/styles/`）+ 共享样式表正本。
   *  两份物理副本逐字节相等由 `utils/__tests__/hygieneAdminCss.test.js` 钉着，这里读正本
   *  （与上面 `HYGIENE_CSS` 同一口径）。 */
  function pageSideSources() {
    const stylesDir = join(APP_ROOT, 'src', 'styles')
    const out = [join(APP_ROOT, 'public', 'hygiene-admin.css')]
    const walk = (dir) => {
      for (const name of readdirSync(dir)) {
        const p = join(dir, name)
        if (statSync(p).isDirectory()) {
          if (!SKIP_DIR.test(name) && p !== stylesDir) walk(p)
        } else if (/\.(vue|js|css)$/.test(name)) out.push(p)
      }
    }
    walk(join(APP_ROOT, 'src'))
    return out.sort()
  }

  const envCount = (file) => (stripComments(readFileSync(file, 'utf8')).match(SAFE_ENV) || []).length

  it('令牌层仍是这四根的唯一来源（删了它，整站的安全区一起失效）', () => {
    const theme = read('../../../styles/theme.workbench.css')
    for (const [name, area] of [['--safe-t', 'top'], ['--safe-b', 'bottom'],
                                ['--safe-l', 'left'], ['--safe-r', 'right']]) {
      expect(theme, `${name} 该由 env(safe-area-inset-${area}) 定义`)
        .toMatch(new RegExp(`${name}\\s*:\\s*env\\(\\s*safe-area-inset-${area}`))
    }
  })

  it('页面与共享样式表里不再新增直写：存量逐文件钉住（只减不增）', () => {
    const added = []
    let watched = 0
    for (const file of pageSideSources()) {
      const n = envCount(file)
      if (!n) continue
      watched += 1
      const rel = relative(APP_ROOT, file)
      const max = LEGACY[rel] ?? 0
      if (n > max) {
        added.push(`${rel}：${n} > 存量 ${max}${LEGACY[rel] === undefined
          ? '（未登记，按 strict 必须为 0：安全区请消费令牌层的 var(--safe-t/-b/-l/-r)）' : ''}`)
      }
    }
    expect(added, `安全区只准在 ${TOKEN_LAYER} 里写一遍；页面消费 --safe-* 令牌`).toEqual([])
    // 防空转：这条今天真的在盯着存量；哪天清单收敛空了，这条与 LEGACY 一起删。
    expect(watched, '剥注释后真的还有存量直写在被这条盯着').toBeGreaterThan(0)
  })
})
