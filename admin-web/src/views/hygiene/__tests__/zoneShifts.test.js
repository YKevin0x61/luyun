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

  it('offers only the zones that run the staff-picked shift', () => {
    expect(home).toMatch(/assignableZones/)
    expect(home).toMatch(/\(zone\.shifts \|\| HYGIENE_SHIFTS\)\.includes\(shift\)/)
    expect(home).toMatch(/v-for="zone in assignableZones"/)
    expect(home).toMatch(/这个班次暂时没有责任区/)
    expect(home).toMatch(/assignmentMismatch/)
    expect(home).toMatch(/current\.zone_shifts/)
  })

  it('filters the admin roster assignment zones by the chosen shift', () => {
    expect(roster).toMatch(/function zonesForShift/)
    expect(roster).toMatch(/zonesForShift\(drafts\[row\.id\]\.shift\)/)
  })
})
