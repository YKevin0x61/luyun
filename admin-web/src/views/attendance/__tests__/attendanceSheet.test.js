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

describe('考勤表页（票 01 / 票 06 / 票 04 / 票 05 / 票 08）', () => {
  it('请求集合就这五条：读当月 + 改某一天 + 封账上月 + 存手填列 + 导出当月', () => {
    // 票 01 时这里钉的是「整页只有一条 GET」；票 06 加上了逐日改格那一条 PUT、
    // 票 04 加上了封账那一 POST、票 05 又加上了手填列那一条 PUT，票 08 加上导出。
    // 集合本身仍然是硬钉的：多一条没人测的路径会让它红。
    const calls = [
      ...view.matchAll(/api\.(get|post|put|delete|patch|download|downloadPost)\(\s*[`']([^`']*)/g),
    ].map((match) => `${match[1]} ${match[2]}`)
    // 顺序不钉（页面里谁先写不影响契约），集合钉死。
    expect([...new Set(calls)].sort()).toEqual([
      'download /api/attendance/sheet.xlsx',
      'get /api/attendance/sheet',
      'post /api/attendance/seal',
      'put /api/attendance/sheet/${person.id}/${cell.business_date}',
      'put /api/attendance/sheet/${person.id}/manual',
    ].sort())
  })

  it('不提供月份选择：打开就是当月', () => {
    // 月份选择器（原生 month 与站内那个组件）一个都不许有：打开就是当月。
    expect(view).not.toMatch(/month-picker/)
    expect(view).not.toMatch(/LuyunDatePicker/)
    expect(view).not.toMatch(/type="month"/)
    // 导出（票 08）不自己拼文件名 —— 名字由服务端的 `Content-Disposition` 带回来，
    // 页面这一侧写死的月份迟早与服务端算的那个对不上：那条下载请求必须是**一条写死的
    // 路径**（没有模板占位、没有拼接）。
    expect([...view.matchAll(/api\.download\(\s*([^)]*)\)/g)].map((m) => m[1].trim())).toEqual([
      "'/api/attendance/sheet.xlsx'",
    ])
  })

  it('三态与两列的说法从 util 来，页面不自己写第二份', () => {
    expect(view).toMatch(/dayStateText\(/)
    expect(view).toMatch(/newHireText\(/)
    expect(view).toMatch(/formatNetHours\(/)
  })

  it('只给已经到的那几天装控件（今天之后是死的格子）', () => {
    // 验收 45：今天之后的日子**不能编辑** —— 不是「点了再弹一句拒绝」，是压根没有
    // 能点的东西。挂控件的那两处都由 `editable(cell)` 守着。
    // 编辑记号只出现在**被点的那一格**：判据是「这个人 + 这一天」，不是单靠日期
    // （旧版按日期记编辑态，点一天会让每张含这天的卡各开一条编辑带）。格子上的记号与
    // 网格下面那条编辑带都走这一个判据 `isEditing`，别处不自己再算一遍。
    expect(view).toMatch(
      /editing\.value\?\.employeeId === person\.id && editing\.value\?\.date === cell\.business_date/,
    )
    expect(view).toMatch(/v-if="isEditing\(person, cell\)"/)
    expect(view).toMatch(/'is-editing': isEditing\(person, cell\)/)
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
      // `shift_name_stored` 是台账里的**原始**列（A 批加的那一位）：输入框绑它。
      // `shift_name` 是读侧的回填值（没填过时 = 当月众数），导出件 A 列用它。
      shift_name: '早班',
      shift_name_stored: '早班',
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
      // 这一位一项都没填过：`shift_name_stored` 是 `null`（输入框空的），
      // `shift_name` 是服务端回填的当月众数（导出件用它 —— 别把它预填进输入框）。
      shift_name: '夜班',
      shift_name_stored: null,
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
function attendanceServer({ refuse = null, sheet = SHEET, sealRefuse = null, exportRefuse = null } = {}) {
  let current = JSON.parse(JSON.stringify(sheet))
  const puts = []
  const seals = []
  const exports = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url).split('?')[0]
    if (path === '/api/attendance/sheet' && (options.method || 'GET') === 'GET') {
      // 每次 GET 都给一个**新的**对象（真 HTTP 响应本来就是一串新解析出来的 JSON）：
      // 回同一个引用的话，页面里 `sheet.value = await api.get(...)` 那一下是同一个值、
      // 不触发重渲染，重拉之后的改动就永远画不出来（票 04 的封账回执正是这么发现的）。
      return jsonResponse(JSON.parse(JSON.stringify(current)))
    }
    // 导出（票 08）：一条 GET，服务端把工作簿当附件下发 —— 前端只看响应头里那个名字。
    if (path === '/api/attendance/sheet.xlsx') {
      exports.push(path)
      if (exportRefuse) return jsonResponse({ detail: exportRefuse }, 503)
      return {
        ok: true,
        status: 200,
        headers: new Headers({
          'content-type':
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          'content-disposition': `attachment; filename*=UTF-8''${encodeURIComponent(
            `考勤表-${current.month}.xlsx`,
          )}`,
        }),
        blob: async () => new Blob(['fake-xlsx']),
      }
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
  return { fetchMock, puts, seals, exports }
}

async function mountSheet(server = attendanceServer()) {
  vi.resetModules()
  vi.stubGlobal('fetch', server.fetchMock)
  // jsdom 没有 objectURL / 下载：导出那条路（票 08）走的是 `api.download`，
  // 它建一个 `<a download>` 再点一下 —— 这两个动作在这里都得存在才不会抛。
  URL.createObjectURL = vi.fn(() => 'blob:mock')
  URL.revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
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
  vi.restoreAllMocks()
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

  // 编辑带（B 批返工 ⑥）：控件从格子里搬到了网格下面那条带子 —— 这三条用例的
  // **判据一字没改**（打哪条 PUT、带什么 body、页面上立刻写成什么、被拒时不许改样子），
  // 换掉的只有「点什么」：原先点格子里的下拉，现在点带子里的 chip。
  function bandOf(wrapper) {
    return wrapper.findAll('.aCard')[0].find('.aEditBar')
  }

  it('点某一天 → 编辑带里选另一个班次 → 打 PUT 并立刻变', async () => {
    const server = attendanceServer()
    const { wrapper, puts } = await mountSheet(server)
    const cells = wrapper.findAll('.aCard')[0].findAll('.aDay')

    await cells[0].find('button.aDayBtn').trigger('click')
    // 带子里是**启用中的班次** + 休 + 休半天（那天是上班）+ 取消；
    // 现在挂着的白班是 `is-on` 那颗（原先断的是「下拉的选中项」）。
    const chips = bandOf(wrapper).findAll('button.aChip')
    expect(chips.map((chip) => chip.text())).toEqual(['白班', '夜班', '休', '休半天', '取消'])
    expect(chips[0].classes()).toContain('is-on')
    expect(chips[1].classes()).not.toContain('is-on')
    // 格子里**不再**有下拉（控件搬家了，那一格只剩日期与文案）。
    expect(cells[0].find('select').exists()).toBe(false)

    await chips[1].trigger('click')
    await flushPromises()

    expect(puts).toEqual([
      { employeeId: 1, date: '2026-09-01', body: { shift_id: 2 } },
    ])
    // 重拉之后那一格立刻写成新班次（页面不等 nudge），带子收起。
    const after = wrapper.findAll('.aCard')[0].findAll('.aDay')
    expect(after[0].find('.aDayText').text()).toBe('夜班')
    expect(after[1].find('.aDayText').text()).toBe('休') // 别的日子没动
    expect(wrapper.findAll('.aCard')[0].find('.aEditBar').exists()).toBe(false)
  })

  it('一次点击只有一个编辑目标：点第一张卡的那天，只有第一张卡出带子', async () => {
    // 两张卡都含 9/1。旧版编辑态按**日期**记（`editingDate` 是个字符串），点一次会让
    // 两张卡各开一条带子；用户的操作是「在某人那张卡上点某一天」，一次点击只能有一个目标。
    const { wrapper } = await mountSheet()
    expect(wrapper.findAll('.aCard')).toHaveLength(2)

    await wrapper
      .findAll('.aCard')[0]
      .findAll('.aDay')[0]
      .find('button.aDayBtn')
      .trigger('click')

    // 有且**只有一条**带子，且它落在第一张卡（张三）里、指的是 1 号。
    expect(wrapper.findAll('.aCard')[0].find('.aName').text()).toBe('张三')
    const bands = wrapper.findAll('.aEditBar')
    expect(bands).toHaveLength(1)
    expect(bands[0].attributes('aria-label')).toBe('改 1 号的班')
    expect(wrapper.findAll('.aCard')[0].find('.aEditBar').exists()).toBe(true)
    // 第二张卡（李四）的同一天不进这一态：没有带子，那一格也没有编辑记号。
    expect(wrapper.findAll('.aCard')[1].find('.aEditBar').exists()).toBe(false)
    expect(
      wrapper.findAll('.aCard')[1].findAll('.aDay')[0].classes(),
    ).not.toContain('is-editing')
    // 第一张卡里也只有被点的那一格进这一态。
    const first = wrapper.findAll('.aCard')[0].findAll('.aDay')
    expect(first[0].classes()).toContain('is-editing')
    expect(first[1].classes()).not.toContain('is-editing')
  })

  it('编辑带的抬头写着改的是哪一天、那一天现在是什么', async () => {
    const { wrapper } = await mountSheet()
    const card = wrapper.findAll('.aCard')[0]

    await card.findAll('.aDay')[0].find('button.aDayBtn').trigger('click')
    const band = bandOf(wrapper)
    expect(band.attributes('role')).toBe('group')
    expect(band.attributes('aria-label')).toBe('改 1 号的班')
    expect(band.find('.aEditNow b').text()).toBe('1 号')
    expect(band.find('.aEditNow').text()).toContain('现在 白班')

    // 9/3 是「今天」：抬头补一句「（今天）」；那天的现状走 `dayStateText`（待补）。
    await card.findAll('.aDay')[2].find('button.aDayBtn').trigger('click')
    const today = bandOf(wrapper)
    expect(today.attributes('aria-label')).toBe('改 3 号的班')
    expect(today.find('.aEditNow').text()).toContain('3 号（今天）')
    expect(today.find('.aEditNow').text()).toContain('现在 待补')
  })

  it('选「休」就把那天改成休（`is_rest`，不带班次）', async () => {
    const { wrapper, puts } = await mountSheet()
    await wrapper.findAll('.aCard')[0].findAll('.aDay')[0].find('button.aDayBtn').trigger('click')
    const rest = bandOf(wrapper)
      .findAll('button.aChip')
      .find((chip) => chip.text() === '休')

    await rest.trigger('click')
    await flushPromises()

    expect(puts).toEqual([
      { employeeId: 1, date: '2026-09-01', body: { is_rest: true } },
    ])
    expect(
      wrapper.findAll('.aCard')[0].findAll('.aDay')[0].find('.aDayText').text(),
    ).toBe('休')
  })

  it('服务端拒绝时把理由写在编辑带里，页面不装作改成功了', async () => {
    const refused = attendanceServer({ refuse: '那天有一条待批的请假在等店长批' })
    const { wrapper } = await mountSheet(refused)
    const cells = wrapper.findAll('.aCard')[0].findAll('.aDay')
    await cells[1].find('button.aDayBtn').trigger('click')
    await bandOf(wrapper)
      .findAll('button.aChip')
      .find((chip) => chip.text() === '白班')
      .trigger('click')
    await flushPromises()

    // 理由留在**这条带子**里（被拒时带子不收，店长正好接着改），并且带 `role="alert"`。
    const err = bandOf(wrapper).find('.aDayErr')
    expect(err.text()).toBe('那天有一条待批的请假在等店长批')
    expect(err.attributes('role')).toBe('alert')
    // 那一格给红边（`is-error`），且**不许**变样子。
    const after = wrapper.findAll('.aCard')[0].findAll('.aDay')
    expect(after[1].classes()).toContain('is-error')
    expect(after[1].find('.aDayText').text()).toBe('休') // 服务端说没写，就不改样子
    // 红边**只落在他那一格**上：另一张卡**同一天**不许有 `is-error`。
    // 判据同时把「同一天」钉住（两张卡的标题必须一样），否则这条断言可能比的是两个不同的日子。
    const other = wrapper.findAll('.aCard')[1]
    expect(other.find('.aName').text()).toBe('李四')
    expect(other.findAll('.aDay')[1].attributes('title')).toBe(after[1].attributes('title'))
    expect(other.findAll('.aDay')[1].classes()).not.toContain('is-error')
  })

  // ── B 批返工 ⑥：编辑态**不动网格**（用户截图里的那个错位） ─────────────────
  //
  // 断的是**结构不变**而不是 CSS 里有没有某条规则：jsdom 不做布局，`offsetTop` /
  // `offsetLeft` 在这里恒为 0，拿它断「没错位」永远绿（tautology）。真正决定
  // 「7 格一行」的是 `.aDays` 里那串格子的**顺序与各自画出来的东西**——只要进编辑态
  // 前后这两样一字不变，格子就不可能换列换行。真实的 `offsetTop/offsetLeft` 实测在
  // Chromium 里做（见 `.scratch/考勤表导出/shots/` 那组截图脚本），这里守住结构。
  it('零重排：进编辑态不改网格的格子序列与内容（编辑带在网格外面）', async () => {
    const { wrapper } = await mountSheet()
    const card = wrapper.findAll('.aCard')[0]
    const snapshot = () =>
      card.find('.aDays').findAll('.aDay').map((cell) => ({
        date: cell.attributes('title'),
        // 格子**自己画出来的**东西（日期数字 + 那一格的文案）：进编辑态不许动它。
        // 曾经这里被换成一个下拉 —— 选项文字也会落进 textContent，于是这一条会红。
        text: cell.element.textContent,
        html: cell.element.innerHTML,
      }))

    const before = snapshot()
    expect(card.find('.aDays').find('.aEditBar').exists()).toBe(false)

    await card.findAll('.aDay')[0].find('button.aDayBtn').trigger('click')

    const after = snapshot()
    // ① 格子一个不多、一个不少、顺序不变（7 格一行的对齐靠的就是这个序列）。
    expect(after.map((cell) => cell.date)).toEqual(before.map((cell) => cell.date))
    // ② 每一格画出来的东西一字不变（被点那格也一样：它只是不再是一颗按钮）。
    expect(after.map((cell) => cell.text)).toEqual(before.map((cell) => cell.text))
    // ③ 没被点的那些格子连 DOM 都一字不变（只有被点那格多了 `is-editing` 这个记号）。
    expect(after.slice(1).map((cell) => cell.html)).toEqual(before.slice(1).map((cell) => cell.html))
    // ④ 被点的那一格仍然在网格里，只是多一个记号（看得出在改哪一天）。
    const cells = card.find('.aDays').findAll('.aDay')
    expect(cells[0].classes()).toContain('is-editing')
    expect(cells[1].classes()).not.toContain('is-editing')
    // ⑤ 编辑带在 `.aDays` **外面**、同一张卡里：网格多不出第二个子元素来撑出行结构。
    expect(card.find('.aDays').find('.aEditBar').exists()).toBe(false)
    expect(card.find('.aEditBar').exists()).toBe(true)
    // ⑥ 顺带钉住那条把格子拔出网格的规则不再回来。
    expect(view).not.toMatch(/\.aDay\.is-editing[^{]*\{[^}]*grid-column/)
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

  // 「休半天」那颗搬进了网格下面的编辑带（B 批返工 ⑥）：它仍然是**单独**的一次写，
  // 只是不再挤在 43px 的格子里。
  function bandOf(wrapper) {
    return wrapper.findAll('.aCard')[0].find('.aEditBar')
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

  it('点开那格能在编辑带里标「休半天」，也能取消（打的是同一条 PUT）', async () => {
    const server = attendanceServer({ sheet: SHEET_WITH_HALF_REST })
    const { wrapper, puts } = await mountSheet(server)

    // 1) 整天班那天：编辑带里那颗按钮是「休半天」→ 点下去发 `{ half_rest: true }`。
    const plain = cellsOf(wrapper)[1]
    await plain.find('button.aDayBtn').trigger('click')
    const toggle = bandOf(wrapper).find('button.aHalfBtn')
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
    const cancel = bandOf(wrapper).find('button.aHalfBtn')
    expect(cancel.text()).toBe('取消休半天')

    await cancel.trigger('click')
    await flushPromises()

    expect(puts[1]).toEqual({ employeeId: 1, date: '2026-09-01', body: { half_rest: false } })
    expect(cellsOf(wrapper)[0].find('.aDayText').text()).toBe('白班')
  })

  it('没有班次的那天不给标（编辑带里没有那颗按钮）', async () => {
    const { wrapper } = await mountSheet(attendanceServer({ sheet: SHEET_WITH_HALF_REST }))

    // 待补那天（有排班行都没有）：编辑带里只有班次与休，没有「休半天」。
    await cellsOf(wrapper)[2].find('button.aDayBtn').trigger('click')
    expect(cellsOf(wrapper)[2].find('select').exists()).toBe(false)
    expect(bandOf(wrapper).find('button.aHalfBtn').exists()).toBe(false)

    // 今天之后那格是死的：既点不开，也没有那颗按钮。
    expect(cellsOf(wrapper)[3].find('button.aDayBtn').exists()).toBe(false)
    expect(cellsOf(wrapper)[3].find('select').exists()).toBe(false)
  })

  it('「休半天」那颗只在**上班**那一态出现（休 / 待补 都没有）', async () => {
    // 与服务的 `half_rest_needs_shift` 同一条口径：没有班次就谈不上「只上半天」。
    const { wrapper } = await mountSheet()
    const states = ['work', 'rest', 'todo']

    for (const [index, state] of states.entries()) {
      await cellsOf(wrapper)[index].find('button.aDayBtn').trigger('click')
      expect(cellsOf(wrapper)[index].classes()).toContain(`is-${state}`)
      expect(bandOf(wrapper).find('button.aHalfBtn').exists()).toBe(state === 'work')
    }
  })

  it('服务端拒绝时把理由写在编辑带里，页面不装作标上了', async () => {
    const refused = attendanceServer({
      sheet: SHEET_WITH_HALF_REST,
      refuse: '那天没有班次（休 / 还没排到），标不上「休半天」',
    })
    const { wrapper } = await mountSheet(refused)

    await cellsOf(wrapper)[1].find('button.aDayBtn').trigger('click')
    await bandOf(wrapper).find('button.aHalfBtn').trigger('click')
    await flushPromises()

    expect(bandOf(wrapper).find('.aDayErr').text()).toBe(
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
    // 「本月班次」那一格绑的是**台账里的原始值**（`shift_name_stored`）：填过就显示填的，
    // 没填过就是**空的**；当月众数只出现在提示语里 —— 预填它，店长一存就把这条默认
    // 固化成了他定的值（导出件的 A 列读的是 `shift_name`，那一位的含义没动）。
    const first = wrapper.findAll('.aCard')[0]
    expect(first.findAll('.aManualInput').map((input) => input.element.value)).toEqual([
      '早班', '是', '调东莞', '',
    ])
    // 这一位一项都没填过：「本月班次」那一格是**空的**（空着有含义：用默认），
    // 提示语里说清那条默认是什么 —— 「这是默认」与「这是我定的」在页面上分得开。
    const second = wrapper.findAll('.aCard')[1]
    expect(second.findAll('.aManualInput').map((input) => input.element.value)).toEqual([
      '', '', '', '',
    ])
    expect(second.find('.aManualHint').text()).toBe('默认：当月最多 夜班')
    // 回显那一行念的也是**台账里存了什么**（没填过 = `—`），不是服务端回填的众数。
    const [shiftNow] = second.findAll('.aManualNowOne')
    expect(shiftNow.find('i').text()).toBe('本月班次')
    expect(shiftNow.find('b').text()).toBe('—')
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

  it('只改别的那几项、班次那格不动 → 不会把服务端回填的众数写进台账', async () => {
    // 这一位一项都没填过（`shift_name_stored` = `null`，服务端把 `shift_name` 回填成当月众数
    // 「夜班」）。店长只改「是否住宿」，班次那格一个字没打 —— 发出去的必须是 `null`，
    // 不能是 `夜班`：写进去就把这条默认**固化**成他定的值了，以后再改排班这一格也不跟着变。
    const server = attendanceServer()
    const { wrapper, puts } = await mountSheet(server)
    const card = wrapper.findAll('.aCard')[1]
    expect(card.findAll('.aManualInput')[0].element.value).toBe('')

    await card.findAll('.aManualInput')[1].setValue('是')
    await card.find('.aManual .aSave').trigger('click')
    await flushPromises()

    expect(puts).toEqual([
      {
        employeeId: 2,
        manual: { shift_name: null, lodging: '是', borrowed_store: null, resigned: null },
      },
    ])
    // 重拉之后那一格**还是空的**（页面也没替服务端认下这条默认）。
    expect(wrapper.findAll('.aCard')[1].findAll('.aManualInput')[0].element.value).toBe('')
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

describe('考勤表页的封账上月（票 04 / B 批返工 ⑦）', () => {
  it('没封过账时给一颗按钮，点一次先确认、再打 POST，卡片上的「上月存假」随即换成刚封下的数', async () => {
    const server = attendanceServer({ sheet: SHEET_WITH_LEAVE })
    const { wrapper, seals, fetchMock } = await mountSheet(server)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
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

    // 封账**不可逆**（写的是上月结余、封过不再重算）：点下去先问一次。
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(confirm.mock.calls[0][0]).toContain('上月')
    expect(confirm.mock.calls[0][0]).toContain('不再重算')
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

  it('确认框里点「取消」就一个字节都不写（按钮还在、没有回执）', async () => {
    const server = attendanceServer({ sheet: SHEET_WITH_LEAVE })
    const { wrapper, seals, fetchMock } = await mountSheet(server)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)

    await sealButton(wrapper).trigger('click')
    await flushPromises()

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(seals).toEqual([]) // 没发那条 POST
    expect(fetchMock.mock.calls).toHaveLength(1) // 也没多拉一次表
    expect(sealButton(wrapper).text()).toBe('封账上月')
    expect(wrapper.find('.aSealed').exists()).toBe(false)
  })

  it('已经封过的月不给按钮，只交代什么时候封的（打开就是不动的）', async () => {
    const sealed = {
      ...SHEET_WITH_LEAVE,
      previous_month: { month: SEALED_MONTH, sealed_at: SEALED_AT },
    }
    const { wrapper, fetchMock } = await mountSheet(attendanceServer({ sheet: sealed }))

    expect(wrapper.find('.aSealed').text()).toBe('2026-08 已封账（2026-09-24 10:00）')
    expect(sealButton(wrapper)).toBe(null)
    expect(wrapper.findAll('.aActions .btn').map((btn) => btn.text())).toEqual([
      '回到月历',
      '导出这个月',
    ])
    expect(fetchMock.mock.calls).toHaveLength(1) // 只读一次，没有多出来的请求
  })

  it('服务端拒了就把理由写出来，按钮照旧在（不装作封成功了）', async () => {
    const refused = attendanceServer({
      sheet: SHEET_WITH_LEAVE,
      sealRefuse: '2026-09 还没过完，等它过完再封',
    })
    const { wrapper } = await mountSheet(refused)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    await sealButton(wrapper).trigger('click')
    await flushPromises()

    expect(wrapper.find('.aErr').text()).toBe('2026-09 还没过完，等它过完再封')
    expect(sealButton(wrapper).text()).toBe('封账上月') // 没封成，按钮还在
    expect(wrapper.find('.aSealed').exists()).toBe(false)
  })
})

// ── B 批返工 ⑦：五颗按钮的主次 ──────────────────────────────────────────────
//
// 「导出这个月」（这一页的主动作）与「存这四项」用主按钮样式；「封账上月」是不可逆的
// 那一颗，所以它点下去先确认一次（不是靠颜色挡人）。

describe('按钮主次（B 批返工 ⑦）', () => {
  it('「导出这个月」与「存这四项」是主按钮，其余三颗是次按钮', async () => {
    const { wrapper } = await mountSheet()

    expect(exportButton(wrapper).classes()).toContain('btn-primary')
    expect(wrapper.findAll('.aCard')[0].find('.aManual .aSave').classes()).toContain('btn-primary')
    // 不可逆的那颗不用主色招人（它靠确认框挡），「回到月历」也不抢。
    expect(sealButton(wrapper).classes()).not.toContain('btn-primary')
    const back = wrapper.findAll('.aActions .btn').find((btn) => btn.text() === '回到月历')
    expect(back.classes()).not.toContain('btn-primary')
  })
})

// ── 票 08：一键导出 .xlsx（顶部那颗按钮） ────────────────────────────────────

/** 顶部那一行里那颗「导出这个月」（没有就是 null）。 */
function exportButton(wrapper) {
  return wrapper.findAll('.aActions .btn').find((btn) => btn.text().includes('导出')) || null
}

describe('考勤表页的导出这个月（票 08）', () => {
  it('打开不导出：只有店长点下去才打那一条 GET', async () => {
    const { wrapper, fetchMock, exports } = await mountSheet()
    expect(exportButton(wrapper).text()).toBe('导出这个月')
    expect(fetchMock.mock.calls.map((call) => String(call[0]))).toEqual([
      '/api/attendance/sheet',
    ])
    expect(exports).toEqual([])
  })

  it('点一次导出 → 打 GET /api/attendance/sheet.xlsx，并用响应头里的文件名落盘', async () => {
    const server = attendanceServer()
    const { wrapper, exports } = await mountSheet(server)

    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click')
    await exportButton(wrapper).trigger('click')
    await flushPromises()

    expect(exports).toEqual(['/api/attendance/sheet.xlsx'])
    // 文件名由服务端的 `Content-Disposition` 带回来（`考勤表-YYYY-MM.xlsx`）——
    // 页面不自己拼那个月份。
    const anchor = click.mock.instances[0]
    expect(anchor.download).toBe('考勤表-2026-09.xlsx')
    expect(wrapper.find('.aErr').exists()).toBe(false)
    expect(exportButton(wrapper).text()).toBe('导出这个月') // 回执之后按钮回到常态
  })

  it('服务端拒了就把理由写出来，页面不装作导出来了', async () => {
    const refused = attendanceServer({ exportRefuse: '考勤表要读的表还没建好' })
    const { wrapper, exports } = await mountSheet(refused)

    await exportButton(wrapper).trigger('click')
    await flushPromises()

    expect(exports).toEqual(['/api/attendance/sheet.xlsx'])
    expect(wrapper.find('.aErr').text()).toBe('考勤表要读的表还没建好')
    expect(exportButton(wrapper).text()).toBe('导出这个月') // 没导成，按钮还在
  })
})

// ── 收尾修复 ②：手填那一块里的「期初存假（上月结转）」 ──────────────────────
//
// 它写的是月度台账**本月那一行**的 `opening_balance`（票 03 的裁定：人工期初读的就是
// 本月那一行），走的是与四列手填**同一条**月级 PUT —— 但自己一颗按钮：那四列是每张卡
// 随时会改的日常项，期初是上线当月手工给**一次**的账，混在一起会把这一格顺手清掉。

/** 一份带人工期初的当月考勤表（其余照票 03 那份）。 */
const SHEET_WITH_OPENING = {
  ...SHEET_WITH_LEAVE,
  employees: [{ ...SHEET_WITH_LEAVE.employees[0], opening_balance: 5.5 }],
}

/** 一份只认月级 PUT 的「服务端」：GET 出表、PUT 记下 body 并把期初写回去。 */
function openingServer() {
  const current = JSON.parse(JSON.stringify(SHEET_WITH_OPENING))
  const puts = []
  const fetchMock = vi.fn(async (url, options = {}) => {
    const path = String(url).split('?')[0]
    if (path === '/api/attendance/sheet' && (options.method || 'GET') === 'GET') {
      return jsonResponse(JSON.parse(JSON.stringify(current)))
    }
    const manual = path.match(/^\/api\/attendance\/sheet\/(\d+)\/manual$/)
    if (manual && options.method === 'PUT') {
      const body = JSON.parse(options.body || '{}')
      puts.push({ employeeId: Number(manual[1]), manual: body })
      const person = current.employees.find((row) => row.id === Number(manual[1]))
      person.opening_balance = body.opening_balance
      return jsonResponse({ employee_id: person.id, month: current.month })
    }
    return jsonResponse({ detail: 'not found' }, 404)
  })
  return { fetchMock, puts }
}

describe('考勤表页的期初存假（收尾修复 ②）', () => {
  it('卡片上有这个输入口：标签说清是哪一位、提示语说清只在上线当月给一次', async () => {
    const { wrapper } = await mountSheet(attendanceServer({ sheet: SHEET_WITH_OPENING }))
    const card = wrapper.findAll('.aCard')[0]

    expect(card.find('.aOpeningLabel').text()).toBe('期初存假（上月结转）')
    // 输入框里是服务端那一份（人工期初是台账上的值，不是页面自己记的）。
    expect(card.find('.aOpeningInput').element.value).toBe('5.5')
    expect(card.find('.aOpeningHint').text()).toContain('只在上线当月手工给一次')
  })

  it('老载荷没给这一位时留空，不是 `undefined`', async () => {
    const { wrapper } = await mountSheet() // 票 01 那份 SHEET：压根没有 `opening_balance`
    expect(wrapper.findAll('.aCard')[0].find('.aOpeningInput').element.value).toBe('')
  })

  it('改完点「存期初存假」→ 打同一条月级 PUT（只带这一位）→ 卡片立刻变', async () => {
    const server = openingServer()
    const { wrapper, puts } = await mountSheet(server)
    const card = wrapper.findAll('.aCard')[0]

    await card.find('.aOpeningInput').setValue('7.5')
    await card.find('.aOpeningSave').trigger('click')
    await flushPromises()

    expect(puts).toEqual([{ employeeId: 1, manual: { opening_balance: 7.5 } }])
    expect(wrapper.findAll('.aCard')[0].find('.aOpeningInput').element.value).toBe('7.5')
  })

  it('清空那一格 → 发 `null`（清空存 NULL，与从没填过同义）', async () => {
    const server = openingServer()
    const { wrapper, puts } = await mountSheet(server)
    const card = wrapper.findAll('.aCard')[0]

    await card.find('.aOpeningInput').setValue('')
    await card.find('.aOpeningSave').trigger('click')
    await flushPromises()

    expect(puts).toEqual([{ employeeId: 1, manual: { opening_balance: null } }])
  })

  it('填了不是数字的东西就不发请求，理由写在那一块上', async () => {
    const server = openingServer()
    const { wrapper, puts } = await mountSheet(server)
    const card = wrapper.findAll('.aCard')[0]

    await card.find('.aOpeningInput').setValue('七点五')
    await card.find('.aOpeningSave').trigger('click')
    await flushPromises()

    // `Number('七点五')` 是 `NaN`，发出去会变成 `null` —— 那会把这一格悄悄清掉。
    expect(puts).toEqual([])
    expect(wrapper.findAll('.aCard')[0].find('.aOpeningErr').text()).toContain('数字')
  })

  it('服务端拒了就把理由写出来，页面不装作存进去了', async () => {
    const refused = attendanceServer({
      sheet: SHEET_WITH_OPENING,
      refuse: '台账还没建好，请先做数据库迁移',
    })
    const { wrapper } = await mountSheet(refused)
    const card = wrapper.findAll('.aCard')[0]

    await card.find('.aOpeningInput').setValue('7.5')
    await card.find('.aOpeningSave').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.aCard')[0].find('.aOpeningErr').text()).toBe(
      '台账还没建好，请先做数据库迁移',
    )
    expect(wrapper.findAll('.aCard')[0].find('.aOpeningInput').element.value).toBe('5.5')
  })
})

// ── 收尾修复 ⑥ / B 批返工 ⑤：「借调门店」那处与历史表的偏差 ────────────────
//
// 这处偏差**必须留着**（它是与历史样表唯一的有意偏差），只是不必占三行可视小字 ——
// 那三行把一排四个输入口拽得参差不齐，现在压进输入框的 `title`（悬停可见）。

describe('「借调门店」那处例外压进 title（B 批返工 ⑤）', () => {
  it('那一格的 title 说清：历史表写在某一天的格子上，本版按月记、不落进导出件的日格', async () => {
    const { wrapper } = await mountSheet()
    const field = wrapper.findAll('.aCard')[0].findAll('.aManualField')[2]

    expect(field.find('.aManualLabel').text()).toBe('借调门店')
    const title = field.find('input').attributes('title')
    expect(title).toContain('某一天')
    expect(title).toContain('不落进')
    expect(title).toContain('导出')
  })

  it('这处注明只跟着「借调门店」那一项，页面上不再占那三行小字', async () => {
    const { wrapper } = await mountSheet()
    const card = wrapper.findAll('.aCard')[0]

    expect(card.findAll('.aManualNote')).toHaveLength(0)
    // 借调门店是第三个字段（`MANUAL_INPUTS` 的顺序）：只有它那一格挂 title。
    expect(card.findAll('.aManualField')[2].find('input').attributes('title')).toBeTruthy()
    expect(card.findAll('.aManualField')[1].find('input').attributes('title')).toBeUndefined()
  })
})

// ── 收尾修复 ⑥：日格那几个字，把两份源文件钉在一起 ──────────────────────────

describe('日格文案与导出件的常量逐字相同（收尾修复 ⑥）', () => {
  it('export.py 的 DAY_TEXT / HALF_REST_TEXT / TODO_TEXT == attendanceSheet.js 的三种写法', () => {
    // 这一份文件的注释原先自称「卡片与导出件共用同一批字」，而导出件是 Python、import
    // 不到 `utils/attendanceSheet.js`，只好在 `services/attendance/export.py` 里另存一份
    // （同仓库既有的读源码式断言，如前端那几条 `readFileSync` 读 `.py` / `main.py`）。
    // 那一句话本身没法成立，于是改成这条**把两份源文件都读出来**的比对：改一边忘另一边
    // 就红 —— 比注释诚实。
    const exportPy = readFileSync(join(here, '../../../../../services/attendance/export.py'), 'utf8')

    const constant = (name) => {
      const matched = exportPy.match(new RegExp(`^${name} = "(.+)"$`, 'm'))
      expect(matched, `${name} 不在 export.py 里了`).not.toBeNull()
      return matched[1]
    }
    // 前端那三种写法也从源码里读出来（不是 import 之后再比）：这一条要钉的是「两份源文件
    // 里写的是同一批字」，所以两边都读源码，谁也不比谁多一层加工。
    const written = (pattern) => {
      const matched = copy.match(pattern)
      expect(matched, `attendanceSheet.js 里那一种写法找不到了：${pattern}`).not.toBeNull()
      return matched[1]
    }

    expect(constant('DAY_TEXT')).toBe(written(/^\s*rest: '(.*)',$/m))
    expect(constant('HALF_REST_TEXT')).toBe(written(/^export const HALF_REST_TEXT = '(.*)'$/m))
    expect(constant('TODO_TEXT')).toBe(written(/^\s*todo: '(.*)',$/m))
    // 「上班」是唯一一处**有意**不同：导出件里出勤是**空格子**（不写字），所以
    // `export.py` 里压根没有它的常量 —— 不是漏了，是那一位在纸面上就是空的。
    expect(written(/^\s*work: '(.*)',$/m)).toBe('上班')
  })
})

// ── B 批返工 ③②⑥⑧①：这一页的版式（窄屏、撞色、编辑态、圆角、按钮排布） ──────
//
// 这几条改的都是「页面上看到什么」。能挂起来量的（DOM 次序、有没有那个标记）就挂起来量；
// 剩下的是**写在 CSS 里的一个事实**（两条规则用了同一个颜色 / 有没有 `flex-wrap`），
// 照本仓既有的写法读源码断它 —— 判定即结论，不是拿文本去断行为。

describe('考勤表页的版式（B 批返工 ③②⑥⑧①）', () => {
  it('③ 手填那一块的次序：四输入 → 回显 + 存这四项 → 期初块 + 存期初存假', async () => {
    const { wrapper } = await mountSheet(attendanceServer({ sheet: SHEET_WITH_OPENING }))
    const manual = wrapper.findAll('.aCard')[0].find('.aManual')
    // 回显与它自己那颗「存这四项」必须挨着（中间不再岔进期初那一块）。
    const order = [...manual.element.querySelectorAll(
      '.aManualRow, .aManualNow, .aManualFoot, .aOpening',
    )].map((el) => el.className.split(' ')[0])
    expect(order).toEqual(['aManualRow', 'aManualNow', 'aManualFoot', 'aOpening'])
  })

  it('② 「今天」那一格用 `--accent` 描边（`--warn` 留给待补），格子里另有「今」字', async () => {
    const { wrapper } = await mountSheet()
    const cells = wrapper.findAll('.aCard')[0].findAll('.aDay')
    // fixture 里第 3 天同时是「今天」与「待补」—— 正是撞色那一格。
    expect(cells[2].classes()).toContain('is-today')
    expect(cells[2].classes()).toContain('is-todo')
    expect(cells[2].find('.aTodayMark').text()).toBe('今')
    expect(cells[0].find('.aTodayMark').exists()).toBe(false)

    const today = view.match(/\.aDay\.is-today\s*\{[^}]*\}/)[0]
    expect(today).toContain('var(--accent)')
    expect(today).not.toContain('var(--warn)')
  })

  // ⑥ 的**旧版**钉的是「被点的那一格独占整行（`grid-column: 1 / -1`）」—— 那正是
  // 用户截图里 7 格一行错位的成因：那一格离开原列、后面的日子被挤到下一行开头。
  // 现在控件搬到了网格下面的编辑带，这条改成钉「格子留在原列 + 带子在网格后面」。
  it('⑥ 编辑态那一格留在原来的列位上（控件在网格下面的编辑带里）', async () => {
    const { wrapper } = await mountSheet()
    const card = wrapper.findAll('.aCard')[0]
    await card.findAll('.aDay')[0].find('button.aDayBtn').trigger('click')

    const editing = card.findAll('.aDay')[0]
    expect(editing.classes()).toContain('is-editing')
    // 被点之后仍然看得出在改哪一天（日期数字照旧在格子里）。
    expect(editing.find('.aDayNum').text()).toBe('1')
    // 别的格子不进这一态（一次只改一天）。
    expect(card.findAll('.aDay')[1].classes()).not.toContain('is-editing')
    // 控件搬出格子：那条把格子拔出网格的规则不再回来。
    expect(view).not.toMatch(/\.aDay\.is-editing\s*\{[^}]*grid-column/)
    expect(view).not.toMatch(/\.aDay\.is-editing\s*\{[^}]*flex-direction:\s*row/)
    // 编辑带挂在网格**后面**（DOM 上紧挨着 `.aDays`）。
    const band = card.find('.aEditBar')
    expect(band.exists()).toBe(true)
    expect(band.element.previousElementSibling).toBe(card.find('.aDays').element)
  })

  it('⑧ 卡片圆角走语义令牌 `--r-2`；长名字省略号 + `min-width: 0`', () => {
    expect(view).toMatch(/\.aCard\s*\{[^}]*border-radius:\s*var\(--r-2\)/)
    const name = view.match(/^\.aName\s*\{[^}]*\}/m)[0]
    expect(name).toContain('text-overflow: ellipsis')
    expect(name).toContain('min-width: 0')
  })

  it('① 顶部那一行会换行：标题块给基准宽、按钮组自己也能换行', () => {
    expect(view).toMatch(/\.aTop\s*\{[^}]*flex-wrap:\s*wrap/)
    expect(view).toMatch(/\.aActions\s*\{[^}]*flex-wrap:\s*wrap/)
    expect(view).toMatch(/\.aIntro\s*\{[^}]*flex:\s*1 1 320px/)
  })
})
