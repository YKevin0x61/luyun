// @vitest-environment jsdom
/**
 * 人事端「考勤表」页（考勤表导出票 01）：读源码式的契约 + 一次真渲染。
 *
 * 这一票的页面**只读、只算当月**：一条 GET、没有第二种请求，没有月份选择器，也没有
 * 导出按钮（导出是票 08）。这三条是验收里最容易在后续几票里被顺手破坏的：加一颗按钮、
 * 抄一个月份下拉，页面就悄悄变成了另一件事。所以在这里钉住。
 *
 * 票 06 往集合里加了逐日改格那条 PUT，票 04 加了封账那条 POST —— 集合仍然是硬钉的：
 * 多一条没人测的路径（导出、手填列）会让它红。
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

describe('考勤表页（票 01 / 票 06 / 票 04 / 票 05）', () => {
  it('请求集合就这四条：读当月 + 改某一天 + 封账上月 + 存手填列', () => {
    // 票 01 时这里钉的是「整页只有一条 GET」；票 06 加上了逐日改格那一条 PUT、
    // 票 04 加上了封账那一 POST、票 05 又加上了手填列那一条 PUT。
    // 集合本身仍然是硬钉的：多一条没人测的路径（导出）会让它红。
    const calls = [
      ...view.matchAll(/api\.(get|post|put|delete|patch)\(\s*[`']([^`']*)/g),
    ].map((match) => `${match[1]} ${match[2]}`)
    // 顺序不钉（页面里谁先写不影响契约），集合钉死。
    expect([...new Set(calls)].sort()).toEqual([
      'get /api/attendance/sheet',
      'post /api/attendance/seal',
      'put /api/attendance/sheet/${person.id}/${cell.business_date}',
      'put /api/attendance/sheet/${person.id}/manual',
    ].sort())
  })

  it('不提供月份选择、也不导出', () => {
    // 月份选择器（原生 month 与站内那个组件）一个都不许有：打开就是当月。
    expect(view).not.toMatch(/month-picker/)
    expect(view).not.toMatch(/LuyunDatePicker/)
    expect(view).not.toMatch(/type="month"/)
    // 导出是票 08 的事：这一页上没有它 —— 没有下载动作、也没有工作簿
    // （上面那条用例已经把请求集合钉死在两条上了）。
    expect(view).not.toMatch(/xlsx|download|Blob|\.blob\(/i)
  })

  it('三态与两列的说法从 util 来，页面不自己写第二份', () => {
    expect(view).toMatch(/dayStateText\(/)
    expect(view).toMatch(/newHireText\(/)
    expect(view).toMatch(/formatNetHours\(/)
  })

  it('只给已经到的那几天装控件（今天之后是死的格子）', () => {
    // 验收 45：今天之后的日子**不能编辑** —— 不是「点了再弹一句拒绝」，是压根没有
    // 能点的东西。挂控件的那两处都由 `editable(cell)` 守着。
    expect(view).toMatch(/v-if="editingDate === cell\.business_date"/)
    expect(view).toMatch(/v-else-if="editable\(cell\)"/)
    expect(view).toMatch(/function editable\(cell\)/)
    expect(view).toMatch(/state\)\s*&&\s*!cell\.is_future|Boolean\(cell\.state\)/)
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

// ── 真渲染：验收 3/4/6 与票 06 的逐日改格说的都是「页面上看到什么」 ──────────

/** 一份当月考勤表：四天，三种状态 + 一天还没到（`state: null`）。
 *  `shifts` 是票 06 的下拉里能挑的班次（**启用中的**）。 */
const SHIFTS = [
  { id: 1, name: '白班' },
  { id: 2, name: '夜班' },
]

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
      shifts: SHIFTS,
      // 票 05：台账里填过的四个手填列 + 「没填过时」的默认（当月众数）。
      shift_name: '早班',
      default_shift_name: '早班',
      lodging: '是',
      borrowed_store: '调东莞',
      resigned: null,
      days: [
        { business_date: '2026-09-01', state: 'work', shift_id: 1 },
        { business_date: '2026-09-02', state: 'rest', shift_id: null },
        { business_date: '2026-09-03', state: 'todo', shift_id: null },
        { business_date: '2026-09-04', state: null, shift_id: null },
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
      shifts: SHIFTS,
      // 这一位四项一项都没填过：`shift_name` 给的是当月众数，`default_shift_name` 也是它。
      shift_name: '夜班',
      default_shift_name: '夜班',
      lodging: null,
      borrowed_store: null,
      resigned: null,
      days: [
        { business_date: '2026-09-01', state: 'todo', shift_id: null },
        { business_date: '2026-09-02', state: 'todo', shift_id: null },
        { business_date: '2026-09-03', state: 'todo', shift_id: null },
        { business_date: '2026-09-04', state: null, shift_id: null },
      ],
    },
  ],
}

// 封账（票 04）那几个固定值：mock 服务端封完账之后回给页面的那一刻与那个数。
const SEALED_MONTH = '2026-08'
const SEALED_AT = '2026-09-24T10:00:00+08:00'
const SEALED_BALANCE = -21

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/** 一份「服务端」：GET 出表、PUT 改某一天、POST 封账上月（并记下收到的请求）。
 *
 *  `refuse` 决定那次 PUT 回什么（票 06 的拒绝理由走的就是这条）；`sealRefuse` 是封账
 *  那条 POST 的拒绝理由（票 04：当月封不了 / 已经封过）。改成功之后，之后那次 GET 要回
 *  **改过的**表 —— 页面在这一页上立刻变，靠的就是「重拉一次」，这条 mock 必须真的跟着变，
 *  否则验的就只是本地状态自己骗自己。封账也一样：封下的结余成为每张卡上的「上月存假」。
 */
function attendanceServer({ refuse = null, sheet = SHEET, sealRefuse = null } = {}) {
  let current = JSON.parse(JSON.stringify(sheet))
  const puts = []
  const seals = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url).split('?')[0]
    if (path === '/api/attendance/sheet' && (options.method || 'GET') === 'GET') {
      // 每次 GET 都给一个**新的**对象（真 HTTP 响应本来就是一串新解析出来的 JSON）：
      // 回同一个引用的话，页面里 `sheet.value = await api.get(...)` 那一下是同一个值、
      // 不触发重渲染，重拉之后的改动就永远画不出来（票 04 的封账回执正是这么发现的）。
      return jsonResponse(JSON.parse(JSON.stringify(current)))
    }
    if (path === '/api/attendance/seal' && options.method === 'POST') {
      seals.push(JSON.parse(options.body || '{}'))
      if (sealRefuse) return jsonResponse({ detail: sealRefuse }, 409)
      current.previous_month = { month: SEALED_MONTH, sealed_at: SEALED_AT }
      current.employees.forEach((row) => {
        if (row.leave) row.leave.previous_balance = SEALED_BALANCE
      })
      return jsonResponse({
        month: SEALED_MONTH,
        sealed_at: SEALED_AT,
        sealed_employees: current.employees.length,
      })
    }
    const manual = path.match(/^\/api\/attendance\/sheet\/(\d+)\/manual$/)
    if (manual && options.method === 'PUT') {
      const body = JSON.parse(options.body || '{}')
      puts.push({ employeeId: Number(manual[1]), manual: body })
      if (refuse) return jsonResponse({ detail: refuse }, 409)
      const person = current.employees.find((row) => row.id === Number(manual[1]))
      for (const field of ['shift_name', 'lodging', 'borrowed_store', 'resigned']) {
        if (field in body) person[field] = body[field]
      }
      return jsonResponse({
        employee_id: person.id,
        month: current.month,
        shift_name: person.shift_name,
        lodging: person.lodging,
        borrowed_store: person.borrowed_store,
        resigned: person.resigned,
        default_shift_name: person.default_shift_name,
      })
    }
    const edit = path.match(/^\/api\/attendance\/sheet\/(\d+)\/(\d{4}-\d{2}-\d{2})$/)
    if (edit && options.method === 'PUT') {
      const body = JSON.parse(options.body || '{}')
      puts.push({ employeeId: Number(edit[1]), date: edit[2], body })
      if (refuse) return jsonResponse({ detail: refuse }, 409)
      const person = current.employees.find((row) => row.id === Number(edit[1]))
      const cell = person.days.find((row) => row.business_date === edit[2])
      if (body.half_rest !== undefined) {
        // 票 07：「休半天」是一次单独的动作 —— 只改那个标志，班次与状态都不动。
        cell.half_rest = body.half_rest
      } else if (body.is_rest) {
        cell.state = 'rest'
        cell.shift_id = null
        cell.half_rest = false
      } else {
        cell.state = 'work'
        cell.shift_id = body.shift_id
      }
      return jsonResponse({ employee_id: person.id, business_date: cell.business_date })
    }
    return jsonResponse({ detail: 'not found' }, 404)
  })
  return { fetchMock, puts, seals }
}

async function mountSheet(server = attendanceServer()) {
  vi.resetModules()
  vi.stubGlobal('fetch', server.fetchMock)
  const [{ default: AttendanceSheetView }] = await Promise.all([
    import('../AttendanceSheetView.vue'),
  ])
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }],
  })
  const wrapper = mount(AttendanceSheetView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, ...server }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('考勤表页渲染（票 01 / 票 06）', () => {
  it('一天一格，上班 / 休 / 待补，今天之后留空', async () => {
    const { wrapper, fetchMock } = await mountSheet()
    // 打开只读一次（写请求要等店长点下去）。
    expect(fetchMock.mock.calls.map((call) => String(call[0]))).toEqual([
      '/api/attendance/sheet',
    ])
    const card = wrapper.findAll('.aCard')[0]
    const cells = card.findAll('.aDay')
    expect(cells).toHaveLength(4)
    expect(cells.map((cell) => cell.find('.aDayText').text())).toEqual([
      '白班', // 上班的那天写**班次名**（票 06 起下拉要能标出现在是哪个）
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

  it('今天之后的那一格没有能点的东西', async () => {
    const { wrapper } = await mountSheet()
    const cells = wrapper.findAll('.aCard')[0].findAll('.aDay')
    // 前三天（都已到）各有一颗按钮；第四天是死的。
    expect(cells[0].find('button.aDayBtn').exists()).toBe(true)
    expect(cells[2].find('button.aDayBtn').exists()).toBe(true)
    expect(cells[3].find('button.aDayBtn').exists()).toBe(false)
    expect(cells[3].find('select').exists()).toBe(false)
  })

  it('点某一天 → 选另一个班次 → 打 PUT 并立刻变', async () => {
    const server = attendanceServer()
    const { wrapper, puts } = await mountSheet(server)
    const cells = wrapper.findAll('.aCard')[0].findAll('.aDay')

    await cells[0].find('button.aDayBtn').trigger('click')
    const select = wrapper.findAll('.aCard')[0].findAll('.aDay')[0].find('select')
    expect(select.exists()).toBe(true)
    // 下拉里是**启用中的班次** + 休（现在挂着的白班是选中项）。
    expect(select.findAll('option').map((option) => option.text())).toEqual([
      '白班', '夜班', '休',
    ])
    expect(select.element.value).toBe('1')

    await select.setValue('2')
    await flushPromises()

    expect(puts).toEqual([
      { employeeId: 1, date: '2026-09-01', body: { shift_id: 2 } },
    ])
    // 重拉之后那一格立刻写成新班次（页面不等 nudge）。
    const after = wrapper.findAll('.aCard')[0].findAll('.aDay')
    expect(after[0].find('.aDayText').text()).toBe('夜班')
    expect(after[1].find('.aDayText').text()).toBe('休') // 别的日子没动
  })

  it('选「休」就把那天改成休（`is_rest`，不带班次）', async () => {
    const { wrapper, puts } = await mountSheet()
    await wrapper.findAll('.aCard')[0].findAll('.aDay')[0].find('button.aDayBtn').trigger('click')
    const select = wrapper.findAll('.aCard')[0].findAll('.aDay')[0].find('select')

    await select.setValue('rest')
    await flushPromises()

    expect(puts).toEqual([
      { employeeId: 1, date: '2026-09-01', body: { is_rest: true } },
    ])
    expect(
      wrapper.findAll('.aCard')[0].findAll('.aDay')[0].find('.aDayText').text(),
    ).toBe('休')
  })

  it('服务端拒绝时把理由写在那一格上，页面不装作改成功了', async () => {
    const refused = attendanceServer({ refuse: '那天有一条待批的请假在等店长批' })
    const { wrapper } = await mountSheet(refused)
    const cells = wrapper.findAll('.aCard')[0].findAll('.aDay')
    await cells[1].find('button.aDayBtn').trigger('click')
    await wrapper.findAll('.aCard')[0].findAll('.aDay')[1].find('select').setValue('1')
    await flushPromises()

    const after = wrapper.findAll('.aCard')[0].findAll('.aDay')
    expect(after[1].find('.aDayErr').text()).toBe('那天有一条待批的请假在等店长批')
    expect(after[1].find('.aDayText').text()).toBe('休') // 服务端说没写，就不改样子
  })
})

// ── 票 07：半天休（标 / 取消 / 卡片上写着它） ─────────────────────────────

/** 一份带「休半天」的当月考勤表：一天半天休、一天整天班、一天待补、一天还没到。 */
const SHEET_WITH_HALF_REST = {
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
      net_hours: 0,
      shifts: SHIFTS,
      days: [
        // 半天休：仍然挂着白班、仍然是「上班」那一态。
        { business_date: '2026-09-01', state: 'work', shift_id: 1, half_rest: true },
        { business_date: '2026-09-02', state: 'work', shift_id: 2, half_rest: false },
        { business_date: '2026-09-03', state: 'todo', shift_id: null, half_rest: false },
        { business_date: '2026-09-04', state: null, shift_id: null, half_rest: false },
      ],
    },
  ],
}

describe('考勤表页的「休半天」（票 07）', () => {
  function cellsOf(wrapper) {
    return wrapper.findAll('.aCard')[0].findAll('.aDay')
  }

  it('卡片上写着「休半天」，班次没丢、别的天照旧', async () => {
    const { wrapper } = await mountSheet(attendanceServer({ sheet: SHEET_WITH_HALF_REST }))
    expect(cellsOf(wrapper).map((cell) => cell.find('.aDayText').text())).toEqual([
      '休半天', // 那天仍然上白班，只是只上半天
      '夜班',
      '待补',
      '',
    ])
    // 半天休那一格有自己的记号（页面靠它上色，不是靠班次名）。
    expect(cellsOf(wrapper)[0].classes()).toContain('is-half')
    expect(cellsOf(wrapper)[1].classes()).not.toContain('is-half')
  })

  it('点开那格能标「休半天」，也能取消（打的是同一条 PUT）', async () => {
    const server = attendanceServer({ sheet: SHEET_WITH_HALF_REST })
    const { wrapper, puts } = await mountSheet(server)

    // 1) 整天班那天：编辑里那颗按钮是「休半天」→ 点下去发 `{ half_rest: true }`。
    const plain = cellsOf(wrapper)[1]
    await plain.find('button.aDayBtn').trigger('click')
    const toggle = cellsOf(wrapper)[1].find('button.aHalfBtn')
    expect(toggle.exists()).toBe(true)
    expect(toggle.text()).toBe('休半天')

    await toggle.trigger('click')
    await flushPromises()

    expect(puts).toEqual([
      { employeeId: 1, date: '2026-09-02', body: { half_rest: true } },
    ])
    expect(cellsOf(wrapper)[1].find('.aDayText').text()).toBe('休半天')

    // 2) 已经在半天休的那天：按钮变成「取消休半天」→ 发 `{ half_rest: false }`。
    const half = cellsOf(wrapper)[0]
    await half.find('button.aDayBtn').trigger('click')
    const cancel = cellsOf(wrapper)[0].find('button.aHalfBtn')
    expect(cancel.text()).toBe('取消休半天')

    await cancel.trigger('click')
    await flushPromises()

    expect(puts[1]).toEqual({ employeeId: 1, date: '2026-09-01', body: { half_rest: false } })
    expect(cellsOf(wrapper)[0].find('.aDayText').text()).toBe('白班')
  })

  it('没有班次的那天不给标（编辑里没有那颗按钮）', async () => {
    const { wrapper } = await mountSheet(attendanceServer({ sheet: SHEET_WITH_HALF_REST }))

    // 待补那天（有排班行都没有）：编辑里只有班次下拉，没有「休半天」。
    await cellsOf(wrapper)[2].find('button.aDayBtn').trigger('click')
    expect(cellsOf(wrapper)[2].find('select').exists()).toBe(true)
    expect(cellsOf(wrapper)[2].find('button.aHalfBtn').exists()).toBe(false)

    // 今天之后那格是死的：既没有下拉也没有它。
    expect(cellsOf(wrapper)[3].find('button.aHalfBtn').exists()).toBe(false)
    expect(cellsOf(wrapper)[3].find('select').exists()).toBe(false)
  })

  it('服务端拒绝时把理由写在那一格上，页面不装作标上了', async () => {
    const refused = attendanceServer({
      sheet: SHEET_WITH_HALF_REST,
      refuse: '那天没有班次（休 / 还没排到），标不上「休半天」',
    })
    const { wrapper } = await mountSheet(refused)

    await cellsOf(wrapper)[1].find('button.aDayBtn').trigger('click')
    await cellsOf(wrapper)[1].find('button.aHalfBtn').trigger('click')
    await flushPromises()

    expect(cellsOf(wrapper)[1].find('.aDayErr').text()).toBe(
      '那天没有班次（休 / 还没排到），标不上「休半天」',
    )
    expect(cellsOf(wrapper)[1].find('.aDayText').text()).toBe('夜班')
  })
})

// ── 票 03：卡片上的假期台账（暂算值 / 九项 / 算式 / 待批加班） ──────────────

/** 一份带假期台账的当月考勤表：一个人、一天、有待批的加班。 */
const SHEET_WITH_LEAVE = {
  month: '2026-09',
  today: '2026-09-24',
  provisional: true,
  // 上月的封账状态（票 04）：还没封过 —— 页面上这时候画的是「封账上月」那颗按钮。
  previous_month: { month: '2026-08', sealed_at: null },
  days: [{ day: 1, business_date: '2026-09-01', is_today: false, is_future: false }],
  employees: [
    {
      id: 1,
      name: '张三',
      job_title: '炒锅',
      base_salary: 6800,
      seniority_bonus: 400,
      new_hire: false,
      net_hours: 3.5,
      pending_hours: 1,
      pending_count: 2,
      leave: {
        actual_days: 12,
        monthly_rest: 2,
        holiday_makeup: 1,
        birthday_leave: 1,
        annual_leave: 5,
        previous_balance: 5.5,
        pool: 26.5,
        month_days: 30,
        month_attendance_days: 26.5,
        balance: -3.5,
      },
      days: [{ business_date: '2026-09-01', state: 'work' }],
    },
  ],
}

const LEAVE_LABELS = [
  '实际出勤',
  '月例休',
  '法定节假日补休',
  '生日假',
  '年假',
  '上月存假',
  '可用天数池',
  '本月出勤天数',
  '存假',
]

describe('考勤表页的假期台账（票 03）', () => {
  it('顶部明写当月是暂算值（未封账）', async () => {
    const { wrapper } = await mountSheet(attendanceServer({ sheet: SHEET_WITH_LEAVE }))
    expect(wrapper.find('.aProvisional').text()).toContain('暂算值（未封账）')
  })

  it('每张卡摊开假期那九项，存假带负号、可带 .5', async () => {
    const { wrapper } = await mountSheet(attendanceServer({ sheet: SHEET_WITH_LEAVE }))
    const stats = wrapper.find('.aCard').findAll('.aLv')
    expect(stats.map((stat) => stat.find('i').text())).toEqual(LEAVE_LABELS)
    expect(stats.map((stat) => stat.find('b').text())).toEqual([
      '12 天', '2 天', '1 天', '1 天', '5 天', '5.5 天', '26.5 天', '26.5 天', '−3.5 天',
    ])
  })

  it('算式收在「看算式」后面，摊开是六项相加', async () => {
    const { wrapper } = await mountSheet(attendanceServer({ sheet: SHEET_WITH_LEAVE }))
    expect(wrapper.find('.aFormula summary').text()).toBe('看算式')
    expect(wrapper.find('.aFormulaText').text()).toBe(
      '实际出勤 12 天 + 月例休 2 天 + 法定节假日补休 1 天 + 生日假 1 天 + ' +
        '年假 5 天 + 上月存假 5.5 天 = 可用 26.5 天；本月 30 天，存假 −3.5 天',
    )
  })

  it('未审批的加班标色并报出待批小时数，净时长那一格不动', async () => {
    const { wrapper } = await mountSheet(attendanceServer({ sheet: SHEET_WITH_LEAVE }))
    const stat = wrapper.find('.aCard .aStat.is-pending')
    expect(stat.find('i').text()).toBe('当月净时长')
    expect(stat.find('b').text()).toBe('+3.5 小时') // 只算已批准的那份
    expect(stat.find('.aPending').text()).toBe('待批 +1 小时')
  })

  it('服务端没给 leave / 待批时不编数字（老载荷照旧）', async () => {
    const { wrapper } = await mountSheet() // 票 01 的那份 SHEET
    expect(wrapper.find('.aLeave').exists()).toBe(false)
    expect(wrapper.find('.aStat.is-pending').exists()).toBe(false)
    expect(wrapper.find('.aProvisional').exists()).toBe(false)
  })
})

// ── 票 05：卡片上的四个手填列 ────────────────────────────────────────────────

const MANUAL_LABELS = ['本月班次', '是否住宿', '借调门店', '是否离职']
const MANUAL_FIELDS = ['shift_name', 'lodging', 'borrowed_store', 'resigned']

describe('考勤表页的手填列（票 05）', () => {
  it('四个输入口都在卡片上，标签按票面的四项来', async () => {
    const { wrapper } = await mountSheet()
    const rows = wrapper.findAll('.aCard')[0].findAll('.aManualRow .aManualLabel')
    expect(rows.map((label) => label.text())).toEqual(MANUAL_LABELS)
    // 「本月班次」那一格写的是服务端给的那份值（填过就是填的，没填过就是当月众数），
    // 提示语里说清「空着的时候用的是哪条默认」—— 空着有含义，店长要看得见它。
    const first = wrapper.findAll('.aCard')[0]
    expect(first.findAll('.aManualInput').map((input) => input.element.value)).toEqual([
      '早班', '是', '调东莞', '',
    ])
    // 这一位一项都没填过：「本月班次」那一格显示的是服务端给的**当月众数**（夜班）；
    // 提示语里是同一个说法 —— 「这是默认」与「这是我定的」在页面上分得开。
    const second = wrapper.findAll('.aCard')[1]
    expect(second.findAll('.aManualInput').map((input) => input.element.value)).toEqual([
      '夜班', '', '', '',
    ])
    expect(second.find('.aManualHint').text()).toBe('默认：当月最多 夜班')
    // 另外三项的提示语说的是「怎么写」（不是默认值 —— 那三项没有默认）。
    expect(second.findAll('.aManualHint').map((hint) => hint.text())).toEqual([
      '默认：当月最多 夜班', '写「是」或「否」', '如「调东莞」', '写「是」或「否」',
    ])
  })

  it('改完点「存这四项」→ 一次 PUT 带上四项（空着的发 `null`）→ 卡片立刻变', async () => {
    const server = attendanceServer()
    const { wrapper, puts } = await mountSheet(server)
    const card = wrapper.findAll('.aCard')[0]
    const inputs = card.findAll('.aManualInput')
    const button = wrapper.findAll('.aCard')[0].find('.aManual .aSave')

    // 清空「借调门店」（文本输入清空之后 `value` 是 `''`）+ 改成「否」+ 填上班次与离职。
    await inputs[2].setValue('')
    await inputs[1].setValue('否')
    await inputs[0].setValue('早班')
    await inputs[3].setValue('是')
    await button.trigger('click')
    await flushPromises()

    // **四项一起发**：空着的那几项发 `null`（清空存 NULL，与从没填过同义）。
    expect(puts).toEqual([
      {
        employeeId: 1,
        manual: {
          shift_name: '早班',
          lodging: '否',
          borrowed_store: null,
          resigned: '是',
        },
      },
    ])
    // 重拉之后卡片上写的就是最新的那一份（页面不等 nudge）。
    const after = wrapper.findAll('.aCard')[0]
    expect(after.findAll('.aManualInput').map((input) => input.element.value)).toEqual([
      '早班', '否', '', '是',
    ])
  })

  it('服务端拒绝时把理由写在这一块上，页面不装作存进去了', async () => {
    const refused = attendanceServer({ refuse: '台账还没建好，请先做数据库迁移' })
    const { wrapper } = await mountSheet(refused)
    const card = wrapper.findAll('.aCard')[0]
    await card.find('.aManualInput').setValue('早班')
    await wrapper.findAll('.aCard')[0].find('.aManual .aSave').trigger('click')
    await flushPromises()

    const after = wrapper.findAll('.aCard')[0]
    expect(after.find('.aManualErr').text()).toBe('台账还没建好，请先做数据库迁移')
    // 服务端说没写，就不改样子：这一格还是原来填过的那个值。
    expect(after.findAll('.aManualInput')[0].element.value).toBe('早班')
  })

  it('这一块的四项说法都从 util 来，页面不自己写第二份', () => {
    expect(view).toMatch(/manualText\(/)
    expect(view).toMatch(/manualStats\(/)
    expect(view).toMatch(/shiftNameDefault\(/)
    // 手填列**不进员工档案**：页面这一块只打考勤表那一扇门（请求集合那条用例钉着），
    // 这里再钉一次「没有第二个写档案的接口」。
    expect(view).not.toMatch(/employees\/\$\{person\.id\}\/profile/)
  })
})

// ── 票 04：封账上月（按钮 / 回执 / 拒绝） ──────────────────────────────────

/** 顶部那一行里那颗「封账上月」（没有就是 null）。 */
function sealButton(wrapper) {
  return wrapper.findAll('.aActions .btn').find((btn) => btn.text().includes('封账上月')) || null
}

describe('考勤表页的封账上月（票 04）', () => {
  it('没封过账时给一颗按钮，点一次打 POST，卡片上的「上月存假」随即换成刚封下的数', async () => {
    const server = attendanceServer({ sheet: SHEET_WITH_LEAVE })
    const { wrapper, seals, fetchMock } = await mountSheet(server)
    // 打开只读一次：封账要等店长点下去。
    expect(fetchMock.mock.calls.map((call) => String(call[0]))).toEqual([
      '/api/attendance/sheet',
    ])
    expect(sealButton(wrapper).text()).toBe('封账上月')
    expect(wrapper.find('.aSealed').exists()).toBe(false)
    // 封账之前，卡片上那个数是人工期初（票 03 的 5.5）。
    expect(
      wrapper.findAll('.aCard .aLv').find((stat) => stat.find('i').text() === '上月存假')
        .find('b').text(),
    ).toBe('5.5 天')

    await sealButton(wrapper).trigger('click')
    await flushPromises()

    // 封的是上个月，页面上不给月份 —— body 是空的。
    expect(seals).toEqual([{}])
    // 重拉回来的那张表：回执顶掉按钮，那个数换成封下的结余。
    expect(wrapper.find('.aSealed').text()).toBe('2026-08 已封账（2026-09-24 10:00）')
    expect(sealButton(wrapper)).toBe(null)
    expect(
      wrapper.findAll('.aCard .aLv').find((stat) => stat.find('i').text() === '上月存假')
        .find('b').text(),
    ).toBe('−21 天')
  })

  it('已经封过的月不给按钮，只交代什么时候封的（打开就是不动的）', async () => {
    const sealed = {
      ...SHEET_WITH_LEAVE,
      previous_month: { month: SEALED_MONTH, sealed_at: SEALED_AT },
    }
    const { wrapper, fetchMock } = await mountSheet(attendanceServer({ sheet: sealed }))

    expect(wrapper.find('.aSealed').text()).toBe('2026-08 已封账（2026-09-24 10:00）')
    expect(sealButton(wrapper)).toBe(null)
    expect(wrapper.findAll('.aActions .btn').map((btn) => btn.text())).toEqual(['回到月历'])
    expect(fetchMock.mock.calls).toHaveLength(1) // 只读一次，没有多出来的请求
  })

  it('服务端拒了就把理由写出来，按钮照旧在（不装作封成功了）', async () => {
    const refused = attendanceServer({
      sheet: SHEET_WITH_LEAVE,
      sealRefuse: '2026-09 还没过完，等它过完再封',
    })
    const { wrapper } = await mountSheet(refused)

    await sealButton(wrapper).trigger('click')
    await flushPromises()

    expect(wrapper.find('.aErr').text()).toBe('2026-09 还没过完，等它过完再封')
    expect(sealButton(wrapper).text()).toBe('封账上月') // 没封成，按钮还在
    expect(wrapper.find('.aSealed').exists()).toBe(false)
  })
})
