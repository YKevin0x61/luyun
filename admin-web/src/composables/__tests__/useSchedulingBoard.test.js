import { beforeEach, describe, expect, it, vi } from 'vitest'

// 契约（ADR 0142 决策 1 / 10）：board 只经由 `api` 读，测试统一 mock 这一层，
// 断调用路径、参数与顺序 —— 不再回源码里找字符串。
vi.mock('../../api/client', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

import { api } from '../../api/client'
import {
  PICK_HALF_REST,
  PICK_REST,
  PICK_RESTORE,
  PICK_SHIFT,
  PICK_VOID,
} from '../../utils/schedulingWeekPick'
import { useSchedulingBoard } from '../useSchedulingBoard'

function calendarPayload(overrides = {}) {
  return {
    month: '2026-10',
    today: '2026-10-15',
    days: [{ business_date: '2026-10-01' }, { business_date: '2026-10-15' }],
    shifts: [{ id: 1, name: '白班', is_active: true }],
    ...overrides,
  }
}

function dayPayload(date = '2026-10-15') {
  return { date, groups: [] }
}

/** 按 URL 回包；没登记的路由直接炸，防止测试悄悄放过一个没预期的请求。 */
function routeGet(handlers) {
  api.get.mockImplementation((path, params) => {
    const handler = handlers[path]
    if (!handler) return Promise.reject(new Error(`unexpected GET ${path}`))
    return Promise.resolve(typeof handler === 'function' ? handler(params) : handler)
  })
}

function deferred() {
  let resolve
  const promise = new Promise((done) => {
    resolve = done
  })
  return { promise, resolve }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('初始化与视角', () => {
  it('init 只落页面从 URL 读出的值，不自动发请求', () => {
    const board = useSchedulingBoard()
    board.init({ month: '2026-10', view: 'week', weekStart: '2026-10-12' })

    expect(api.get).not.toHaveBeenCalled()
    expect(board.monthValue.value).toBe('2026-10')
    expect(board.view.value).toBe('week')
    expect(board.weekStart.value).toBe('2026-10-12')
    // 首屏 loading 由页面决定何时收起：init 不动它。
    expect(board.loading.value).toBe(true)
    expect(board.calendar.value).toBeNull()
    expect(board.errorText.value).toBe('')
  })

  it('setView 只改视角，不碰 URL、不发请求', () => {
    const board = useSchedulingBoard()
    board.setView('roster')
    expect(board.view.value).toBe('roster')
    expect(api.get).not.toHaveBeenCalled()
  })
})

describe('loadMonth（原 loadCalendar）', () => {
  it('GET /calendar 带月份，写月历 / 班次列 / 选中那天，再按选中那天读当天', async () => {
    const board = useSchedulingBoard()
    board.init({ month: '2026-10' })
    routeGet({
      '/api/scheduling/calendar': calendarPayload(),
      '/api/scheduling/day': dayPayload(),
    })

    await board.loadMonth('2026-10')

    expect(api.get.mock.calls).toEqual([
      ['/api/scheduling/calendar', { month: '2026-10' }],
      ['/api/scheduling/day', { date: '2026-10-15' }],
    ])
    expect(board.calendar.value.month).toBe('2026-10')
    expect(board.shifts.value).toEqual([{ id: 1, name: '白班', is_active: true }])
    expect(board.monthValue.value).toBe('2026-10')
    expect(board.selectedDate.value).toBe('2026-10-15')
    expect(board.dayDetail.value).toEqual(dayPayload())
    expect(board.loading.value).toBe(false)
    expect(board.errorText.value).toBe('')
  })

  it('今天不在这个月就选 1 号；月份以服务端回的为准', async () => {
    const board = useSchedulingBoard()
    routeGet({
      '/api/scheduling/calendar': calendarPayload({
        month: '2026-09',
        today: '2026-10-15',
        days: [{ business_date: '2026-09-01' }],
      }),
      '/api/scheduling/day': dayPayload('2026-09-01'),
    })

    await board.loadMonth('2026-09')

    expect(board.selectedDate.value).toBe('2026-09-01')
    expect(board.monthValue.value).toBe('2026-09')
  })

  it('慢网下只认最后一次请求：先发后到的旧月份不能覆盖后点的月份', async () => {
    const board = useSchedulingBoard()
    const first = deferred()
    const second = deferred()
    api.get.mockImplementation((path, params) => {
      if (path === '/api/scheduling/day') return Promise.resolve(dayPayload())
      return params.month === '2026-09' ? first.promise : second.promise
    })

    const slow = board.loadMonth('2026-09')
    const fresh = board.loadMonth('2026-10')
    second.resolve(calendarPayload({ month: '2026-10' }))
    await fresh
    first.resolve(calendarPayload({ month: '2026-09', today: '2026-09-02' }))
    await slow

    expect(board.calendar.value.month).toBe('2026-10')
    expect(board.monthValue.value).toBe('2026-10')
    expect(board.loading.value).toBe(false)
    // 旧响应在写状态前就被序号挡掉，不会再去读它选中的那天。
    expect(api.get.mock.calls.filter(([path]) => path === '/api/scheduling/day')).toHaveLength(1)
  })

  it('读失败落进 errorText 并收起 loading；silent 重读不翻 loading', async () => {
    const board = useSchedulingBoard()
    routeGet({ '/api/scheduling/calendar': calendarPayload(), '/api/scheduling/day': dayPayload() })
    await board.loadMonth('2026-10')
    expect(board.loading.value).toBe(false)

    api.get.mockImplementation(() => Promise.reject(new Error()))
    await board.loadMonth('2026-11')
    expect(board.errorText.value).toBe('排班月历加载失败')
    expect(board.loading.value).toBe(false)

    await board.loadMonth('2026-10', true)
    expect(board.loading.value).toBe(false)
  })
})

describe('loadDay', () => {
  it('空日期只清空当天，不发请求', async () => {
    const board = useSchedulingBoard()
    board.dayDetail.value = dayPayload()

    await board.loadDay('')

    expect(board.dayDetail.value).toBeNull()
    expect(api.get).not.toHaveBeenCalled()
  })

  it('换一天先清空上一天的人，再写新的一天；失败留错误文案', async () => {
    const board = useSchedulingBoard()
    const pending = deferred()
    api.get.mockReturnValue(pending.promise)

    const task = board.loadDay('2026-10-16')
    expect(board.selectedDate.value).toBe('2026-10-16')
    expect(board.dayDetail.value).toBeNull()
    pending.resolve(dayPayload('2026-10-16'))
    await task
    expect(board.dayDetail.value.date).toBe('2026-10-16')
    expect(api.get).toHaveBeenCalledWith('/api/scheduling/day', { date: '2026-10-16' })

    api.get.mockRejectedValueOnce(new Error('这一天读不出来'))
    await board.loadDay('2026-10-17')
    expect(board.dayDetail.value).toBeNull()
    expect(board.errorText.value).toBe('这一天读不出来')
  })
})

describe('loadRoster', () => {
  it('读名单 / 工作区 / 周期上限，班次列取并集而不是被名单那份替换', async () => {
    const board = useSchedulingBoard()
    routeGet({
      '/api/scheduling/calendar': calendarPayload({
        shifts: [
          { id: 1, name: '白班', is_active: true },
          { id: 2, name: '旧夜班', is_active: false },
        ],
      }),
      '/api/scheduling/day': dayPayload(),
    })
    await board.loadMonth('2026-10')

    api.get.mockResolvedValueOnce({
      employees: [{ id: 7, name: '阿明' }],
      shifts: [{ id: 2, name: '旧夜班（改名）', is_active: false }, { id: 3, name: '早班', is_active: true }],
      zones: [{ id: 1, name: '前厅' }],
      max_cycle_days: 60,
    })
    await board.loadRoster()

    expect(board.roster.value).toEqual([{ id: 7, name: '阿明' }])
    expect(board.zones.value).toEqual([{ id: 1, name: '前厅' }])
    expect(board.maxCycleDays.value).toBe(60)
    // 月历里那两条都在（1 保留、2 用月历那份的名字），名单新增的 3 追加在后面。
    expect(board.shifts.value.map((shift) => shift.id)).toEqual([1, 2, 3])
    expect(board.shifts.value[1].name).toBe('旧夜班')
  })

  it('失败落 errorText', async () => {
    const board = useSchedulingBoard()
    api.get.mockRejectedValueOnce(new Error())
    await board.loadRoster()
    expect(board.errorText.value).toBe('名单读不出来')
  })
})

describe('loadPendingCount', () => {
  it('只数在职、已批准、没规则或规则坏了的人', async () => {
    const board = useSchedulingBoard()
    api.get.mockResolvedValueOnce({
      employees: [
        { id: 1, approved: true, rule: null },
        { id: 2, approved: true, rule: { invalid: true } },
        { id: 3, approved: true, rule: { cycle: [1] } },
        { id: 4, approved: false, rule: null },
        { id: 5, approved: true, resigned: true, rule: null },
      ],
    })

    await board.loadPendingCount()

    expect(api.get).toHaveBeenCalledWith('/api/scheduling/roster')
    expect(board.pendingCount.value).toBe(2)
  })

  it('读不出来置 null（不是 0）', async () => {
    const board = useSchedulingBoard()
    api.get.mockRejectedValueOnce(new Error())
    await board.loadPendingCount()
    expect(board.pendingCount.value).toBeNull()
  })

  it('只碰计数，不动班次列（月历那份不被名单那份挤掉）', async () => {
    const board = useSchedulingBoard()
    board.shifts.value = [
      { id: 1, name: '白班', is_active: true },
      { id: 2, name: '旧夜班', is_active: false },
    ]
    api.get.mockResolvedValueOnce({
      employees: [{ id: 1, approved: true, rule: null }],
    })

    await board.loadPendingCount()

    expect(board.pendingCount.value).toBe(1)
    expect(board.shifts.value.map((shift) => shift.id)).toEqual([1, 2])
  })
})

describe('loadPendingMarks', () => {
  it('把 /inbox 的每条申请按天摊开计数，并留一份原样请求', async () => {
    const board = useSchedulingBoard()
    const requests = [
      { kind: 'leave', start_date: '2026-10-01', end_date: '2026-10-03' },
      { kind: 'swap', start_date: '2026-10-03', end_date: null },
    ]
    api.get.mockResolvedValueOnce({ requests })

    await board.loadPendingMarks()

    expect(api.get).toHaveBeenCalledWith('/api/scheduling/inbox')
    expect(board.pendingMarks.value).toEqual({
      '2026-10-01': 1,
      '2026-10-02': 1,
      '2026-10-03': 2,
    })
    expect(board.inboxRequests.value).toEqual(requests)
  })

  it('读不出来就整块清空，不标错数字', async () => {
    const board = useSchedulingBoard()
    board.pendingMarks.value = { '2026-10-01': 1 }
    board.inboxRequests.value = [{ id: 1 }]
    api.get.mockRejectedValueOnce(new Error())

    await board.loadPendingMarks()

    expect(board.pendingMarks.value).toEqual({})
    expect(board.inboxRequests.value).toEqual([])
  })
})

describe('loadWeek', () => {
  it('GET /week 带 start（空串也照发），写周与周起点，班次并集', async () => {
    const board = useSchedulingBoard()
    board.shifts.value = [{ id: 1, name: '白班', is_active: true }]

    api.get.mockResolvedValueOnce({
      start: '2026-10-12',
      end: '2026-10-18',
      today: '2026-10-15',
      shifts: [{ id: 2, name: '旧夜班', is_active: false }],
      days: [],
      employees: [],
    })

    await board.loadWeek('')

    expect(api.get).toHaveBeenLastCalledWith('/api/scheduling/week', { start: '' })
    expect(board.weekStart.value).toBe('2026-10-12')
    expect(board.week.value.start).toBe('2026-10-12')
    expect(board.shifts.value.map((shift) => shift.id)).toEqual([1, 2])
  })

  it('失败落 weekError（周表自己的错误位）', async () => {
    const board = useSchedulingBoard()
    api.get.mockRejectedValueOnce(new Error())
    await board.loadWeek('2026-10-12')
    expect(board.weekError.value).toBe('周表读不出来')
  })
})

describe('loadLeaveBalances', () => {
  it('只收数字存假；不带月份参数，月份用于 title', async () => {
    const board = useSchedulingBoard()
    api.get.mockResolvedValueOnce({
      month: '2026-10',
      employees: [
        { id: 1, leave: { balance: 12 } },
        { id: 2, leave: { balance: null } },
        { id: 3, leave: null },
      ],
    })

    await board.loadLeaveBalances()

    expect(api.get).toHaveBeenCalledWith('/api/attendance/sheet')
    expect(board.leaveBalance.value).toEqual({ 1: 12 })
    expect(board.leaveMonth.value).toBe('2026-10')
  })

  it('静默失败：清空、不抛、不写 weekError', async () => {
    const board = useSchedulingBoard()
    board.leaveBalance.value = { 1: 3 }
    board.leaveMonth.value = '2026-09'
    api.get.mockRejectedValueOnce(new Error())

    await expect(board.loadLeaveBalances()).resolves.toBeUndefined()

    expect(board.leaveBalance.value).toEqual({})
    expect(board.leaveMonth.value).toBe('')
    expect(board.weekError.value).toBe('')
  })
})

describe('刷新 helper', () => {
  it('refreshNotes 同时读人数与待批', async () => {
    const board = useSchedulingBoard()
    routeGet({
      '/api/scheduling/roster': { employees: [] },
      '/api/scheduling/inbox': { requests: [] },
    })

    await board.refreshNotes()

    expect(api.get.mock.calls.map(([path]) => path)).toEqual([
      '/api/scheduling/roster',
      '/api/scheduling/inbox',
    ])
  })

  it('refreshMonthCounts 只重读月历并保住已有班次列；没月份不发请求', async () => {
    const board = useSchedulingBoard()
    await board.refreshMonthCounts()
    expect(api.get).not.toHaveBeenCalled()

    board.monthValue.value = '2026-10'
    board.shifts.value = [{ id: 1, name: '白班', is_active: true }]
    api.get.mockResolvedValueOnce(calendarPayload({ shifts: [] }))
    await board.refreshMonthCounts()

    expect(api.get).toHaveBeenCalledWith('/api/scheduling/calendar', { month: '2026-10' })
    expect(board.calendar.value.month).toBe('2026-10')
    expect(board.shifts.value.map((shift) => shift.id)).toEqual([1])
  })

  it('refreshAfterWrite 先重读月历，再把刚改的那天读回来', async () => {
    const board = useSchedulingBoard()
    board.init({ month: '2026-10' })
    routeGet({
      '/api/scheduling/calendar': calendarPayload(),
      '/api/scheduling/day': (params) => dayPayload(params.date),
    })

    await board.refreshAfterWrite('2026-10-20')

    expect(api.get.mock.calls.map(([path, params]) => [path, params])).toEqual([
      ['/api/scheduling/calendar', { month: '2026-10' }],
      ['/api/scheduling/day', { date: '2026-10-15' }],
      ['/api/scheduling/day', { date: '2026-10-20' }],
    ])
  })

  it('refreshVisible 静默重读月历 + 待批，周表视角再读那 7 天', async () => {
    const board = useSchedulingBoard()
    const calendar = calendarPayload()
    routeGet({
      '/api/scheduling/calendar': calendar,
      '/api/scheduling/day': dayPayload(),
      '/api/scheduling/roster': { employees: [] },
      '/api/scheduling/inbox': { requests: [] },
      '/api/scheduling/week': { start: '2026-10-12', shifts: [], days: [], employees: [] },
    })
    board.init({ month: '2026-10', view: 'week', weekStart: '2026-10-12' })
    // 先正常读一次，把 loading 收起来。
    await board.loadMonth('2026-10')
    expect(board.loading.value).toBe(false)
    // 只数 nudge 那一轮：首屏那次已经消费掉。
    api.get.mockClear()

    await board.refreshVisible()

    expect(api.get.mock.calls.map(([path]) => path)).toEqual([
      '/api/scheduling/calendar',
      '/api/scheduling/day',
      '/api/scheduling/roster',
      '/api/scheduling/inbox',
      '/api/scheduling/week',
    ])
    // 静默：不翻 loading。
    expect(board.loading.value).toBe(false)
    expect(board.weekStart.value).toBe('2026-10-12')
  })

  it('refreshVisible 在月历 / 名单视角不读周表', async () => {
    const board = useSchedulingBoard()
    routeGet({
      '/api/scheduling/calendar': calendarPayload(),
      '/api/scheduling/day': dayPayload(),
      '/api/scheduling/roster': { employees: [] },
      '/api/scheduling/inbox': { requests: [] },
    })
    board.init({ month: '2026-10', view: 'roster' })

    await board.refreshVisible()

    expect(api.get.mock.calls.some(([path]) => path === '/api/scheduling/week')).toBe(false)
  })
})

// ── 写口（ADR 0142 第二步）─────────────────────────────────────────────────
// 请求体 / 三道闸 / 回执 / 撤销计划原先写在页面里，靠源码正则钉；现在收进 board，
// 这里按 mock 断**行为**：发不发、发什么、回执说什么、重读几次。

const EMP7 = { id: 7, name: '张三' }
const EMP8 = { id: 8, name: '李四' }
const EMP9 = { id: 9, name: '王五' }

function dayOf(date) {
  return { business_date: date, weekday: 0 }
}

function cellOf(overrides = {}) {
  return {
    scheduled: true,
    shift_id: 2,
    shift_name: '白班',
    zone_id: 3,
    overridden: false,
    half_rest: false,
    leave: false,
    ...overrides,
  }
}

/** 周表：今天 10-14；7 号今天有班（没覆盖）、8 号今天是批过的假、9 号是手动改过的班。 */
function weekPayload(overrides = {}) {
  return {
    start: '2026-10-12',
    today: '2026-10-14',
    window_end: '2026-10-31',
    days: ['2026-10-12', '2026-10-13', '2026-10-14'].map((date) => ({
      business_date: date,
      weekday: 0,
      in_window: true,
    })),
    employees: [
      { id: 7, name: '张三', cells: { '2026-10-13': cellOf(), '2026-10-14': cellOf() } },
      {
        id: 8,
        name: '李四',
        cells: { '2026-10-14': { scheduled: false, shift_id: null, overridden: true, leave: true } },
      },
      { id: 9, name: '王五', cells: { '2026-10-14': cellOf({ overridden: true }) } },
    ],
    shifts: [],
    ...overrides,
  }
}

function makeBoard(handlers = {}, week = weekPayload()) {
  const board = useSchedulingBoard()
  board.init({ month: '2026-10', view: 'week', weekStart: '2026-10-12' })
  routeGet({
    '/api/scheduling/week': week,
    '/api/scheduling/calendar': calendarPayload(),
    ...handlers,
  })
  return board
}

function posts() {
  return api.post.mock.calls.map(([path, body]) => [path, body])
}

function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

describe('落笔队列：连点不丢格', () => {
  it('上一笔还在写时进队列，同一格重复点只留最后一次', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    const first = deferred()
    api.post.mockReturnValueOnce(first.promise)

    const pending = board.paintRegion(PICK_REST, {}, [{ employeeId: 7, date: '2026-10-14' }])
    expect(board.pickBusy.value).toBe(true)

    await board.paintCell(EMP7, dayOf('2026-10-14'), PICK_SHIFT, { shiftId: 2 })
    await board.paintCell(EMP7, dayOf('2026-10-14'), PICK_REST, {})
    expect(board.pendingPaints.value).toHaveLength(1)
    expect(board.pendingPaints.value[0]).toMatchObject({ action: PICK_REST, key: '7|2026-10-14' })

    first.resolve({ applied: 1 })
    await pending
    await flush()
    await flush()

    expect(board.pendingPaints.value).toHaveLength(0)
    expect(board.pickBusy.value).toBe(false)
  })

  it('第一笔写完后按点击顺序补写排队的格子', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    const first = deferred()
    api.post.mockReturnValueOnce(first.promise).mockResolvedValue({ applied: 1 })

    const pending = board.paintRegion(PICK_REST, {}, [{ employeeId: 7, date: '2026-10-14' }])
    await board.paintCell(EMP9, dayOf('2026-10-14'), PICK_SHIFT, { shiftId: 2 })
    await board.paintCell(EMP7, dayOf('2026-10-14'), PICK_VOID, {})

    first.resolve({ applied: 1 })
    await pending
    await flush()
    await flush()
    await flush()

    expect(board.pendingPaints.value).toHaveLength(0)
    expect(posts().map(([, body]) => [body.action, body.items[0].employee_id])).toEqual([
      ['rest', 7],
      ['shift', 9],
      ['void', 7],
    ])
  })

  it('队列满了 20 格只提示、不再排', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    board.pickBusy.value = true
    for (let index = 0; index < 20; index += 1) {
      await board.paintCell({ id: 100 + index, name: `人${index}` }, dayOf('2026-10-14'), PICK_REST, {})
    }
    expect(board.pendingPaints.value).toHaveLength(20)

    await board.paintCell({ id: 200, name: '多出来的' }, dayOf('2026-10-14'), PICK_REST, {})

    expect(board.pendingPaints.value).toHaveLength(20)
    expect(board.notice.value).toMatchObject({ text: '点太快了，等这一笔写完', warn: true })
  })

  it('第一笔写完后按点击顺序 drain；碰到过去日确认就停下', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    const first = deferred()
    api.post.mockReturnValueOnce(first.promise).mockResolvedValue({ applied: 1 })

    const pending = board.paintRegion(PICK_REST, {}, [{ employeeId: 7, date: '2026-10-14' }])
    await board.paintCell(EMP7, dayOf('2026-10-13'), PICK_REST, {})
    await board.paintCell(EMP7, dayOf('2026-10-14'), PICK_REST, {})
    expect(board.pendingPaints.value).toHaveLength(2)

    first.resolve({ applied: 1 })
    await pending
    await flush()
    await flush()

    // 10-13 是过去的日子：drain 到它就弹确认框，余下那格留在队列里，等确认后再写。
    expect(board.pastConfirm.value).toMatchObject({
      action: PICK_REST,
      dates: ['2026-10-13'],
      cells: [{ employeeId: 7, date: '2026-10-13' }],
    })
    expect(board.pendingPaints.value).toHaveLength(1)
    expect(board.pendingPaints.value[0].day.business_date).toBe('2026-10-14')
    expect(posts()).toHaveLength(1)
  })
})

describe('落笔前的三道闸与过去日确认', () => {
  it('空选区不发请求', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    await board.paintRegion(PICK_REST, {}, [])
    expect(api.post).not.toHaveBeenCalled()
    expect(board.notice.value).toBeNull()
  })

  it('超过 200 格只提示，不发请求', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    const cells = Array.from({ length: 201 }, (_, index) => ({
      employeeId: 1000 + index,
      date: '2026-10-14',
    }))
    await board.paintRegion(PICK_REST, {}, cells)
    expect(api.post).not.toHaveBeenCalled()
    expect(board.notice.value).toMatchObject({ warn: true })
    expect(board.notice.value.text).toContain('一次最多 200 格')
  })

  it('没有班次可写的「刷」只提示', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    await board.paintRegion(PICK_SHIFT, {}, [{ employeeId: 7, date: '2026-10-14' }])
    expect(api.post).not.toHaveBeenCalled()
    expect(board.notice.value.text).toBe('这一笔没有班次可写：先在笔栏上选一个班次。')
  })

  it('「休半天」落在没有班次的格子：不发请求', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    await board.paintCell(EMP8, dayOf('2026-10-14'), PICK_HALF_REST, { halfRest: true })
    expect(api.post).not.toHaveBeenCalled()
    expect(board.notice.value.text).toBe('跳过：10月14日 那天没有班次，标不上休半天')
  })

  it('过去日只置确认框，确认后才发；取消清空', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')

    await board.paintRegion(PICK_REST, {}, [{ employeeId: 7, date: '2026-10-13' }])
    expect(api.post).not.toHaveBeenCalled()
    expect(board.pastConfirm.value).toMatchObject({ dates: ['2026-10-13'], voiding: false })

    await board.confirmPast()
    expect(board.pastConfirm.value).toBeNull()
    expect(posts()).toHaveLength(1)

    await board.paintRegion(PICK_VOID, {}, [{ employeeId: 7, date: '2026-10-13' }])
    expect(board.pastConfirm.value).toMatchObject({ voiding: true })
    board.cancelPast()
    expect(board.pastConfirm.value).toBeNull()
    expect(posts()).toHaveLength(1)
  })

})

describe('落笔请求体与重读：五种动作', () => {
  const cases = [
    [PICK_SHIFT, { shiftId: 2, zoneId: '3' }, { action: 'shift', shift_id: 2, zone_id: 3 }],
    [PICK_REST, {}, { action: 'rest' }],
    [PICK_VOID, {}, { action: 'void' }],
    [PICK_HALF_REST, { halfRest: true }, { action: 'half_rest', half_rest: true }],
    [PICK_RESTORE, {}, { action: 'restore' }],
  ]

  for (const [action, options, body] of cases) {
    it(`${action} 走批量端点，写完重读周表与月历`, async () => {
      const board = makeBoard()
      await board.loadWeek('2026-10-12')
      api.post.mockResolvedValue({ applied: 1 })
      api.get.mockClear()

      await board.paintRegion(action, options, [{ employeeId: 7, date: '2026-10-14' }])

      expect(posts()).toEqual([
        ['/api/scheduling/overrides/batch', {
          ...body,
          items: [{ employee_id: 7, business_date: '2026-10-14' }],
        }],
      ])
      // 一笔写完只重读一次：周表 + 月历人数。
      expect(api.get.mock.calls.map(([path]) => path)).toEqual([
        '/api/scheduling/week',
        '/api/scheduling/calendar',
      ])
      expect(board.pickBusy.value).toBe(false)
    })
  }

  it('busy 在请求期间为真，完成后复位', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    const pending = deferred()
    api.post.mockReturnValueOnce(pending.promise)

    const writing = board.paintRegion(PICK_REST, {}, [{ employeeId: 7, date: '2026-10-14' }])
    expect(board.pickBusy.value).toBe(true)
    pending.resolve({ applied: 1 })
    await writing
    expect(board.pickBusy.value).toBe(false)
  })

  it('没有乐观更新：POST 未落地前 week 不变，重读回来的新周才替换', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    const pending = deferred()
    api.post.mockReturnValueOnce(pending.promise)

    const writing = board.paintRegion(PICK_REST, {}, [{ employeeId: 7, date: '2026-10-14' }])
    expect(board.week.value.employees[0].cells['2026-10-14']).toMatchObject({ shift_id: 2 })

    const fresh = weekPayload()
    fresh.employees[0].cells['2026-10-14'] = cellOf({ shift_id: null, shift_name: '休' })
    routeGet({ '/api/scheduling/week': fresh, '/api/scheduling/calendar': calendarPayload() })
    pending.resolve({ applied: 1 })
    await writing

    expect(board.week.value.employees[0].cells['2026-10-14'].shift_id).toBeNull()
  })
})

describe('落笔回执', () => {
  it('applied / skipped / 可撤销一起说，跳过逐格点名进 details', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    api.post.mockResolvedValue({
      applied: 1,
      skipped: [{ employee_id: 8, business_date: '2026-10-14', reason: 'leave_approved' }],
    })

    await board.paintRegion(PICK_REST, {}, [
      { employeeId: 7, date: '2026-10-14' },
      { employeeId: 8, date: '2026-10-14' },
    ])

    expect(board.notice.value.text).toBe(
      '已排休 1 格 · 跳过 1 格 · 可撤销（只留最近一批、也只在这台手机上）',
    )
    expect(board.notice.value.details[0]).toBe('跳过：李四 10/14 已批的假')
    expect(board.canUndo.value).toBe(true)
  })

  it('回执按笔说话：清空 / 退回规则 / 休半天分标与取消 / 排休 / 改班', async () => {
    // 原来这条断言钉在页面源码上（`action === PICK_VOID ? '已清空' ...`）；写口搬进
    // board 之后再验一遍真正说出去的那句话，防止五种笔的动词被顺手归并成「已改班」。
    const cases = [
      [PICK_VOID, {}, '已清空 1 格'],
      [PICK_RESTORE, {}, '已退回规则 1 格'],
      [PICK_HALF_REST, { halfRest: true }, '已标休半天 1 格'],
      [PICK_HALF_REST, { halfRest: false }, '已取消休半天 1 格'],
      [PICK_REST, {}, '已排休 1 格'],
      [PICK_SHIFT, { shiftId: 2, zoneId: '3' }, '已改班 1 格'],
    ]
    for (const [action, options, text] of cases) {
      const board = makeBoard()
      await board.loadWeek('2026-10-12')
      api.post.mockResolvedValue({ applied: 1 })

      await board.paintRegion(action, options, [{ employeeId: 7, date: '2026-10-14' }])

      expect(board.notice.value.text).toContain(text)
    }
  })

  it('服务端回执里的 reminders 先说', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    api.post.mockResolvedValue({
      applied: 1,
      reminders: [{ employee_id: 7, business_date: '2026-10-14', kind: 'leave' }],
    })

    await board.paintRegion(PICK_REST, {}, [{ employeeId: 7, date: '2026-10-14' }])

    expect(board.notice.value.details).toEqual([
      '待批提醒：张三 10/14 那天有申请等着批，批了会盖掉你刚刷的班。',
    ])
  })

  it('回执没给 reminders 时退回 inboxRequests 那一份（去掉被跳过的）', async () => {
    const board = makeBoard({
      '/api/scheduling/inbox': {
        requests: [
          { employee_id: 7, start_date: '2026-10-14', end_date: '2026-10-14', kind: 'swap' },
          { employee_id: 8, start_date: '2026-10-14', end_date: '2026-10-14', kind: 'leave' },
        ],
      },
    })
    await board.loadWeek('2026-10-12')
    await board.loadPendingMarks()
    api.post.mockResolvedValue({
      applied: 1,
      skipped: [{ employee_id: 8, business_date: '2026-10-14', reason: 'leave_approved' }],
    })

    await board.paintRegion(PICK_REST, {}, [
      { employeeId: 7, date: '2026-10-14' },
      { employeeId: 8, date: '2026-10-14' },
    ])

    expect(board.notice.value.details).toContain(
      '待批提醒：张三 10/14 那天有申请等着批，批了会盖掉你刚刷的班。',
    )
  })

  it('失败时把服务端原话转成 warn 提示', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    api.post.mockRejectedValue(new Error('这一天不在可补记窗口里'))

    await board.paintRegion(PICK_REST, {}, [{ employeeId: 7, date: '2026-10-14' }])

    expect(board.notice.value).toMatchObject({ text: '这一天不在可补记窗口里', warn: true })
    expect(board.pickBusy.value).toBe(false)
  })
})

describe('撤销刚才那一笔', () => {
  it('按原值生成计划：没覆盖的删、批过的假留着、覆盖过的写回', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    api.post.mockResolvedValue({ applied: 3 })
    api.delete.mockResolvedValue({})
    api.put.mockResolvedValue({})

    await board.paintRegion(PICK_REST, {}, [
      { employeeId: 7, date: '2026-10-14' },
      { employeeId: 8, date: '2026-10-14' },
      { employeeId: 9, date: '2026-10-14' },
    ])
    expect(board.canUndo.value).toBe(true)

    await board.undo()

    expect(api.delete).toHaveBeenCalledWith('/api/scheduling/overrides/7/2026-10-14')
    expect(api.put).toHaveBeenCalledWith('/api/scheduling/overrides/9/2026-10-14', {
      shift_id: 2,
      zone_id: 3,
    })
    expect(board.notice.value.text).toBe('已撤销 2 格')
    expect(board.notice.value.details).toEqual(['有 1 格原本是批过的假，撤不回原样'])
    expect(board.canUndo.value).toBe(false)
    // 落笔与撤销各重读一次周表，月历人数也各读一次。
    const paths = api.get.mock.calls.map(([path]) => path)
    expect(paths.filter((path) => path === '/api/scheduling/week')).toHaveLength(3)
    expect(paths).toContain('/api/scheduling/calendar')
  })

  it('撤「休半天」按原值发单格 half_rest，不走班次那条路', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    api.post.mockResolvedValue({ applied: 1 })

    await board.paintRegion(PICK_HALF_REST, { halfRest: true }, [{ employeeId: 7, date: '2026-10-14' }])
    api.post.mockClear()
    await board.undo()

    expect(posts()).toEqual([
      ['/api/scheduling/overrides/batch', {
        action: 'half_rest',
        half_rest: false,
        items: [{ employee_id: 7, business_date: '2026-10-14' }],
      }],
    ])
    expect(api.put).not.toHaveBeenCalled()
    expect(api.delete).not.toHaveBeenCalled()
  })

  it('部分失败逐格点名，整条提示转 warn', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    api.post.mockResolvedValue({ applied: 2 })
    api.delete.mockRejectedValue(new Error('没权限'))

    await board.paintRegion(PICK_REST, {}, [
      { employeeId: 7, date: '2026-10-14' },
      { employeeId: 8, date: '2026-10-14' },
    ])
    await board.undo()

    expect(board.notice.value.warn).toBe(true)
    expect(board.notice.value.details[0]).toContain('没撤回来：张三 10/14')
    expect(board.notice.value.text).toBe('已撤销 0 格')
  })

  it('撤销按每批 5 格并发，回执从 0/N 走到 N/N', async () => {
    // 6 格：第一批 5 个请求同时发出，第二批等第一批回来才发第 6 个。
    const employees = [7, 8, 9, 10, 11, 12].map((id) => ({
      id,
      name: `员工${id}`,
      cells: { '2026-10-14': cellOf() },
    }))
    const board = makeBoard({}, weekPayload({ employees }))
    await board.loadWeek('2026-10-12')
    api.post.mockResolvedValue({ applied: 6 })

    await board.paintRegion(PICK_REST, {}, employees.map(({ id }) => ({ employeeId: id, date: '2026-10-14' })))
    expect(board.canUndo.value).toBe(true)

    const pending = employees.map(() => deferred())
    let opened = 0
    api.delete.mockImplementation(() => {
      const item = pending[opened]
      opened += 1
      return item.promise
    })

    const done = board.undo()
    expect(board.notice.value.text).toBe('撤销中 0/6')
    await flush()
    expect(opened).toBe(5) // 第一批 5 个并发，不是 6 个一起发

    for (let i = 0; i < 5; i += 1) pending[i].resolve({})
    await flush()
    await flush()
    expect(opened).toBe(6) // 第二批等第一批回来
    expect(board.notice.value.text).toBe('撤销中 5/6')

    pending[5].resolve({})
    await done
    expect(board.notice.value.text).toBe('已撤销 6 格')
  })

  it('被服务端跳过的格子不进计划；没得撤时 canUndo 为假', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    api.post.mockResolvedValue({
      applied: 0,
      skipped: [{ employee_id: 7, business_date: '2026-10-14', reason: 'beyond_window' }],
    })

    await board.paintRegion(PICK_REST, {}, [{ employeeId: 7, date: '2026-10-14' }])

    expect(board.canUndo.value).toBe(false)
    await board.undo()
    expect(api.delete).not.toHaveBeenCalled()
    expect(api.put).not.toHaveBeenCalled()
  })

  it('dropUndo 清掉上一批（换周时页面调）', async () => {
    const board = makeBoard()
    await board.loadWeek('2026-10-12')
    api.post.mockResolvedValue({ applied: 1 })
    await board.paintRegion(PICK_REST, {}, [{ employeeId: 7, date: '2026-10-14' }])
    expect(board.canUndo.value).toBe(true)
    board.dropUndo()
    expect(board.canUndo.value).toBe(false)
  })
})

describe('单日覆盖（当天卡里的编辑器）', () => {
  it('改成休发 is_rest；成功只返回 true，重读由页面在关框后触发', async () => {
    const board = makeBoard()
    api.put.mockResolvedValue({})
    api.get.mockClear()

    const ok = await board.saveOverride({ employeeId: 7, day: '2026-10-14', shiftId: 'rest', zoneId: '' })

    expect(ok).toBe(true)
    expect(api.put).toHaveBeenCalledWith('/api/scheduling/overrides/7/2026-10-14', { is_rest: true })
    // 旧页的顺序是「写完先关对话框，再重读」；board 不在这里抢先发 GET。
    expect(api.get).not.toHaveBeenCalled()
    expect(board.overrideBusy.value).toBe(false)
    expect(board.editError.value).toBe('')
  })

  it('普通班次发 shift_id + zone_id（空工作区 = 跟固定区）', async () => {
    const board = makeBoard({ '/api/scheduling/day': dayPayload('2026-10-14') })
    api.put.mockResolvedValue({})

    await board.saveOverride({ employeeId: 7, day: '2026-10-14', shiftId: '2', zoneId: '3' })
    expect(api.put).toHaveBeenLastCalledWith('/api/scheduling/overrides/7/2026-10-14', {
      shift_id: 2,
      zone_id: 3,
    })

    await board.saveOverride({ employeeId: 7, day: '2026-10-14', shiftId: '2', zoneId: '' })
    expect(api.put).toHaveBeenLastCalledWith('/api/scheduling/overrides/7/2026-10-14', {
      shift_id: 2,
      zone_id: null,
    })
  })

  it('PUT 失败留 editError、返回 false，且不重读', async () => {
    const board = makeBoard()
    api.put.mockRejectedValue(new Error('那天还没铺到'))
    api.get.mockClear()

    const ok = await board.saveOverride({ employeeId: 7, day: '2026-10-14', shiftId: '2', zoneId: '' })

    expect(ok).toBe(false)
    expect(board.editError.value).toBe('那天还没铺到')
    expect(api.get).not.toHaveBeenCalled()
    expect(board.overrideBusy.value).toBe(false)
  })

  it('DELETE 撤销覆盖同样只返回结果，失败留原话', async () => {
    const board = makeBoard()
    api.delete.mockResolvedValue({})
    api.get.mockClear()

    expect(await board.deleteOverride({ employeeId: 7, day: '2026-10-14' })).toBe(true)
    expect(api.delete).toHaveBeenCalledWith('/api/scheduling/overrides/7/2026-10-14')
    expect(api.get).not.toHaveBeenCalled()

    api.delete.mockRejectedValue(new Error('超出可补记窗口'))
    expect(await board.deleteOverride({ employeeId: 7, day: '2026-10-14' })).toBe(false)
    expect(board.editError.value).toBe('超出可补记窗口')
    expect(api.get).not.toHaveBeenCalled()
  })

  it('错误信息为空时给一句兜底', async () => {
    const board = makeBoard()
    api.put.mockRejectedValue({})
    await board.saveOverride({ employeeId: 7, day: '2026-10-14', shiftId: '2', zoneId: '' })
    expect(board.editError.value).toBe('这一天没改上')

    api.delete.mockRejectedValue({})
    await board.deleteOverride({ employeeId: 7, day: '2026-10-14' })
    expect(board.editError.value).toBe('撤销没成功')
  })
})

describe('提示槽', () => {
  it('6 秒后自己消失；dispose 清掉定时器', async () => {
    vi.useFakeTimers()
    try {
      const board = makeBoard()
      board.sayNotice('已排休 1 格')
      expect(board.notice.value).toMatchObject({ text: '已排休 1 格', details: [], warn: false })
      expect(board.noticeOpen.value).toBe(false)

      vi.advanceTimersByTime(6000)
      expect(board.notice.value).toBeNull()

      board.sayNotice('新的一条')
      board.dispose()
      vi.advanceTimersByTime(6000)
      expect(board.notice.value.text).toBe('新的一条')
    } finally {
      vi.useRealTimers()
    }
  })
})
