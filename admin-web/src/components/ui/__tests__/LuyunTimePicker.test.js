import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

describe('LuyunTimePicker', () => {
  it('shows three rows and keeps the CSS geometry in sync with the component', () => {
    const theme = read('../../../styles/theme.css')
    const picker = read('../LuyunTimePicker.vue')

    expect(theme).toMatch(/--luyun-time-visible-rows: 3;/)
    expect(theme).toMatch(/--luyun-time-item-height: 36px;/)
    expect(theme).toMatch(/height: var\(--luyun-time-wheel-height\);/)
    expect(theme).toMatch(
      /padding: calc\(\(var\(--luyun-time-wheel-height\) - var\(--luyun-time-item-height\)\) \/ 2\) 0;/,
    )
    // The scroll math in the component still assumes the 36px row height.
    expect(picker).toMatch(/const ITEM_HEIGHT = 36/)
  })
})
