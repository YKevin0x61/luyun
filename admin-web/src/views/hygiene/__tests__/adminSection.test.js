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
    const shell = read('../../workbench/WorkbenchShell.vue')
    // ③-2：共享样式表与手机档页头都收进统一壳；卫生壳交的是本组那份手写名单。
    expect(shell).toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
    expect(layout).toMatch(/:items="HYGIENE_ADMIN_NAV"/)
    expect(layout).not.toMatch(/StandardPhotoCachePanel/)
    expect(layout).toMatch(/useStandardPhotoCacheStore/)
    expect(layout).toMatch(/HYGIENE_ADMIN_NAV/)
        // ③-2：组内八页的 rail 由统一壳渲染（`WorkbenchRail`），本壳交那份带图标的名单。
    expect(shell).toMatch(/<WorkbenchRail/)
    expect(layout).toMatch(/HYGIENE_BACK_TO_ADMIN_LABEL/)
    // ③-2：skip link 也在统一壳里（本壳的注释里提到它不算）。
    expect(shell).toMatch(/跳到内容/)
    expect(layout).not.toMatch(/hy-nav-link/)
  })

  it('方案 C：手机档收掉顶上那两条横带，组内八项走页头下拉（2026-10-08）', () => {
    const layout = read('../HygieneAdminLayout.vue')
    const shell = read('../../workbench/WorkbenchShell.vue')
    // 底栏挂上（三套壳共用同一颗）：它自己只在 ≤720 渲染（桌面档 `display: none`）。
    expect(shell).toMatch(/components\/workbench\/WorkbenchTabBar\.vue/)
    expect(shell).toMatch(/<WorkbenchTabBar/)
    // 顶栏那排组件还在（桌面档那条横条里仍是它在干活），手机档只是收起来。
        // ③-2：rail 与工作台级导航都在统一壳里；本壳交的是名单与脚注。
    expect(shell).toMatch(/components\/workbench\/WorkbenchRail\.vue/)
    expect(layout).toMatch(/:items="HYGIENE_ADMIN_NAV"/)
    // 手机档的换页出口：组内八页从横滑带换成这个下拉（组名 / 当前页 / 身份 / 更多）。
    expect(shell).toMatch(/components\/workbench\/WorkbenchMobileHead\.vue/)
    expect(shell).toMatch(/<WorkbenchMobileHead[^>]*:items="props\.items \|\| undefined"/)

    const rules = mobileRules(shell)
    // 顶栏：手机档整条 `display: none`（方案 C）—— 换页 / 身份 / 退出 / 回后台都进了页头那个
    // 一行，桌面档（>720px）还是它在干活。同一排入口不在顶上和底下同时出现。
    expect(rules).toMatch(/\.wb-top\s*\{\s*display:\s*none/)
    // 顶上那两条横带整个撤掉：组内八项（`.hy-tabbar`，桌面档那条 238px 的左 rail，
    // 2026-10-05~10-08 之间它还兼过"内容区顶上一条横滑带"）与顶栏横条（身份 / 退出 /
    // 后台，`.hy-header`）在手机档都不再渲染 —— 八项进页头下拉、三件也进页头。
    // 组内八项的 rail（桌面才是它）在手机档整个收起来 —— 判据在 rail 组件里一处。
    const rail = read('../../../components/workbench/WorkbenchRail.vue')
    expect(mobileRules(rail)).toMatch(/\.wb-rail\s*\{\s*display:\s*none/)
    // 底栏钉在视口底：那一份契约现在只有组件里一处（`position: fixed` + `var(--z-shell)`），
    // 三个壳不再各钉一遍 —— 那条同特异度的通配规则已经删掉（`public/hygiene-admin.css:50`）。
    const tabbar = read('../../../components/workbench/WorkbenchTabBar.vue')
    expect(mobileRules(tabbar)).toMatch(/\.wb-tabbar\s*\{[^}]*position:\s*fixed/)
    // 它不占流，内容区自己让出那条栏的高度（统一壳那一处），否则最后一屏压在栏下滚不到底。
    expect(rules).toMatch(/\.wb-main\s*\{[^}]*padding-bottom:\s*calc\(57px/)
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
