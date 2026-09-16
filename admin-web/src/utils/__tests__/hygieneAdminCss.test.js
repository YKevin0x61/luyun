import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const CSS_FILES = {
  'admin-web/public/hygiene-admin.css': join(here, '../../../public/hygiene-admin.css'),
  'public/hygiene-admin.css': join(here, '../../../../public/hygiene-admin.css'),
}

describe.each(Object.entries(CSS_FILES))('%s hygiene admin chrome', (_label, path) => {
  const css = readFileSync(path, 'utf8')

  it('keeps tokens and chrome scoped to the hygiene admin shell', () => {
    expect(css).toContain('.hygiene-admin,')
    expect(css).toContain('.hygiene-staff {')
    expect(css).toContain('--hy-pool: #0f6f78')
    expect(css).toContain('.hygiene-admin .hy-tabbar')
    expect(css).toContain('.hygiene-work .hy-tabbar')
    expect(css).toContain('.hygiene-admin .hy-tab.router-link-active')
    expect(css).toContain('.hygiene-staff .hy-staff-card')
    expect(css).toContain('prefers-reduced-motion')
  })

  it('keeps picker geometry and v14 datepicker class names intact', () => {
    // The time wheel centers its items through the base stylesheet's column
    // padding, which is derived from --luyun-time-visible-rows; overriding it
    // puts the band on the wrong item.
    expect(css).not.toContain('.hygiene-admin .luyun-time-picker__column')
    expect(css).not.toMatch(/dp__/)
    expect(css).not.toMatch(/\.luyun-date-picker \.dp__input\b/)
    if (css.includes('.hygiene-admin .luyun-time-picker__popover')) {
      // 承载逾期点的卡片带 backdrop-filter，会形成层叠上下文；这张卡必须抬起来，
      // 否则弹窗会被后面的卡片盖住。
      expect(css).toMatch(/\.hygiene-admin \.clocks-card \{[\s\S]*?z-index: 30/)
    }
  })
})
