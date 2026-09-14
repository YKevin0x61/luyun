import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const zones = readFileSync(join(here, '../HygieneZonesView.vue'), 'utf8')
const home = readFileSync(join(here, '../HygieneHomeView.vue'), 'utf8')

describe('admin can delete a zone or daily item with confirm', () => {
  it('calls DELETE after a Chinese confirm and explains in-flight work goes away', () => {
    expect(zones).toMatch(/ConfirmDialog/)
    expect(zones).toMatch(/删除卫生责任区/)
    expect(zones).toMatch(/进行中的日常待办和未闭环整改单/)
    expect(zones).toMatch(/删除日常检查项/)
    expect(zones).toMatch(/该项进行中的待办/)
    expect(zones).toMatch(/api\.delete\(`\/api\/hygiene\/admin\/zones\/\$\{zone\.id\}`\)/)
    expect(zones).toMatch(/api\.delete\(`\/api\/hygiene\/admin\/items\/\$\{item\.id\}`\)/)
    expect(zones).toMatch(/aria-label="`删除卫生责任区 \$\{zone\.name\}`"/)
    expect(zones).toMatch(/aria-label="`删除日常检查项 \$\{item\.name\}`"/)
  })
})

describe('staff-phone has no delete for zones or items', () => {
  it('does not expose admin delete routes on the staff home', () => {
    expect(home).not.toMatch(/\/api\/hygiene\/admin\/zones/)
    expect(home).not.toMatch(/\/api\/hygiene\/admin\/items/)
    expect(home).not.toMatch(/deleteZone/)
    expect(home).not.toMatch(/deleteItem/)
  })
})
