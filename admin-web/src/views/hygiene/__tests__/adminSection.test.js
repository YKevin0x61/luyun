import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { pageMeta } from '../../../router/pageRoutes.js'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

/** 把文件里 `@media (max-width: 720px)` 那几段规则拼起来。按大括号配对取块，不靠正则猜边界
 *  （同一手法见 `views/workbench/__tests__/shellMobileChrome.test.js` 的 `mobileBlocks`）。
 *  手机档的排布是**布局**：jsdom 不做布局、也不解析媒体查询，所以这一组按源码断。 */
function mobileRules(source) {
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
  return blocks.join('\n')
}

describe('hygiene admin section shell', () => {
  it('顶部导航只有一格工作台入口，不铺开成六个兄弟页签', () => {
    const nav = read('../../../components/NavBar.vue')
    // 2026-10-04：排班与卫生合并成子系统「工作台」，导航上只剩一格，指到工作台；
    // 卫生那一组从工作台窄栏的「现场」进（自己的 rail 有八页），不再直接挂在顶部导航上。
    expect(nav).toMatch(/to="\/workbench"/)
    expect(nav).toMatch(/WORKBENCH_TITLE/)
    // 票 04：两组同住 `/workbench/*`，高亮只看这一条前缀，不必再分两组判断。
    expect(nav).toMatch(/route\.path\.startsWith\('\/workbench'\)/)
    expect(nav).not.toMatch(/to="\/workbench\/roster"/)
    expect(nav).not.toMatch(/>花名册</)
    expect(nav).not.toMatch(/>卫生区</)
    expect(nav).not.toMatch(/>日常验收</)
    expect(nav).not.toMatch(/>专项卫生</)
    expect(nav).not.toMatch(/>整改单</)
    expect(nav).not.toMatch(/>红黑榜</)
  })

  it('layout owns the rail and loads the section stylesheet', () => {
    const layout = read('../HygieneAdminLayout.vue')
    expect(layout).toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
    expect(layout).toMatch(/StandardPhotoCachePanel/)
    expect(layout).toMatch(/useStandardPhotoCacheStore/)
    expect(layout).toMatch(/HYGIENE_ADMIN_NAV/)
    expect(layout).toMatch(/class="hy-tabbar"/)
    expect(layout).toMatch(/HYGIENE_BACK_TO_ADMIN_LABEL/)
    expect(layout).toMatch(/跳到内容/)
    expect(layout).not.toMatch(/hy-nav-link/)
  })

  it('C 方向：手机档的底部那一格让给工作台级底栏，组内八项挪到内容区顶上横滑', () => {
    const layout = read('../HygieneAdminLayout.vue')
    // 底栏挂上（三套壳共用同一颗）：它自己只在 ≤720 渲染（桌面档 `display: none`）。
    expect(layout).toMatch(/components\/workbench\/WorkbenchTabBar\.vue/)
    expect(layout).toMatch(/<WorkbenchTabBar class="hy-wb-tabbar" \/>/)
    // 顶栏那排组件还在（桌面档那条横条里仍是它在干活），手机档只是收起来。
    expect(layout).toMatch(/components\/workbench\/WorkbenchNav\.vue/)

    const rules = mobileRules(layout)
    // 顶栏那排：手机档 `display: none` —— 落点到底栏，同一排入口不在顶上和底下同时出现。
    expect(rules).toMatch(/\.hygiene-app \.hy-wb-nav\s*\{\s*display:\s*none/)
    // 组内八项：从底部的 6 格 grid 换成一条横滑带（`flex` + `nowrap` + `overflow-x`），
    // 次序排在内容**之前**（order 1 < 2）—— 底部那一格留给工作台级底栏，两条底栏不叠。
    expect(rules).toMatch(/\.hy-tabbar\s*\{[^}]*order:\s*1/)
    expect(rules).toMatch(/\.hy-tabbar\s*\{[^}]*flex-wrap:\s*nowrap/)
    expect(rules).toMatch(/\.hy-tabbar\s*\{[^}]*overflow-x:\s*auto/)
    expect(rules).toMatch(/\.hy-main\s*\{[^}]*order:\s*2/)
    // 底栏钉在视口底（组件里那条 `position: fixed` 会被共享样式表的
    // `.hygiene-admin > *:not(.modal-overlay) { position: relative }` 打回 relative ——
    // 同特异度、但那张表更晚进 head，所以壳里这条带前缀的规则是它生效的条件）。
    expect(rules).toMatch(/\.hy-wb-tabbar\s*\{[^}]*position:\s*fixed/)
    // 它不占流，内容区自己让出那条栏的高度，否则最后一屏压在栏下滚不到底。
    expect(rules).toMatch(/\.hy-main\s*\{[^}]*padding-bottom:\s*calc\(1\.4rem \+ 57px/)
  })

  it('router wraps every floor page in a standalone shell（现场组七页）', () => {
    const router = read('../../../router/index.js')
    expect(router).toMatch(/HygieneAdminLayout/)
    // 票 01：独立外壳标记不再写死在 router 里，而是每条路由从页面清单派生
    // （`meta: pageMeta(path)`）。值本身在这里对着清单断一次；「注册出来的路由确实
    // 等于清单」由 `router/__tests__/pageRoutes.test.js` 对着真实路由表钉。
    expect(router).toMatch(/meta: pageMeta\(path\)/)
    expect(pageMeta('/workbench/floor/daily')).toEqual({ standalone: true, public: false, audience: 'admin' })
    expect(router).toMatch(/hygieneAdminPage\('\/workbench\/floor\/zones'/)
    expect(router).toMatch(/hygieneAdminPage\('\/workbench\/floor\/daily'/)
    expect(router).toMatch(/hygieneAdminPage\('\/workbench\/floor\/deep-clean'/)
    expect(router).toMatch(/hygieneAdminPage\('\/workbench\/floor\/fix'/)
    expect(router).toMatch(/hygieneAdminPage\('\/workbench\/floor\/boards'/)
    // 花名册票 05 起是人事页：走人事壳那条工厂，不在这条 rail 的清单里。
    expect(router).toMatch(/workbenchHrPage\('\/workbench\/hr\/roster'/)
  })
})
