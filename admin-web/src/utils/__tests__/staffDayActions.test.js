import { describe, expect, it } from 'vitest'
import {
  actFromQuery,
  candidateLine,
  dateFromQuery,
  dayActions,
  incomingOnDay,
  leaveAction,
  monthOfDate,
  requestsOnDay,
  swapAction,
  swapCandidates,
  swapPreview,
} from '../staffDayActions'

// 服务端的营业日与展开窗口末日：所有「能不能做」的判据都拿它们比。
const CTX = { today: '2026-10-05', windowEnd: '2027-01-03' }

const day = (business_date, extra = {}) => ({
  business_date,
  scheduled: true,
  shift_id: 3,
  shift_name: '白班',
  leave: false,
  ...extra,
})

describe('那一天的请假：能不能提、不能提时说什么', () => {
  it('今天与窗口里的日子都能提', () => {
    expect(leaveAction(day('2026-10-05'), CTX)).toEqual({ ok: true, reason: '' })
    expect(leaveAction(day('2026-10-20'), CTX).ok).toBe(true)
    // 窗口末日那天自己也在内（服务端判的是 `last > last_day`）。
    expect(leaveAction(day('2027-01-03'), CTX).ok).toBe(true)
  })

  it('过去的日子与窗口外的日子先被挡住，两句话分开说', () => {
    // 服务端会拒（`past_leave` / `beyond_leave`）：在控件上先挡，别让人提交了才被打回。
    expect(leaveAction(day('2026-10-04'), CTX).reason).toBe('这天已经过去了')
    expect(leaveAction(day('2027-01-04'), CTX).reason).toBe('排班还没铺到这天')
  })

  it('已经批了假的那天不再给入口', () => {
    expect(leaveAction(day('2026-10-08', { leave: true }), CTX).reason).toBe('这天已经是请假')
  })

  it('这天休也能请：休是排班给的，假是自己提的，两件事', () => {
    const rest = day('2026-10-08', { shift_id: null, shift_name: null })
    expect(leaveAction(rest, CTX).ok).toBe(true)
  })

  it('读不出这天就说读不出来，不编一句「可以」', () => {
    expect(leaveAction(null, CTX).ok).toBe(false)
    expect(leaveAction({}, CTX).ok).toBe(false)
  })
})

describe('那一天的换班：比请假多一条「那天得有你的那一行」', () => {
  it('有班的那天能换', () => {
    expect(swapAction(day('2026-10-06'), CTX)).toEqual({ ok: true, reason: '' })
  })

  it('休的那天也能换（服务端只问那一行在不在）', () => {
    // `_day_shift` 返回的 `scheduled` 才是判据：休 → 有行、没班次。
    // 批了就是对方接走你那天，你那天没班 —— 是合法换法，不能在这儿拦掉。
    const rest = day('2026-10-06', { shift_id: null, shift_name: null })
    expect(swapAction(rest, CTX).ok).toBe(true)
  })

  it('那天还没排到你 → 没得换；这句跟「休」必须分开说', () => {
    const notRostered = day('2026-10-06', { scheduled: false, shift_id: null, shift_name: null })
    expect(swapAction(notRostered, CTX).reason).toBe('这天还没排到你，没法换')
    expect(swapAction(notRostered, CTX).reason).not.toContain('休')
  })

  it('过去的、窗口外的、请了假的都换不了，理由各不相同', () => {
    expect(swapAction(day('2026-10-01'), CTX).reason).toBe('这天已经过去了')
    expect(swapAction(day('2027-02-01'), CTX).reason).toBe('排班还没铺到这天')
    expect(swapAction(day('2026-10-08', { leave: true }), CTX).reason).toBe('这天你请假，不用换班')
  })

  it('两个动作一次给完（模板不写判据）', () => {
    const both = dayActions(day('2026-10-06'), CTX)
    expect(both.leave.ok).toBe(true)
    expect(both.swap.ok).toBe(true)
    const past = dayActions(day('2026-10-01'), CTX)
    expect(past.leave.ok).toBe(false)
    expect(past.swap.ok).toBe(false)
  })
})

describe('那天我的申请：区间两端都算', () => {
  const requests = [
    { id: 1, kind: 'leave', start_date: '2026-10-08', end_date: null },
    { id: 2, kind: 'leave', start_date: '2026-10-20', end_date: '2026-10-22' },
    { id: 3, kind: 'swap', start_date: '2026-10-09', end_date: '2026-10-09' },
  ]

  it('单日申请只落在自己那一天', () => {
    expect(requestsOnDay(requests, '2026-10-08').map((r) => r.id)).toEqual([1])
    expect(requestsOnDay(requests, '2026-10-09')).toEqual([requests[2]])
  })

  it('跨天申请在区间里的每一天都看得见，两端都算', () => {
    for (const date of ['2026-10-20', '2026-10-21', '2026-10-22']) {
      expect(requestsOnDay(requests, date).map((r) => r.id)).toEqual([2])
    }
    expect(requestsOnDay(requests, '2026-10-19')).toEqual([])
    expect(requestsOnDay(requests, '2026-10-23')).toEqual([])
  })

  it('空数据、缺日期的脏数据都不炸', () => {
    expect(requestsOnDay(null, '2026-10-20')).toEqual([])
    expect(requestsOnDay([{ id: 9, start_date: null }], '2026-10-20')).toEqual([])
    expect(requestsOnDay(requests, '')).toEqual([])
  })
})

describe('那天有人找我换班', () => {
  const incoming = [
    { id: 11, business_date: '2026-10-12', employee_name: '王五' },
    { id: 12, business_date: '2026-10-13', employee_name: '赵六' },
  ]

  it('按那天认（别的天不串到这一格）', () => {
    expect(incomingOnDay(incoming, '2026-10-12').map((c) => c.id)).toEqual([11])
    expect(incomingOnDay(incoming, '2026-10-14')).toEqual([])
    expect(incomingOnDay(null, '2026-10-12')).toEqual([])
  })
})

describe('换班能点谁：先点那天在班的人', () => {
  const groups = [
    {
      shift_name: '白班',
      count: 2,
      people: [
        { id: 7, name: '余威威', zone: '案板' },
        { id: 8, name: '王五', zone: '明档' },
      ],
    },
    { shift_name: '夜班', count: 1, people: [{ id: 9, name: '李四', zone: '蒸柜' }] },
  ]

  it('摊平各班的人，带上他那天的班次与工作区', () => {
    expect(swapCandidates(groups, 7)).toEqual([
      { id: 8, name: '王五', zone: '明档', shift_name: '白班' },
      { id: 9, name: '李四', zone: '蒸柜', shift_name: '夜班' },
    ])
  })

  it('把自己去掉（按 id 认，不按名字）', () => {
    expect(swapCandidates(groups, 7).some((p) => p.id === 7)).toBe(false)
    // id 是数字、传进来的是字符串也要认得出（query / cookie 那条路上可能就变字符串了）。
    expect(swapCandidates(groups, '9').some((p) => p.id === 9)).toBe(false)
  })

  it('空组、空名单都不炸', () => {
    expect(swapCandidates(null, 7)).toEqual([])
    expect(swapCandidates([{ shift_name: '白班', people: [] }], 7)).toEqual([])
  })

  it('候选行的小字：班次与工作区各缺一个也拼得出', () => {
    expect(candidateLine({ shift_name: '白班', zone: '案板' })).toBe('白班 · 案板')
    expect(candidateLine({ shift_name: '白班', zone: '' })).toBe('白班')
    expect(candidateLine(null)).toBe('')
  })
})

describe('选中一位同事之后那句「批了会怎样」', () => {
  const me = { shift_name: '白班' }

  it('两边都有班 → 对调', () => {
    expect(swapPreview(me, { name: '王五', shift_name: '夜班' }))
      .toBe('批了你上夜班、王五上白班')
  })

  it('两个人那天同一个班次：说清换的是岗位，不写一句「你上白班、他也上白班」', () => {
    expect(swapPreview(me, { name: '孙七', shift_name: '白班' }))
      .toBe('你俩那天都是白班：批了换的是岗位（工作区）')
  })

  it('他那天没班 → 他接你的班；你那天休 → 你接他的班', () => {
    expect(swapPreview(me, { name: '王五', shift_name: '' }))
      .toBe('他那天本来没班：批了 王五 接你的白班')
    expect(swapPreview({ shift_name: '' }, { name: '王五', shift_name: '夜班' }))
      .toBe('你那天休：批了你去上夜班')
  })

  it('两个人都没班也照实说，不装作换了什么', () => {
    expect(swapPreview({ shift_name: '' }, { name: '王五', shift_name: '' }))
      .toBe('两个人那天都没有班：批了也不改什么')
  })
})

describe('从今天页跳过来那两参数', () => {
  it('act 只认 leave / swap，别的值一律当没给', () => {
    expect(actFromQuery({ act: 'leave' })).toBe('leave')
    expect(actFromQuery({ act: 'swap' })).toBe('swap')
    expect(actFromQuery({ act: 'delete' })).toBe('')
    expect(actFromQuery({})).toBe('')
    expect(actFromQuery(null)).toBe('')
  })

  it('date 只认 YYYY-MM-DD 这个形状', () => {
    expect(dateFromQuery({ date: '2026-10-05' })).toBe('2026-10-05')
    expect(dateFromQuery({ date: '2026-10-5' })).toBe('')
    expect(dateFromQuery({ date: '今天' })).toBe('')
    expect(dateFromQuery(undefined)).toBe('')
  })

  it('日期 → 月份（翻月与「回到本月」都按它比）', () => {
    expect(monthOfDate('2026-10-05')).toBe('2026-10')
    expect(monthOfDate('')).toBe('')
  })
})
