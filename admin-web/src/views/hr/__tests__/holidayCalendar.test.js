// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../HolidayCalendarView.vue'), 'utf8')
const grid = readFileSync(join(here, '../../../utils/holidayCalendar.js'), 'utf8')
const pageRoutes = readFileSync(join(here, '../../../router/pageRoutes.json'), 'utf8')
const router = readFileSync(join(here, '../../../router/index.js'), 'utf8')
const mainPy = readFileSync(join(here, '../../../../../main.py'), 'utf8')

/** 这一页只读自己那三条接口（`src/api/client.js` 的 `api`），所以取数只要一个 fetch 桩。 */
function jsonResponse(data) {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

describe('节假日日历（考勤表导出 票 02）', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('页面真的挂得起来（不白屏）：一年 12 张网格，已标记的那天写着节名', async () => {
    // 打开就是今年，桩跟着今年给一天 —— 写死 2026 会在换年之后变成「一格都没标」。
    const year = new Date().getFullYear()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ year, days: [{ day: `${year}-10-01`, name: '国庆' }] })),
    )

    const View = (await import('../HolidayCalendarView.vue')).default
    const wrapper = mount(View)
    await flushPromises()

    expect(wrapper.findAll('.hMonth')).toHaveLength(12)
    const marked = wrapper.findAll('.hCell.is-marked')
    expect(marked).toHaveLength(1)
    expect(marked[0].text()).toContain('国庆')
    expect(marked[0].text()).toContain('1')
    wrapper.unmount()
  })
  it('三处页面清单都登记了这一页（人事组、管理端专属、直连不 404）', () => {
    const row = JSON.parse(pageRoutes).pages.find(
      (item) => item.path === '/workbench/hr/holidays',
    )
    expect(row).toBeTruthy()
    expect(row.title).toBe('节假日日历')
    expect(row.group).toBe('hr')
    expect(row.audience).toBe('admin')
    expect(row.public).toBe(false)

    expect(router).toMatch(/workbenchHrPage\('\/workbench\/hr\/holidays'/)
    expect(mainPy).toContain('"/workbench/hr/holidays"')
  })

  it('只打自己那三条路由，且都在考勤日历那一个前缀下', () => {
    expect(view).toMatch(/api\.get\(PATH, \{ year: year\.value \}\)/)
    expect(view).toMatch(/api\.put\(`\$\{PATH\}\/\$\{day\}`/)
    expect(view).toMatch(/api\.delete\(`\$\{PATH\}\/\$\{day\}`\)/)
    expect(view).toMatch(/const PATH = '\/api\/attendance\/holidays'/)

    const calls = [...view.matchAll(/api\.(?:get|post|put|delete)\(\s*([^)]*)/g)].map(
      (match) => match[1],
    )
    expect(calls).toHaveLength(3)
    for (const call of calls) expect(call).toContain('PATH')
  })

  it('标记与取消走服务端的同一份判据，成功后按年重读（不用刷新页面）', () => {
    // 名称的规则（1~20 字）只有 `utils/holidayCalendar.js` 一处实现，页面不另抄。
    expect(view).toMatch(/nameError\(/)
    expect(grid).toMatch(/MAX_HOLIDAY_NAME = 20/)
    // 每次写完都重读这一年：页面上看到的立刻就是新的。
    expect(view.match(/await load\(\)/g).length).toBeGreaterThanOrEqual(3)
    // 取消标记是页面上的一颗按钮（验收 4）。
    expect(view).toContain('取消标记')
  })

  it('一年一个网格，已标记的那天把节的名字写在格子里', () => {
    expect(view).toMatch(/yearGrid\(year\.value\)/)
    expect(view).toMatch(/holidayMap\(body\.days\)/)
    expect(view).toMatch(/\{\{ marked\[day\] \}\}/)
    // 一天一格、格子可点（点一天就标记 / 取消那一天的判据在 `pick`）。
    expect(view).toMatch(/@click="pick\(day\)"/)
    expect(view).toMatch(/function pick\(day\)/)
    expect(view).toMatch(/v-if="marked\[day\]"/)
  })

  it('页面里没有写死的年度节假日表（日期与节名都由接口给）', () => {
    // 一天都不许写死：`2026-10-01` 这种字面量出现在页面上就是第二份真相。
    expect(view).not.toMatch(/'20\d\d-\d\d-\d\d'/)
    // 也没有「某年有哪些节」的常量：节名只从接口来（格子里那个 `marked[day]`）。
    expect(view).not.toMatch(/'(国庆|春节|元旦|中秋|清明|端午)'/)
    // 「国庆」在这份源码里只作为输入框的示例文案出现，不是数据。
    expect(view).toContain('placeholder="比如：国庆"')
  })
})
