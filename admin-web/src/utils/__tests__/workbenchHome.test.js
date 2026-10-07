import { describe, expect, it } from 'vitest'
import {
  HOME_LINKS,
  countHealthCertDue,
  countOverdueFixes,
  countPendingItems,
  countPendingLeaves,
  onDutyNames,
  staffShiftCell,
  staffWorkCounts,
  workbenchHomeSummary,
} from '../workbenchHome.js'

// 工作台首页（票 06）的聚合口径：**只用已有接口的响应**算出那几个数字，
// 不多发一次请求、不新增端点，也不在这里做任何业务动作。
//
// 断言对着**接口真实形状**写（`/api/scheduling/day` 的 `groups[].people[]`、
// `/api/scheduling/inbox` 的 `requests[].kind`、`/api/hygiene/admin/daily-queue`
// 的 `items[].status`、`/api/hygiene/admin/fix` 的 `items[].deadline`）——
// 形状是从 `api/*.py` 与 `services/*` 读出来的，不是照着实现再抄一遍。

const DAY = {
  business_date: '2026-10-05',
  total: 3,
  off_count: 2,
  groups: [
    {
      shift: { id: 1, name: '白班' },
      count: 2,
      people: [{ id: 1, name: '张三', zone: '后厨' }, { id: 2, name: '李四', zone: null }],
    },
    { shift: { id: 2, name: '夜班' }, count: 1, people: [{ id: 3, name: '王五', zone: '洗碗间' }] },
  ],
  off_people: [],
}

const INBOX = {
  today: '2026-10-05',
  requests: [
    { id: 1, kind: 'leave', employee_name: '张三' },
    { id: 2, kind: 'swap', employee_name: '李四' },
    { id: 3, kind: 'leave', employee_name: '王五' },
  ],
  without_rule: [],
}

const QUEUE = {
  date: '2026-10-05',
  is_today: true,
  items: [{ item_id: 1, status: '待验收' }],
  // 2026-10 花名册改版：健康证到期那一格的数字（未停用且临期 / 过期的人数）。
  health_cert_due: { count: 2, items: [{ id: 5, name: '孙平', expires_on: '2026-09-01', state: 'expired' }] },
}

const FIX = {
  items: [
    { id: 1, status: '待回拍', deadline: '2026-10-05T02:00:00+08:00' },
    { id: 2, status: '待回拍', deadline: '2026-10-06T02:00:00+08:00' },
    { id: 3, status: '待验收', deadline: '2026-10-05T01:00:00+08:00' },
  ],
}

describe('店长那三个数字', () => {
  it('待批请假只数请假，等对方点头的换班不算（它在店长这儿还看不到）', () => {
    expect(countPendingLeaves(INBOX)).toBe(2)
    expect(countPendingLeaves({ requests: [{ kind: 'swap' }] })).toBe(0)
    // 队列整个读不出来：给 null（页面画「—」），不是 0 —— 「没有待批」与「没读到」不是一回事。
    expect(countPendingLeaves(null)).toBeNull()
    expect(countPendingLeaves(undefined)).toBeNull()
  })

  it('待验收就是队列里那几条（不带日期时服务端已经只回「待验收」）', () => {
    expect(countPendingItems(QUEUE)).toBe(1)
    expect(countPendingItems({ items: [{ status: '待验收' }, { status: '待验收' }] })).toBe(2)
    // 回看某个历史营业日时会带上别的状态（已通过 / 待拍），那些不算。
    expect(countPendingItems({ items: [{ status: '已通过' }, { status: '待拍' }] })).toBe(0)
    expect(countPendingItems(null)).toBeNull()
  })

  it('逾期整改只数「过了截止时间、还等着回拍」那些', () => {
    const now = Date.parse('2026-10-05T03:00:00+08:00')
    // 1 号已超时且待回拍 → 算；2 号还没到点 → 不算；3 号是等验收（已回拍）→ 不算。
    expect(countOverdueFixes(FIX, now)).toBe(1)
    // 时间点换到 6 号：两张待回拍都过点了。
    expect(countOverdueFixes(FIX, Date.parse('2026-10-06T03:00:00+08:00'))).toBe(2)
    // 没有截止时间 / 坏时间戳的：不算逾期（判不了就不报）。
    expect(countOverdueFixes({ items: [{ status: '待回拍', deadline: null }] }, now)).toBe(0)
    expect(countOverdueFixes({ items: [{ status: '待回拍', deadline: '不是时间' }] }, now)).toBe(0)
    expect(countOverdueFixes(null, now)).toBeNull()
  })

  it('今天谁上班：按班次分组，名字取那些真上班的人（休的人不在里面）', () => {
    const groups = onDutyNames(DAY)
    expect(groups.map((group) => group.shift)).toEqual(['白班', '夜班'])
    expect(groups.map((group) => group.count)).toEqual([2, 1])
    expect(groups[0].names).toEqual(['张三', '李四'])
    // 名字缺失（花名册里刚被清掉）时不渲染空名字，也不抛错。
    expect(onDutyNames({ groups: [{ shift: { name: '白班' }, people: [{ id: 9, name: '' }] }] }))
      .toEqual([{ shift: '白班', count: 0, names: [] }])
    expect(onDutyNames(null)).toEqual([])
  })
})

describe('员工那一档', () => {
  const ME = {
    employee: { id: 1, name: '张三' },
    today: '2026-10-05',
    days: [{
      business_date: '2026-10-05',
      is_today: true,
      scheduled: true,
      shift_id: 1,
      shift_name: '白班',
      zone_id: 3,
      zone_name: '后厨',
      leave: false,
    }],
  }

  it('我的班次与工作区复用「今天」页那套翻译（休 / 请假 / 还没排分得开）', () => {
    expect(staffShiftCell(ME.days[0])).toEqual({
      headline: '白班', subline: '今天上班 · 后厨', tone: '',
    })
    expect(staffShiftCell({ scheduled: true, shift_id: null }).headline).toBe('休')
    expect(staffShiftCell({ scheduled: false }).headline).toBe('今天没有你的班')
    expect(staffShiftCell({ scheduled: true, shift_id: null, leave: true }).headline).toBe('请假')
    // 班次被删了：说「班次已调整」，不假装那天是休（跟「今天」页同一条口径）。
    expect(staffShiftCell({ scheduled: true, shift_id: 5, shift_name: null }).headline)
      .toBe('班次已调整')
    expect(staffShiftCell(null).headline).toBe('今天没有你的班')
  })

  it('我的待办条数：日常 + 专项 + 整改里没通过的那些，加上等我回应的换班', () => {
    const buckets = {
      daily: { items: [{ status: '待拍' }, { status: '待验收' }, { status: '已通过' }] },
      deep: { items: [{ status: '待拍' }] },
      fix: { items: [{ id: 1, status: '待回拍' }, { id: 2, status: '已通过' }] },
      requests: { incoming: [{ id: 7, kind: 'swap' }] },
    }
    expect(countPendingItems(buckets.daily)).toBe(1)
    const summary = workbenchHomeSummary({ identity: 'staff', me: ME, inbox: buckets.requests,
      daily: buckets.daily, deep: buckets.deep, fix: buckets.fix })
    expect(summary.view).toBe('staff')
    expect(summary.work.pending).toBe(4)
    // 三块分别数出来（首页只摊一个总数，但分块数在票尾要能对上）。日常那三条里
    // 只有「待拍」与「待验收」是没完成的 —— 与员工端「卫生待办」页三个角标同一口径。
    expect(summary.work.daily).toBe(2)
    expect(summary.work.deep).toBe(1)
    expect(summary.work.fix).toBe(1)
    // 等我回应的换班也算我的待办（在「今天」页那张卡上处理）。
    expect(summary.incoming).toBe(1)
  })

  it('拿不到数据时数出来是 0，不是 NaN', () => {
    const summary = workbenchHomeSummary({ identity: 'staff', me: null })
    // 一块都没读到：整格给 null（页面画「—」），不是 0 —— 「没有活」与「没读到」不是一回事。
    expect(summary.work.pending).toBeNull()
    expect(summary.incoming).toBeNull()
    expect(summary.me.shift.headline).toBe('今天没有你的班')
  })

  it('有一块待办读不出来时，总数不装作少了一块（给 null）', () => {
    const counts = staffWorkCounts({
      daily: { items: [{ status: '待拍' }] },
      deep: null,
      fix: { items: [{ id: 1, status: '待回拍' }] },
      requests: { incoming: [] },
    })
    expect(counts.daily).toBe(1)
    expect(counts.deep).toBeNull()
    expect(counts.pending).toBeNull()
    expect(counts.incoming).toBe(0)
  })
})

describe('按身份给两个视角', () => {
  it('店长：今天谁上班 + 四个数字（各自带跳转目标）', () => {
    const summary = workbenchHomeSummary({
      identity: 'super',
      day: DAY,
      inbox: INBOX,
      queue: QUEUE,
      fix: FIX,
      now: Date.parse('2026-10-05T03:00:00+08:00'),
    })
    expect(summary.view).toBe('super')
    expect(summary.duty.total).toBe(3)
    expect(summary.duty.groups.map((group) => group.shift)).toEqual(['白班', '夜班'])
    expect(summary.leaves).toBe(2)
    expect(summary.reviews).toBe(1)
    expect(summary.fixes).toBe(1)
    expect(summary.certs).toBe(2)
  })

  it('健康证到期：数字只认 daily-queue 的 health_cert_due.count，读不到给 null（不是 0）', () => {
    expect(countHealthCertDue(QUEUE)).toBe(2)
    expect(countHealthCertDue({ ...QUEUE, health_cert_due: { count: 0, items: [] } })).toBe(0)
    // 键没下发 / 整个队列读不出来 → 「没读到」，页面画「—」。
    expect(countHealthCertDue({ ...QUEUE, health_cert_due: undefined })).toBeNull()
    expect(countHealthCertDue(null)).toBeNull()

    const summary = workbenchHomeSummary({ identity: 'super', queue: { items: [] } })
    expect(summary.certs).toBeNull()
  })

  it('员工：不摊店长那几个数字（两种视角互斥，不各显一半）', () => {
    const summary = workbenchHomeSummary({
      identity: 'staff',
      day: DAY,
      inbox: INBOX,
      queue: QUEUE,
      fix: FIX,
    })
    expect(summary.view).toBe('staff')
    expect(summary.duty).toBeNull()
    expect(summary.leaves).toBeNull()
    expect(summary.reviews).toBeNull()
    expect(summary.fixes).toBeNull()
  })

  it('身份还没探出来（null）：哪个视角都不给', () => {
    for (const identity of [null, undefined, '', 'admin']) {
      const summary = workbenchHomeSummary({ identity, day: DAY, inbox: INBOX })
      expect(summary.view, String(identity)).toBeNull()
      expect(summary.duty).toBeNull()
      expect(summary.me).toBeNull()
    }
  })
})

describe('数字点进去的地方', () => {
  it('四个数字各指一页专页，员工那两个指员工页', () => {
    expect(HOME_LINKS.leaves).toBe('/workbench/hr/inbox')
    expect(HOME_LINKS.reviews).toBe('/workbench/floor/daily')
    expect(HOME_LINKS.fixes).toBe('/workbench/floor/fix')
    expect(HOME_LINKS.certs).toBe('/workbench/hr/roster')
    expect(HOME_LINKS.duty).toBe('/workbench/hr/calendar')
    expect(HOME_LINKS.myItems).toBe('/workbench/me/clean')
    expect(HOME_LINKS.myRequests).toBe('/workbench/me/today')
  })
})
