import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const zones = readFileSync(join(here, '../HygieneZonesView.vue'), 'utf8')
const home = readFileSync(join(here, '../HygieneHomeView.vue'), 'utf8')
const roster = readFileSync(join(here, '../HygieneRosterView.vue'), 'utf8')

describe('hygiene zone shifts', () => {
  it('lets the super admin create and edit a zone day/night shift setting', () => {
    expect(zones).toMatch(/newZoneShifts/)
    expect(zones).toMatch(/zoneShifts/)
    expect(zones).toMatch(/保存班次/)
    expect(zones).toMatch(/卫生责任区至少要有一个班次/)
    expect(zones).toMatch(
      /api\.patch\(`\/api\/hygiene\/admin\/zones\/\$\{selected\.value\.id\}`/,
    )
  })

  it('员工端不再自己挑班次和责任区：今天在哪由排班说了算（票 10）', () => {
    // 这一屏原来是「按你选的班次列出能选的责任区」。票 10 起，班次与责任区都来自排班结果
    // （`employee.shift` / `employee.zone_id`），选择器整个撤了 —— 这条守着「它没有偷偷
    // 回来」：既没有那份按班次筛出来的候选区，也没有那条写回自选的请求。
    expect(home).not.toMatch(/const assignableZones = computed/)
    expect(home).not.toMatch(/v-for="zone in assignableZones"/)
    expect(home).not.toMatch(/api\/hygiene\/staff\/assignment/)
    expect(home).not.toMatch(/HYGIENE_SHIFTS/)
    // 判据还在：排班给的班次不在这个区开的档里 → 今天交不了日常（页面说清该找谁）。
    expect(home).toMatch(/assignmentMismatch/)
    expect(home).toMatch(/今天交不了日常检查/)
    expect(home).toMatch(/router\.push\('\/staff\/today'\)/)
  })

  it('filters the admin roster assignment zones by the chosen shift', () => {
    // 候选区还是那份按责任区开关筛出来的名单（`zonesForShift`），但台账上选中的「班次」
    // 现在是**排班的班次 id**（票 10：改派写的是排班的单日覆盖）。排班那条班次只标了
    // `duty_slot`（day/night），先翻成卫生认的「白班/夜班」再喂给它；没标档位的班次不筛
    // （筛只会把区滤空），所以那一步落在 `zoneChoicesForShift` 里。
    expect(roster).toMatch(/function zonesForShift/)
    expect(roster).toMatch(/const DUTY_SLOT_SHIFTS = \{ day: '白班', night: '夜班' \}/)
    expect(roster).toMatch(/return slot \? zonesForShift\(slot\) : zones\.value/)
    expect(roster).toMatch(/zoneChoicesForShift\(drafts\[row\.id\]\.shift\)/)
  })
})
