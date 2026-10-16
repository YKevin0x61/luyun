// @vitest-environment jsdom
/**
 * 人事端「考勤表」页（考勤表导出票 01）：读源码式的契约 + 一次真渲染。
 *
 * 这一票的页面**只读、只算当月**：一条 GET、没有第二种请求，没有月份选择器，也没有
 * 导出按钮（导出是票 08）。这三条是验收里最容易在后续几票里被顺手破坏的：加一颗按钮、
 * 抄一个月份下拉，页面就悄悄变成了另一件事。所以在这里钉住。
 *
 * 另外钉「三处登记」：页面清单（`router/pageRoutes.json`）、vue-router（`index.js`）、
 * 服务端页面清单（`main.py` 的 `SPA_PAGE_ROUTES`）。漏掉任何一处就是「点得进去、
 * 一刷新 404」或者「导航里没有这一页」—— `tests/test_spa_page_routes.py` 从服务端那
 * 一侧双向卡同一件事。
 *
 * 最后挂一次**真渲染**：这一页的验收（上班 / 休 / 待补、今天之后留空、入职日期为空
 * 就留空）说的都是「页面上看到什么」。读源码能保证「没写第二份说法」，只有挂起来才知道
 * 它真的画得出来（写错一个变量名就是白屏，而白屏在源码里看不出来）。
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(here, '../AttendanceSheetView.vue'), 'utf8')
const copy = readFileSync(join(here, '../../../utils/attendanceSheet.js'), 'utf8')
const router = readFileSync(join(here, '../../../router/index.js'), 'utf8')
const pageRoutes = readFileSync(join(here, '../../../router/pageRoutes.json'), 'utf8')
const mainPy = readFileSync(join(here, '../../../../../main.py'), 'utf8')

const PATH = '/workbench/hr/attendance'

describe('考勤表页（票 01）', () => {
  it('只读：整页只有一条读请求', () => {
    const calls = [
      ...view.matchAll(/api\.(get|post|put|delete|patch)\(\s*[`']([^`']*)/g),
    ].map((match) => `${match[1]} ${match[2]}`)
    expect([...new Set(calls)]).toEqual(['get /api/attendance/sheet'])
  })

  it('不提供月份选择、也不导出（这一票只读）', () => {
    // 月份选择器（原生 month 与站内那个组件）一个都不许有：打开就是当月。
    expect(view).not.toMatch(/month-picker/)
    expect(view).not.toMatch(/LuyunDatePicker/)
    expect(view).not.toMatch(/type="month"/)
    // 导出是票 08 的事：这一页上没有它 —— 没有下载动作、没有工作簿、也没有第二条端点
    // （上面那条用例已经把请求集合钉死在一条 GET 上了）。
    expect(view).not.toMatch(/xlsx|download|Blob|\.blob\(/i)
  })

  it('三态与两列的说法从 util 来，页面不自己写第二份', () => {
    expect(view).toMatch(/dayStateText\(/)
    expect(view).toMatch(/newHireText\(/)
    expect(view).toMatch(/formatNetHours\(/)
  })

  it('三处登记都在：页面清单、vue-router、服务端页面清单', () => {
    const row = JSON.parse(pageRoutes).pages.find((page) => page.path === PATH)
    expect(row).toMatchObject({
      title: '考勤表',
      group: 'hr',
      audience: 'admin',
      standalone: true,
      public: false,
    })
    expect(router).toMatch(/workbenchHrPage\(\s*'\/workbench\/hr\/attendance'/)
    expect(mainPy).toMatch(/["']\/workbench\/hr\/attendance["']/)
  })

  it('逐日那一列用服务端下发的月框（今天之后没有状态）', () => {
    // 卡片上的日子与表头的日子来自同一份 `days`（服务端按月框下发），页面不自己
    // 从 1 数到 30 —— 那样 2 月与 31 天的月份会各错一次。
    expect(view).toMatch(/sheet\.days|days\b/)
    expect(copy).toMatch(/export function dayStateText/)
  })
})

// ── 真渲染：验收 3/4/6 说的是「页面上看到什么」 ─────────────────────────────

/** 一份当月考勤表：四天，三种状态 + 一天还没到（`state: null`）。 */
const SHEET = {
  month: '2026-09',
  today: '2026-09-03',
  days: [
    { day: 1, business_date: '2026-09-01', is_today: false, is_future: false },
    { day: 2, business_date: '2026-09-02', is_today: false, is_future: false },
    { day: 3, business_date: '2026-09-03', is_today: true, is_future: false },
    { day: 4, business_date: '2026-09-04', is_today: false, is_future: true },
  ],
  employees: [
    {
      id: 1,
      name: '张三',
      job_title: '炒锅',
      base_salary: 6800,
      seniority_bonus: 300,
      new_hire: true,
      net_hours: -1.5,
      days: [
        { business_date: '2026-09-01', state: 'work' },
        { business_date: '2026-09-02', state: 'rest' },
        { business_date: '2026-09-03', state: 'todo' },
        { business_date: '2026-09-04', state: null },
      ],
    },
    {
      // 入职日期空着的人：这一项算不出来，页面上留空（不是「否」）。
      id: 2,
      name: '李四',
      job_title: '',
      base_salary: null,
      seniority_bonus: null,
      new_hire: null,
      net_hours: 0,
      days: [
        { business_date: '2026-09-01', state: 'todo' },
        { business_date: '2026-09-02', state: 'todo' },
        { business_date: '2026-09-03', state: 'todo' },
        { business_date: '2026-09-04', state: null },
      ],
    },
  ],
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

async function mountSheet(table = { '/api/attendance/sheet': SHEET }) {
  vi.resetModules()
  const fetchMock = vi.fn(async (url) => {
    const path = String(url).split('?')[0]
    if (Object.prototype.hasOwnProperty.call(table, path)) return jsonResponse(table[path])
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  const [{ default: AttendanceSheetView }] = await Promise.all([
    import('../AttendanceSheetView.vue'),
  ])
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }],
  })
  const wrapper = mount(AttendanceSheetView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, fetchMock }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('考勤表页渲染（票 01）', () => {
  it('一天一格，上班 / 休 / 待补，今天之后留空', async () => {
    const { wrapper, fetchMock } = await mountSheet()
    // 只有这一条请求（页面是只读的）。
    expect(fetchMock.mock.calls.map((call) => String(call[0]))).toEqual([
      '/api/attendance/sheet',
    ])
    const card = wrapper.findAll('.aCard')[0]
    const cells = card.findAll('.aDay')
    expect(cells).toHaveLength(4)
    expect(cells.map((cell) => cell.find('.aDayText').text())).toEqual([
      '上班',
      '休',
      '待补',
      '', // 今天之后留空 —— **不是**「待补」
    ])
    expect(cells[2].classes()).toContain('is-todo')
    expect(cells[2].classes()).toContain('is-today')
    expect(cells[3].classes()).toContain('is-future')
    expect(card.find('.aTodo').text()).toBe('待补 1 天')
  })

  it('卡片上的四项跟着花名册，入职日期为空就留空', async () => {
    const { wrapper } = await mountSheet()
    const stats = wrapper.findAll('.aCard')[0].findAll('.aStat')
    expect(stats.map((stat) => stat.find('i').text())).toEqual([
      '职位', '底薪', '年限', '是否新入职', '当月净时长',
    ])
    expect(stats.map((stat) => stat.find('b').text())).toEqual([
      '炒锅', '6800 元', '300 元', '是', '−1.5 小时',
    ])
    const theirs = wrapper.findAll('.aCard')[1].findAll('.aStat')
    expect(theirs.map((stat) => stat.find('b').text())).toEqual([
      '—', '—', '—', '', '0 小时', // 「是否新入职」留空
    ])
    // 页签名字也跟清单走（说明 onMounted 真的跑到了）。
    expect(document.title).toBe('考勤表 · 工作台')
  })
})
