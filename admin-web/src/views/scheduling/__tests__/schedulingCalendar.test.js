import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../SchedulingCalendarView.vue'), 'utf8')
const tokens = readFileSync(join(here, '../../../../public/hygiene-admin.css'), 'utf8')
const router = readFileSync(join(here, '../../../router/index.js'), 'utf8')
const navBar = readFileSync(join(here, '../../../components/NavBar.vue'), 'utf8')

describe('店长端排班月历（原型 B）', () => {
  it('borrows the shared deep-teal tokens without joining the hygiene module', () => {
    // 令牌住在 public/hygiene-admin.css 里，但那是**共享样式表**：排班不 import
    // 卫生的 Python、不挂卫生菜单，两边各走各的门。
    expect(view).toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
    expect(view).toMatch(/class="hygiene-admin sched-page"/)
  })

  it('only talks to the scheduling API', () => {
    expect(view).toMatch(/\/api\/scheduling\/calendar/)
    expect(view).toMatch(/\/api\/scheduling\/day/)
    expect(view).toMatch(/\/api\/scheduling\/roster/)
    expect(view).toMatch(/api\.put\(`\/api\/scheduling\/rules\/\$\{employee\.id\}`/)
    expect(view).toMatch(/api\.delete\(`\/api\/scheduling\/rules\/\$\{employee\.id\}`\)/)
    expect(view).not.toMatch(/\/api\/hygiene/)
  })

  it('keeps the chosen prototype B shape', () => {
    // 用户选的店长端首页 = `.scratch/scheduling/prototype/admin-variants.html`
    // 的 B · 月历（以天为中心）：一月网格 → 选中那天的人 → 底部待处理条。
    for (const cls of ['gB-grid', 'gB-d', 'gB-legend', 'gB-card', 'gB-names', 'gPend']) {
      expect(view).toContain(cls)
    }
    expect(view).toMatch(/repeat\(7,\s*1fr\)/)
    expect(view).toMatch(/calendar\.lead/) // 月初空格由后端算，前端不重算星期
    expect(view).toMatch(/calendar\.window_end/) // 窗口尽头之后的格子要说「还没铺到」
  })

  it('renders however many shifts the API returns', () => {
    // 班次可配置（票 11 会加「早班」之类）：页面按 N 个班次渲染，不写死白/夜两个。
    expect(view).toMatch(/v-for="shift in shifts"/)
    expect(view).toMatch(/v-for="group in dayDetail\.groups"/)
    expect(view).not.toMatch(/['"]白班['"]/)
    expect(view).not.toMatch(/['"]夜班['"]/)
  })

  it('pins a fixed zone per person and per shift (票 03)', () => {
    // 区名单只有一份（卫生建的那张表），排班经公共层读出来 → 页面按 N 个区渲染，
    // 一个区名字都不写死。
    expect(view).toMatch(/\/api\/scheduling\/zone-defaults\/\$\{employee\.id\}/)
    expect(view).toMatch(/data\.zones/)
    expect(view).toMatch(/v-for="zone in zones"/)
    expect(view).toMatch(/v-for="person in group\.people"/)
    expect(view).toMatch(/person\.zone/)
    expect(view).toMatch(/未配区/)
  })

  it('uses only tokens the shared stylesheet defines', () => {
    // 少一个 var() 就是一处静默失效的样式（无色/无圆角），而 scoped 样式块
    // 不会因为引用了不存在的自定义属性而报错。
    const used = new Set([...view.matchAll(/var\((--[a-z0-9-]+)\)/g)].map((m) => m[1]))
    expect(used.size).toBeGreaterThan(10)
    const missing = [...used].filter((name) => !tokens.includes(`${name}:`))
    expect(missing).toEqual([])
  })

  it('has its own door in the admin shell', () => {
    expect(router).toMatch(/path: '\/scheduling'/)
    expect(router).toMatch(/views\/scheduling\/SchedulingCalendarView\.vue/)
    expect(navBar).toMatch(/to="\/scheduling"/)
    expect(navBar).toMatch(/prefix: '\/scheduling'/)
  })
})
