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
    expect(home).toMatch(/StandardPhotoCachePanel/)
    expect(home).toMatch(/useStandardPhotoCacheStore/)
    expect(home).toMatch(/class="hygiene-staff hygiene-work"/)
    expect(home).toMatch(/HYGIENE_STAFF_TABS/)
    expect(home).toMatch(/class="hy-tabbar"/)
    expect(home).toMatch(/aria-label="卫生入口"/)
    expect(home).toMatch(/今天负责哪个区域、上哪一班/)
    expect(home).toMatch(/staff\/assignment/)
    expect(home).toMatch(/重新选择区域和班次/)
    expect(home).toMatch(/aria-label="重新选择区域和班次"/)
    expect(home).toMatch(/@click="openAssignmentPicker"/)
    expect(home).toMatch(/loadZones\(\{ force: true \}\)/)
    expect(home).toMatch(/loadBoardsAndTeaching\(\{ force: true \}\)/)
    expect(home).toMatch(/修改个人信息/)
    expect(home).toMatch(/staffRequest\('\/api\/hygiene\/staff\/me'/)
    expect(home).toMatch(/method: 'PATCH'/)
    expect(home).toMatch(/修改密码/)
    expect(home).toMatch(/\/api\/hygiene\/staff\/password/)
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

  it('names the zone when the daily inbox has nothing at all', () => {
    const home = read('../HygieneHomeView.vue')
    const empty = home.slice(
      home.indexOf('class="hy-staff-done"'),
      home.indexOf('hy-queue-group'),
    )
    // 日常清单按所选责任区过滤（ADR-0075）。空清单原来只说「还没有带标准图的日常
    // 检查项」，管理员在别的区建完标准图过来核对，读到的就是「图丢了」。这里必须
    // 点出是哪个区没有，并留一个换区出口。
    expect(empty).toMatch(
      /当前区域「\{\{ employee\.zone_name \|\| '未选区域' \}\}」没有带标准图的日常检查项/,
    )
    expect(empty).toMatch(/这一屏只列你选的这个区/)
    expect(empty).toMatch(/@click="openAssignmentPicker">换个区域看看/)
    // 旧的裸文案（不带区名）不许再作为正文出现；注释里提到它不算。
    expect(empty).not.toMatch(/>\s*还没有带标准图的日常检查项。/)
  })
})
