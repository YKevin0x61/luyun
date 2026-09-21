import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const CANONICAL = join(here, '../../../public/hygiene-admin.css')
const FALLBACK = join(here, '../../../../public/hygiene-admin.css')
const BUILT = join(here, '../../../dist/hygiene-admin.css')
const CSS_FILES = {
  'admin-web/public/hygiene-admin.css': CANONICAL,
  'public/hygiene-admin.css': FALLBACK,
}

describe('卫生样式只有一份来源（防漂移）', () => {
  it('keeps the repo-root fallback byte-identical to the canonical copy', () => {
    // 根 public/ 那份是 main.py 在 admin-web/dist 缺失时的回落目标。两份曾经分叉成
    // 「深色验收台」与「浅色 porcelain」两套相反主题，而契约测试只断言 8 个字面量，
    // 于是漂移一路绿着过——一旦真回落就是浅底浅字、拍照页几乎不可读。
    expect(readFileSync(FALLBACK, 'utf8')).toBe(readFileSync(CANONICAL, 'utf8'))
  })

  it('keeps an existing build in sync with the source', () => {
    // 第三份：构建产物。main.py 优先服务 admin-web/dist，改了源文件却没重新构建时，
    // 线上拿到的还是旧主题——只比两份源文件的测试抓不到这种情况。
    // 干净 checkout（CI 的 Python job、未构建的开发机）没有 dist，跳过。
    if (!existsSync(BUILT)) return
    expect(readFileSync(BUILT, 'utf8')).toBe(readFileSync(CANONICAL, 'utf8'))
  })
})

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
