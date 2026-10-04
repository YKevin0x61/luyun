import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { pageMeta } from '../../../router/pageRoutes.js'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
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
