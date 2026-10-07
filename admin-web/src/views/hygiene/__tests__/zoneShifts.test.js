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
    expect(zones).toMatch(/卫生工作区至少要有一个班次/)
    expect(zones).toMatch(
      /api\.patch\(`\/api\/hygiene\/admin\/zones\/\$\{selected\.value\.id\}`/,
    )
  })

  it('员工端不再自己挑班次和工作区：今天在哪由排班说了算（票 10）', () => {
    // 这一屏原来是「按你选的班次列出能选的工作区」。票 10 起，班次与工作区都来自排班结果
    // （`employee.shift` / `employee.zone_id`），选择器整个撤了 —— 这条守着「它没有偷偷
    // 回来」：既没有那份按班次筛出来的候选区，也没有那条写回自选的请求。
    expect(home).not.toMatch(/const assignableZones = computed/)
    expect(home).not.toMatch(/v-for="zone in assignableZones"/)
    expect(home).not.toMatch(/api\/hygiene\/staff\/assignment/)
    expect(home).not.toMatch(/HYGIENE_SHIFTS/)
    // 判据还在：排班给的班次不在这个区开的档里 → 今天交不了日常（页面说清该找谁）。
    expect(home).toMatch(/assignmentMismatch/)
    expect(home).toMatch(/今天交不了日常检查/)
    expect(home).toMatch(/router\.push\('\/workbench\/me\/today'\)/)
  })

  it('花名册不再按班次筛工作区（2026-10 改版：改派整段退出这一页）', () => {
    // 原来台账上选中的「班次」是排班的班次 id，候选区按工作区开关筛出来（`zonesForShift`）。
    // 改版后花名册只管档案与批准，这一套派生整组下线 —— 这条守着"它没有偷偷回来"。
    expect(roster).not.toMatch(/zonesForShift|zoneChoicesForShift|DUTY_SLOT_SHIFTS/)
    expect(roster).not.toMatch(/\/api\/scheduling\//)
    expect(roster).not.toMatch(/zone_id/)
  })
})
