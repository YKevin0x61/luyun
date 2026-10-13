import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))

function read(rel) {
  return readFileSync(join(here, rel), 'utf8')
}

describe('LuyunDatePicker', () => {
  it('日历弹层用中文 locale，不吃默认的英文', () => {
    const picker = read('../LuyunDatePicker.vue')
    const pkg = JSON.parse(read('../../../../package.json'))

    // 2026-10-13 用户裁定「日期格式要中国本土化」：不传 locale 时 vue-datepicker 的
    // 弹层是英文（月份 `Oct`、星期 `Su Mo Tu…`）。输入框里那串 `yyyy-MM-dd` 由
    // `formats` 管，跟 locale 是两件事 —— 只改 formats 不解决弹层。
    expect(picker).toMatch(/import \{ zhCN \} from 'date-fns\/locale'/)
    expect(picker).toMatch(/:locale="zhCN"/)
    // 它靠 `date-fns` 的 locale 表，那份包要**显式**声明成直接依赖，
    // 不能只靠 vue-datepicker 装上之后提升出来的 node_modules。
    expect(pkg.dependencies['date-fns']).toBeTruthy()
  })

  it('inputId 走 inputAttrs 透传到组件内部那个 input 上', () => {
    const picker = read('../LuyunDatePicker.vue')
    // 直接写 `id="..."` 会落在根 div 上（Vue 的 attrs 落到根元素），label 依然关联不上；
    // vue-datepicker 的 `inputAttrs` 才是挂到内部 input 的那条路。
    expect(picker).toMatch(/inputId: \{ type: String/)
    expect(picker).toMatch(/:input-attrs="inputAttrs"/)
    expect(picker).toMatch(/props\.inputId \? \{ id: props\.inputId \}/)
  })

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
    expect(picker).toMatch(/props\.monthPicker \? 'yyyy-MM' : 'yyyy-MM-dd'/)
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
