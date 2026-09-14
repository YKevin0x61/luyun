import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

describe('hygiene admin section shell', () => {
  it('top nav keeps a single 卫生 entry instead of six sibling tabs', () => {
    const nav = read('../../../components/NavBar.vue')
    expect(nav).toMatch(/to="\/hygiene-roster"/)
    expect(nav).toMatch(/HYGIENE_BRAND_TITLE/)
    expect(nav).toMatch(/isHygieneAdminPath\(route\.path\)/)
    expect(nav).not.toMatch(/>花名册</)
    expect(nav).not.toMatch(/>卫生区</)
    expect(nav).not.toMatch(/>日常验收</)
    expect(nav).not.toMatch(/>专项卫生</)
    expect(nav).not.toMatch(/>整改单</)
    expect(nav).not.toMatch(/>红黑榜</)
  })

  it('layout owns the six inner links and loads the section stylesheet', () => {
    const layout = read('../HygieneAdminLayout.vue')
    expect(layout).toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
    expect(layout).toMatch(/StandardPhotoCachePanel/)
    expect(layout).toMatch(/useStandardPhotoCacheStore/)
    expect(layout).toMatch(/HYGIENE_ADMIN_NAV/)
    expect(layout).toMatch(/aria-label="卫生管理"/)
    expect(layout).toMatch(/class="hy-tabbar"/)
    expect(layout).toMatch(/HYGIENE_BACK_TO_ADMIN_LABEL/)
    expect(layout).toMatch(/跳到内容/)
    expect(layout).not.toMatch(/hy-nav-link/)
  })

  it('router wraps every admin hygiene path in a standalone shell', () => {
    const router = read('../../../router/index.js')
    expect(router).toMatch(/HygieneAdminLayout/)
    expect(router).toMatch(/meta: \{ standalone: true \}/)
    expect(router).toMatch(/hygieneAdminPage\('\/hygiene-roster'/)
    expect(router).toMatch(/hygieneAdminPage\('\/hygiene-zones'/)
    expect(router).toMatch(/hygieneAdminPage\('\/hygiene-daily'/)
    expect(router).toMatch(/hygieneAdminPage\('\/hygiene-deep-clean'/)
    expect(router).toMatch(/hygieneAdminPage\('\/hygiene-fix'/)
    expect(router).toMatch(/hygieneAdminPage\('\/hygiene-boards'/)
  })
})
