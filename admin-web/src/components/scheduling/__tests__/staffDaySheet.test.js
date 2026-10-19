// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'

const here = dirname(fileURLToPath(import.meta.url))
const sheet = readFileSync(join(here, '../StaffDaySheet.vue'), 'utf8')
const monthView = readFileSync(join(here, '../../../views/today/TodayMonthView.vue'), 'utf8')
const todayView = readFileSync(join(here, '../../../views/today/TodayView.vue'), 'utf8')

afterEach(() => { document.body.innerHTML = '' })

// ── 那一天的样子（服务端 `/me/month` 给的那一格 + 票 13 的 `staff_count`）──
const TODAY = '2026-10-05'
const WINDOW_END = '2027-01-03'
const EMPLOYEE = { id: 7, name: '余威威' }

const DAY = {
  business_date: '2026-10-08',
  day: 8,
  is_today: false,
  scheduled: true,
  shift_id: 3,
  shift_name: '白班',
  zone_name: '案板',
  leave: false,
  staff_count: 3,
  text: '白班',
  tone: 'shift',
}

const ROSTER = {
  business_date: '2026-10-08',
  today: TODAY,
  groups: [
    {
      shift_id: 3,
      shift_name: '白班',
      count: 2,
      people: [
        { id: 7, name: '余威威', zone: '案板' },
        { id: 8, name: '王五', zone: '明档' },
      ],
    },
  ],
  total: 3,
  off_count: 1,
  off_people: [{ id: 9, name: '赵六', leave: true }],
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => data,
  }
}

/**
 * 挂载那一天那一屏。`calls` 收下每一条真实请求（路径 + 方法 + body）——
 * 「提交上去的到底是哪一天、哪个人」是这一票最要紧的判据，只有真发一次才算数。
 */
async function mountSheet({
  day = DAY,
  requests = [],
  incoming = [],
  colleagues = [],
  initialAct = '',
  windowEnd = WINDOW_END,
  today = TODAY,
  calls = [],
} = {}) {
  const fetchMock = vi.fn(async (url, init = {}) => {
    const raw = String(url)
    const path = raw.split('?')[0]
    const method = (init.method || 'GET').toUpperCase()
    calls.push({
      path,
      raw,
      method,
      body: init.body ? JSON.parse(init.body) : null,
    })
    if (method !== 'GET') return jsonResponse({ ok: true, request: { id: 1 } })
    if (path === '/api/scheduling/me/day') return jsonResponse(ROSTER)
    if (path === '/api/scheduling/me/requests') {
      return jsonResponse({ requests, incoming, max_request_note: 50 })
    }
    if (path === '/api/scheduling/me/colleagues') return jsonResponse({ colleagues })
    return jsonResponse({ detail: 'not found' }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)

  const { default: StaffDaySheet } = await import('../StaffDaySheet.vue')
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/workbench/staff/month', component: { template: '<div />' } }],
  })
  await router.push('/workbench/staff/month')
  await router.isReady()

  const wrapper = mount(StaffDaySheet, {
    props: { day, employee: EMPLOYEE, today, windowEnd, initialAct },
    global: { plugins: [router] },
    attachTo: document.body,
  })
  await flushPromises()
  await flushPromises()
  return { wrapper, calls }
}

/** 按钮文案定位：这一屏的按钮不靠位置认（多一个少一个都不该让判据漂）。 */
function buttonByText(wrapper, text) {
  return wrapper.findAll('button').find((btn) => btn.text().includes(text))
}

/** 确认框里那颗「确认」：`ConfirmDialog` 是页内最后一个 `.modal-box`，footer 里第二颗。
 *
 *  不能再用文案找 —— 「撤回」与「拒绝」这两颗在主屏上各有一颗同名的（那才是入口），
 *  拿文案找会一直点在入口那颗上，确认框永远点不到（这一条曾经假绿过）。 */
function confirmButton(wrapper) {
  const boxes = wrapper.findAll('.modal-box')
  const footer = boxes[boxes.length - 1].find('.modal-footer')
  return footer.findAll('button')[1]
}

describe('某一天的操作单：点开一天就能把那天的事办掉（票 15）', () => {
  it('一屏里既看得到那天的名单，也看得到那一天能办的两件事', async () => {
    const { wrapper } = await mountSheet()
    // 名单（票 13 那一份照旧）：谁在班、在哪个区、谁休谁请假。
    expect(wrapper.text()).toContain('我这天')
    expect(wrapper.text()).toContain('白班')
    expect(wrapper.findAll('.rList li').map((li) => li.text())).toContain('王五明档')
    expect(wrapper.text()).toContain('赵六请假')
    // 两个动作：日期就是这一天，不再让员工自己选一遍。
    expect(wrapper.text()).toContain('请这天的假')
    expect(wrapper.text()).toContain('换这天的班')
  })

  it('点「请这天的假」：起始日已经填好这一天，提交的就是这一天', async () => {
    const { wrapper, calls } = await mountSheet()
    await buttonByText(wrapper, '请这天的假').trigger('click')
    await flushPromises()
    // 表单里那一格显示的就是这一天（员工不用再拨一次日期）。
    expect(wrapper.get('#day-leave-start').element.value).toBe('2026-10-08')
    // 后一格空着 = 单日：请求里就少一个字段（服务端把 None 与空串都当单日）。
    await buttonByText(wrapper, '提交').trigger('click')
    await flushPromises()
    const posted = calls.find((c) => c.path === '/api/scheduling/me/requests' && c.method === 'POST')
    expect(posted.body).toEqual({ start_date: '2026-10-08', end_date: null, note: null })
    // 办完回到那一天那一屏，并给一句回执。
    expect(wrapper.text()).toContain('请假提上去了，等店长批')
    expect(wrapper.find('#day-leave-start').exists()).toBe(false)
  })

  it('点「换这天的班」：先给那天在班的人（去掉我自己），点一个人才有提交', async () => {
    const { wrapper, calls } = await mountSheet()
    await buttonByText(wrapper, '换这天的班').trigger('click')
    await flushPromises()
    const people = wrapper.findAll('.ds-people .ds-person')
    // 那天在班的是两个人：我（余威威）不在这份名单里 —— 换班没有跟自己换的。
    expect(people.map((p) => p.text())).toHaveLength(1)
    expect(people[0].text()).toContain('王五')
    expect(people[0].text()).toContain('白班 · 明档')
    // 没点人之前不能提交（提交的是「跟谁换」）。
    expect(buttonByText(wrapper, '就跟他换').attributes('disabled')).toBeDefined()
    await people[0].trigger('click')
    await flushPromises()
    // 选中之后说清「批了会怎样」，再提交 —— 王五那天也是白班，所以批了换的是岗位。
    expect(wrapper.text()).toContain('你俩那天都是白班：批了换的是岗位（工作区）')
    await buttonByText(wrapper, '就跟他换').trigger('click')
    await flushPromises()
    const posted = calls.find((c) => c.path === '/api/scheduling/me/swaps' && c.method === 'POST')
    expect(posted.body).toEqual({
      peer_employee_id: 8,
      business_date: '2026-10-08',
      note: null,
    })
    expect(wrapper.text()).toContain('换班提上去了，等 王五 同意')
  })

  it('从今天页跳过来（?act=leave）直接落在那一张表上', async () => {
    // 今天页那两颗按钮带 `?date=&act=`；月历把 `act` 原样传进这一屏（见下面的落点断言）。
    const { wrapper } = await mountSheet({ initialAct: 'leave' })
    expect(wrapper.find('#day-leave-start').exists()).toBe(true)
    expect(wrapper.text()).toContain('请这天的假')
  })

  it('过去的日子两个动作都是灰的，并说清为什么', async () => {
    const past = { ...DAY, business_date: '2026-10-01', day: 1 }
    const { wrapper } = await mountSheet({ day: past })
    expect(buttonByText(wrapper, '请这天的假').attributes('disabled')).toBeDefined()
    expect(buttonByText(wrapper, '换这天的班').attributes('disabled')).toBeDefined()
    // 两个理由一样就只说一遍（说两遍同一句话像是两条限制）。
    expect(wrapper.text()).toContain('这天已经过去了')
    expect(wrapper.text()).not.toContain('请假：这天已经过去了')
    // 那天的名单照旧看得到：办不了事不等于看不见那天谁在班。
    expect(wrapper.text()).toContain('王五')
  })

  it('那天休：请假照常能提，换班也开着（休的那天换班是合法的换法）', async () => {
    const rest = { ...DAY, shift_id: null, shift_name: null, text: '休', tone: 'rest' }
    const { wrapper } = await mountSheet({ day: rest })
    expect(buttonByText(wrapper, '请这天的假').attributes('disabled')).toBeUndefined()
    expect(buttonByText(wrapper, '换这天的班').attributes('disabled')).toBeUndefined()
  })

  it('那天还没排到你：换班灰掉，理由跟「休」分开说', async () => {
    const none = { ...DAY, scheduled: false, shift_id: null, shift_name: null, text: '', tone: 'none' }
    const { wrapper } = await mountSheet({ day: none })
    expect(buttonByText(wrapper, '换这天的班').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('这天还没排到你，没法换')
    // 只有一件事不可用时要指名是哪件事（请假依旧能提：服务端只要求不是过去、在窗口里）。
    expect(wrapper.text()).toContain('换班：这天还没排到你，没法换')
    expect(buttonByText(wrapper, '请这天的假').attributes('disabled')).toBeUndefined()
  })
})

describe('别人找我换这天的班：在这一屏就能回话（票 09 的验收 2）', () => {
  const INCOMING = [{
    id: 31,
    employee_id: 8,
    employee_name: '王五',
    business_date: '2026-10-08',
    their_scheduled: true,
    their_shift_name: '白班',
    my_scheduled: true,
    my_shift_name: '白班',
  }]

  it('那天有人问我：先说清谁的哪天，同意就发 accept', async () => {
    const { wrapper, calls } = await mountSheet({ incoming: INCOMING })
    expect(wrapper.get('.ds-ask').text()).toContain('王五 想跟你换 10/8 周四')
    await buttonByText(wrapper, '同意并转店长').trigger('click')
    await flushPromises()
    const posted = calls.find((c) => c.path.endsWith('/accept'))
    expect(posted.method).toBe('POST')
    expect(posted.path).toBe('/api/scheduling/me/swaps/31/accept')
    // 同意不弹确认框（后面还有店长那道闸），但要说清接下来等谁。
    expect(wrapper.text()).toContain('你同意了，接下来等店长批')
  })

  it('拒绝先过确认框（服务端只往前走，手机上一误触没有回头路）', async () => {
    const { wrapper, calls } = await mountSheet({ incoming: INCOMING })
    await buttonByText(wrapper, '拒绝').trigger('click')
    await flushPromises()
    expect(calls.some((c) => c.path.endsWith('/reject'))).toBe(false)
    expect(wrapper.text()).toContain('这件事就结束了')
    await confirmButton(wrapper).trigger('click')
    await flushPromises()
    expect(calls.some((c) => c.path === '/api/scheduling/me/swaps/31/reject')).toBe(true)
  })

  it('别的天的事不串到这一格上', async () => {
    const { wrapper } = await mountSheet({
      incoming: [{ ...INCOMING[0], business_date: '2026-10-09' }],
    })
    expect(wrapper.find('.ds-ask').exists()).toBe(false)
  })
})

describe('我在这天的申请：看得见、撤得回', () => {
  const MINE = [{
    id: 21,
    kind: 'leave',
    status: 'pending_manager',
    start_date: '2026-10-08',
    end_date: null,
    note: '家里有事',
  }]

  it('那天有一条还在等店长批：状态写出来，撤回要过确认框', async () => {
    const { wrapper, calls } = await mountSheet({ requests: MINE })
    expect(wrapper.get('.ds-mine').text()).toContain('请假 · 10/8 周四 · 等店长批')
    await wrapper.get('.ds-mine .ds-mini').trigger('click')
    await flushPromises()
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
    expect(wrapper.text()).toContain('撤回之后要重新提一次')
    await confirmButton(wrapper).trigger('click')
    await flushPromises()
    const deleted = calls.find((c) => c.method === 'DELETE')
    expect(deleted.path).toBe('/api/scheduling/me/requests/21')
  })

  it('批完 / 驳完的那几条只报结果，没有撤回按钮', async () => {
    const { wrapper } = await mountSheet({
      requests: [{ ...MINE[0], status: 'approved' }],
    })
    expect(wrapper.get('.ds-mine').text()).toContain('批了')
    expect(wrapper.find('.ds-mine .ds-mini').exists()).toBe(false)
  })

  it('跨天的假在区间里每一天都看得见', async () => {
    const spanning = [{ ...MINE[0], start_date: '2026-10-06', end_date: '2026-10-10' }]
    const { wrapper } = await mountSheet({ requests: spanning })
    expect(wrapper.find('.ds-mine').exists()).toBe(true)
  })
})

describe('这一屏的判据都在 util 里，页面不重写第二份', () => {
  it('能不能请、能不能换、批了会怎样、能点谁 —— 全部来自 staffDayActions', () => {
    expect(sheet).toMatch(/from '\.\.\/\.\.\/utils\/staffDayActions'/)
    for (const fn of [
      'DAY_VIEW',
      'actionsNote',
      'candidateLine',
      'dayActions',
      'incomingOnDay',
      'requestsOnDay',
      'swapCandidates',
      'swapPreview',
    ]) {
      expect(sheet).toContain(fn)
    }
    // 页面里不许再出现原始的 `scheduled` / `shift_id` 判断（判据读两遍就会漂）。
    expect(sheet).not.toMatch(/day\.scheduled/)
    // 人话文案也一样：状态与区间都走 utils/leaveRequest.js。
    expect(sheet).toMatch(/from '\.\.\/\.\.\/utils\/leaveRequest'/)
    for (const fn of ['canCancel', 'incomingLine', 'requestLine']) {
      expect(sheet).toContain(fn)
    }
  })

  it('三条接口都在员工那扇门底下，路径上没有 employee_id 可填', () => {
    expect(sheet).toMatch(/staffRequest\(\s*`\/api\/scheduling\/me\/day\?date=/)
    expect(sheet).toMatch(/staffRequest\('\/api\/scheduling\/me\/requests'\)/)
    expect(sheet).toMatch(/staffRequest\('\/api\/scheduling\/me\/colleagues'\)/)
    expect(sheet).toMatch(/staffRequest\('\/api\/scheduling\/me\/swaps'/)
    expect(sheet).toMatch(/method: 'POST'/)
    expect(sheet).toMatch(/method: 'DELETE'/)
    expect(sheet).not.toContain('/api/scheduling/inbox')
    // `peer_employee_id`（同事的 id）不算：前面多一个下划线，落不进这条正则。
    expect(sheet).not.toMatch(/(^|[^_a-zA-Z])employee_id\s*[:=]/)
    // 401 回员工登录，落点算在 `loginRedirectTarget` 一处。
    expect(sheet).toMatch(/loginRedirectTarget\(router\.currentRoute\.value\)/)
  })

  it('事由上限随响应下来，不在前端写死第二份', () => {
    expect(sheet).toMatch(/if \(data\.max_request_note\) maxNote\.value = data\.max_request_note/)
    expect(sheet).toMatch(/:maxlength="maxNote \|\| undefined"/)
  })

  it('过去的日子在控件上先挡住（min-date 取服务端营业日）', () => {
    // 手机时区可能不在东八区，判「过去」的是服务端（`past_leave` / `past_swap`）。
    expect(sheet).toMatch(/:min-date="today \|\| undefined"/)
    expect(sheet).toMatch(/:min-date="leaveStart \|\| today \|\| undefined"/)
  })

  it('弹层走主题里那套容器，并自己收掉 Esc', () => {
    expect(sheet).toMatch(/class="modal-overlay"/)
    expect(sheet).toMatch(/role="dialog"/)
    expect(sheet).toMatch(/aria-modal="true"/)
    expect(sheet).toMatch(/@click\.self="close"/)
    expect(sheet).toMatch(/event\.key === 'Escape'/)
  })
})

describe('月历页与今天页把这两件事送过来（票 15 的接线）', () => {
  it('月历点某一天弹的是这一屏，不再是只读名单', () => {
    expect(monthView).toMatch(/import StaffDaySheet from '\.\.\/\.\.\/components\/scheduling\/StaffDaySheet\.vue'/)
    expect(monthView).toMatch(/<StaffDaySheet/)
    expect(monthView).toMatch(/:initial-act="openAct"/)
    // 存日期不存那一格对象：月历重读（nudge / 翻月）之后，「我这天」那行要跟着新数据走。
    expect(monthView).toMatch(/const detailDay = computed/)
    expect(monthView).toMatch(/cells\.value\.find\(\(cell\) => cell\.business_date === detailDate\.value\)/)
    // 办完（提交 / 撤回 / 回应）让月历上的两个记号跟着变。
    expect(monthView).toMatch(/@changed="loadRequestMarks"/)
    // 名单那件事整块搬进了这一屏：月历页不再自己请求那条接口（注释里提它是文档）。
    expect(monthView).not.toMatch(/staffRequest\([^)]*me\/day/)
  })

  it('今天页那两颗按钮带着日期与动作跳到月历，不再就地弹表单', () => {
    expect(todayView).toMatch(/const ENTRIES = computed/)
    expect(todayView).toMatch(/key: 'leave', label: '请假', to: \{ \.\.\.month, query: \{ date, act: 'leave' \} \}/)
    expect(todayView).toMatch(/key: 'swap', label: '换班', to: \{ \.\.\.month, query: \{ date, act: 'swap' \} \}/)
    expect(todayView).toMatch(/router\.push\(entry\.to\)/)
    // 两套表单退场：这一页不再有请假 / 换班的输入框与提交。
    expect(todayView).not.toContain('leave-sheet-title')
    expect(todayView).not.toContain('swap-sheet-title')
    expect(todayView).not.toMatch(/leaveStart/)
    expect(todayView).not.toMatch(/swapPeer/)
    // 「我的申请」与「别人问我换班」留着：首页是落点，这两件事必须一眼看得见。
    expect(todayView).toMatch(/@click="answerSwap\(card, true\)"/)
    expect(todayView).toMatch(/@click="cancelTarget = request"/)
    expect(todayView).toContain('要重新提一次')
  })

  it('月历页支持 ?date=&act= 直达：解析完就清掉，关掉弹层不会被再开一次', () => {
    expect(monthView).toMatch(/dateFromQuery\(route\.query\)/)
    expect(monthView).toMatch(/actFromQuery\(route\.query\)/)
    expect(monthView).toMatch(/await load\(monthOfDate\(wantedDate\.value\) \|\| undefined\)/)
    expect(monthView).toMatch(/wantedDate\.value = ''/)
  })

  it('标出「有人找我换那天」：格子底下那条琥珀，跟「我的申请」的小点分开', () => {
    expect(monthView).toMatch(/incomingDates/)
    expect(monthView).toMatch(/v-if="incomingDates\.has\(cell\.business_date\)"/)
    expect(monthView).toMatch(/class="ask"/)
    /* B 案重构（2026-10-17）：这条记号的调色从旧的 --hy-amber 换成语义名 --warn
       （两套主题各答一次颜色；断言钉的是「它有一条自己的、与 asked 相同的提示色」）。 */
    expect(monthView).toMatch(/\.mD \.ask \{[\s\S]{0,220}?var\(--warn\)/)
    expect(monthView).toMatch(/class="dot asked"/)
    expect(monthView).toMatch(/\.dot\.asked \{[\s\S]{0,120}?var\(--warn\)/)
  })

  it('页脚说清点开某一天能办什么（两件事写在最前面）', () => {
    expect(monthView).toContain('点开某一天：看那天的名单，也顺手请那天的假、跟那天在班的人换班')
    expect(monthView).toContain('格子下面那条琥珀 = 有人找你换那天')
    // 票 06 起那几句口径不能被这一票挤掉。
    expect(monthView).toContain('右上角的小点')
    expect(monthView).toContain('批过的假写「请假」')
  })
})
