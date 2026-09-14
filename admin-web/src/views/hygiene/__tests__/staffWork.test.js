import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

describe('hygiene staff work app', () => {
  it('uses the porcelain work shell with a five-tab bar and no dark staff-phone card', () => {
    const home = read('../HygieneHomeView.vue')
    expect(home).toMatch(/useScopedStylesheet\('\/hygiene-admin\.css'\)/)
    expect(home).toMatch(/class="hygiene-staff hygiene-work"/)
    expect(home).toMatch(/HYGIENE_STAFF_TABS/)
    expect(home).toMatch(/class="hy-tabbar"/)
    expect(home).toMatch(/aria-label="卫生入口"/)
    expect(home).toMatch(/今天上哪一班/)
    expect(home).not.toMatch(/staff-phone/)
    expect(home).not.toMatch(/<style scoped>/)
  })

  it('shows remaining work, due clocks, and auto-advances after a shot', () => {
    const home = read('../HygieneHomeView.vue')
    expect(home).toMatch(/from '\.\.\/\.\.\/utils\/hygieneWorkFlow'/)
    expect(home).toMatch(/buildWorkQueue/)
    expect(home).toMatch(/hy-work-row/)
    expect(home).toMatch(/下一步|下一件|今天还差什么/)
    expect(home).toMatch(/nextShootRow/)
    expect(home).toMatch(/本班/)
    expect(home).toMatch(/staff-preview-close/)
    expect(home).toMatch(/aria-labelledby="hygiene-sheet-title"/)
    expect(home).toMatch(/已通过/)
    expect(home).toMatch(/daily_clocks/)
    expect(home).toMatch(/employee\.name \|\| employee\.phone/)
    expect(home).not.toMatch(/考核分/)
  })

  it('keeps the before image visible while confirming a deep-clean pair', () => {
    const home = read('../HygieneHomeView.vue')
    expect(home).toMatch(/beforePreviewUrl/)
    expect(home).toMatch(/localBeforeWatermark/)
    expect(home).toMatch(/sheet\.mode === 'after-preview'/)
    expect(home).toMatch(/left-label="清理前"/)
    expect(home).toMatch(/right-label="清理后"/)
  })
})
