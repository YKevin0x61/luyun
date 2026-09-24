import { describe, expect, it } from 'vitest'
import {
  REQUEST_STATUS,
  approveReceipt,
  canCancel,
  kindText,
  noteText,
  previewLine,
  requestLine,
  requestRangeText,
  shortDay,
  splitRuleless,
  statusText,
  statusTone,
} from '../leaveRequest'

// 服务端 `scheduling_requests` 的一行：`kind` 现在只有 `leave`（换班是票 09）。
const PENDING = {
  id: 7,
  employee_id: 3,
  kind: 'leave',
  start_date: '2026-09-25',
  end_date: '2026-09-26',
  status: 'pending_manager',
  note: '家里有事',
  decided_at: null,
}

describe('申请状态翻成人话（票 08）', () => {
  it('四个状态各说各的，谁也不跟谁混', () => {
    expect(statusText(REQUEST_STATUS.pendingManager)).toBe('等店长批')
    expect(statusText(REQUEST_STATUS.approved)).toBe('批了')
    expect(statusText(REQUEST_STATUS.rejected)).toBe('驳回了')
    expect(statusText(REQUEST_STATUS.cancelled)).toBe('已撤回')
    // 四条话两两不同：把 rejected 写成「等店长批」是这类页面上最糟的错。
    const said = ['pending_manager', 'approved', 'rejected', 'cancelled'].map(statusText)
    expect(new Set(said).size).toBe(4)
  })

  it('语气跟着状态走，「等店长批」不是「批了」', () => {
    expect(statusTone('pending_manager')).toBe('wait')
    expect(statusTone('approved')).toBe('ok')
    expect(statusTone('rejected')).toBe('no')
    expect(statusTone('cancelled')).toBe('off')
    expect(statusTone('pending_manager')).not.toBe(statusTone('approved'))
  })

  it('认不出的状态原样回，不显示成空白', () => {
    expect(statusText('pending_peer')).toBe('pending_peer')
    expect(statusText('')).toBe('')
    expect(statusText(null)).toBe('')
    expect(statusTone('pending_peer')).toBe('off')
    expect(statusTone(null)).toBe('off')
  })

  it('只有「等店长批」的申请能撤回', () => {
    expect(canCancel(PENDING)).toBe(true)
    expect(canCancel({ ...PENDING, status: 'approved' })).toBe(false)
    expect(canCancel({ ...PENDING, status: 'rejected' })).toBe(false)
    expect(canCancel({ ...PENDING, status: 'cancelled' })).toBe(false)
    expect(canCancel(null)).toBe(false)
    expect(canCancel({})).toBe(false)
  })

  it('种类只有请假与换班两个词，别的原样回', () => {
    expect(kindText('leave')).toBe('请假')
    expect(kindText('swap')).toBe('换班')
    expect(kindText('')).toBe('')
    expect(kindText(null)).toBe('')
  })

  it('事由没写就不占一行（空格也算没写）', () => {
    expect(noteText('家里有事')).toBe('事由：家里有事')
    expect(noteText('  家里有事  ')).toBe('事由：家里有事')
    expect(noteText('')).toBe('')
    expect(noteText('   ')).toBe('')
    expect(noteText(null)).toBe('')
  })
})

describe('申请区间写成「9/25」或「9/25 – 9/26」', () => {
  it('只取月/日，坏数据给空串', () => {
    expect(shortDay('2026-09-25')).toBe('9/25')
    expect(shortDay('2026-12-01')).toBe('12/1')
    expect(shortDay('')).toBe('')
    expect(shortDay(null)).toBe('')
    expect(shortDay('9/25')).toBe('')
  })

  it('一天只说一次；跨天用顿号连起来', () => {
    expect(requestRangeText('2026-09-25', '2026-09-25')).toBe('9/25')
    expect(requestRangeText('2026-09-25', null)).toBe('9/25')
    expect(requestRangeText('2026-09-25', '')).toBe('9/25')
    expect(requestRangeText('2026-09-25', '2026-09-26')).toBe('9/25 – 9/26')
    expect(requestRangeText('', '2026-09-26')).toBe('')
  })
})

describe('自己那条申请的时间线（票 08 验收 4）', () => {
  it('一天与跨天两种写法，都带周几', () => {
    expect(requestLine(PENDING)).toBe('请假 · 9/25 周五 起 9/26 周六 · 等店长批')
    expect(requestLine({ ...PENDING, end_date: '2026-09-25' })).toBe('请假 · 9/25 周五 · 等店长批')
    expect(requestLine({ ...PENDING, end_date: null })).toBe('请假 · 9/25 周五 · 等店长批')
    expect(requestLine({ ...PENDING, status: 'approved' })).toBe(
      '请假 · 9/25 周五 起 9/26 周六 · 批了',
    )
    expect(requestLine(null)).toBe('')
  })
})

describe('待办上那句「批了会怎样」（票 08 验收 2）', () => {
  const DAY = {
    business_date: '2026-09-25',
    past: false,
    scheduled: true,
    current_shift_id: 3,
    current_shift_name: '白班',
    after: [{ shift_id: 3, shift_name: '白班', count: 1 }],
  }

  it('正常那天说原定什么班、批完还剩几个人', () => {
    expect(previewLine(DAY)).toBe('9/25 原定白班 · 批了之后 白班剩 1 人')
    expect(
      previewLine({ ...DAY, after: [{ shift_id: 4, shift_name: '夜班', count: 2 }] }),
    ).toBe('9/25 原定白班 · 批了之后 夜班剩 2 人')
  })

  it('本来没有别人的班就只说原定班次，不编一个 0 人', () => {
    expect(previewLine({ ...DAY, after: [] })).toBe('9/25 原定白班')
  })

  it('那天本来就休：说出来，不说「原定休」', () => {
    expect(previewLine({ ...DAY, current_shift_id: null, current_shift_name: null })).toBe(
      '9/25 那天本来就休 · 批了之后 白班剩 1 人',
    )
  })

  it('已经过去的日子说明白「批了也不改历史」（票 07 的口径）', () => {
    expect(previewLine({ ...DAY, past: true })).toBe('9/25 已经过去：批了也不改历史')
    // 过去优先于其它两种说法：那天有没有排都不重要了。
    expect(previewLine({ ...DAY, past: true, scheduled: false })).toBe(
      '9/25 已经过去：批了也不改历史',
    )
  })

  it('那天没有你的班：跟「本来就休」分开说，但人头数照样报（没配规则的新人也看得见）', () => {
    expect(previewLine({ ...DAY, scheduled: false, current_shift_name: null })).toBe(
      '9/25 那天没有你的班 · 批了之后 白班剩 1 人',
    )
    // 那天整张表都还没铺：没人头可说，就只说这一句。
    expect(
      previewLine({ ...DAY, scheduled: false, current_shift_name: null, after: [] }),
    ).toBe('9/25 那天没有你的班')
    expect(previewLine(null)).toBe('')
  })
})

describe('批准回执（票 08 验收 3）', () => {
  it('批到的天数写出来', () => {
    expect(approveReceipt({ applied_days: ['2026-09-25', '2026-09-26'], skipped_days: [] })).toBe(
      '批了：9/25、9/26 记成请假',
    )
  })

  it('「批了」不等于「改了」：两边都有时说清楚', () => {
    expect(approveReceipt({ applied_days: ['2026-09-25'], skipped_days: ['2026-09-24'] })).toBe(
      '批了：9/25 记成请假；9/24 已经过去，排班没动',
    )
  })

  it('全都已经过去：排班一个字没动', () => {
    expect(approveReceipt({ applied_days: [], skipped_days: ['2026-09-24'] })).toBe(
      '批了，但这几天都已经过去：排班没动（9/24）',
    )
    expect(approveReceipt({})).toBe('批了，但这几天都已经过去：排班没动')
    expect(approveReceipt(null)).toBe('批了，但这几天都已经过去：排班没动')
  })
})

describe('还没配规则的人分两拨（票 08 验收 2）', () => {
  const person = (extra) => ({ id: 1, name: '赵六', approved: true, disabled: false, ...extra })

  it('在职的要提醒，停用与没批准的不提醒', () => {
    const { active, muted } = splitRuleless([
      person({ id: 1, name: '赵六' }),
      person({ id: 2, name: '钱七', disabled: true }),
      person({ id: 3, name: '孙八', approved: false }),
    ])
    expect(active.map((item) => item.name)).toEqual(['赵六'])
    expect(muted.map((item) => item.name)).toEqual(['钱七', '孙八'])
  })

  it('空名单与坏数据都不炸', () => {
    expect(splitRuleless([])).toEqual({ active: [], muted: [] })
    expect(splitRuleless(null)).toEqual({ active: [], muted: [] })
    expect(splitRuleless([null]).muted).toHaveLength(1)
  })
})
