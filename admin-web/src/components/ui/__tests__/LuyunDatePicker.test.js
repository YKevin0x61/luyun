import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

describe('LuyunDatePicker', () => {
  it('passes Chinese input and clear labels to the datepicker', () => {
    const picker = read('../LuyunDatePicker.vue')
    const deepClean = read('../../../views/hygiene/HygieneDeepCleanView.vue')

    expect(picker).toMatch(/ariaLabel/)
    expect(picker).toMatch(/clearInput: `清除/)
    expect(picker).toMatch(/:aria-labels="ariaLabels"/)
    expect(deepClean).toMatch(/aria-label="专项日历开始日期"/)
    expect(deepClean).toMatch(/aria-label="专项日历结束日期"/)
  })

  it('uses the vue-datepicker v14 formats/time-config API', () => {
    const picker = read('../LuyunDatePicker.vue')
    const theme = read('../../../styles/theme.css')

    expect(picker).toMatch(/:formats="formats"/)
    expect(picker).toMatch(/input: 'yyyy-MM-dd'/)
    expect(picker).toMatch(/:time-config="timeConfig"/)
    expect(picker).toMatch(/enableTimePicker: false/)
    // v14 stopped teleporting the menu by default; inside a card with
    // backdrop-filter/overflow:hidden the calendar gets clipped and covered.
    expect(picker).toMatch(/teleport="body"/)
    expect(picker).not.toMatch(/format="yyyy-MM-dd"/)
    expect(picker).not.toMatch(/:enable-time-picker=/)

    // v14 renamed the theme and input classes; the old v8 selectors silently did nothing.
    expect(theme).toMatch(/\.dp--main\.dp--theme-dark/)
    expect(theme).toMatch(/\.dp--menu\.dp--theme-dark/)
    expect(theme).toMatch(/\.luyun-date-picker \.dp--input\b/)
    expect(theme).not.toMatch(/dp__/)
  })
})
