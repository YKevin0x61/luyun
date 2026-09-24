import { describe, expect, it } from 'vitest'
import {
  REQUEST_STATUS,
  approveReceipt,
  canCancel,
  incomingLine,
  kindText,
  noteText,
  previewLine,
  requestLine,
  requestRangeText,
  shortDay,
  splitRuleless,
  statusText,
  statusTone,
  swapApproveReceipt,
  swapPreviewLine,
  swapStatusText,
} from '../leaveRequest'

// 服务端 `scheduling_requests` 的一行：请假（票 08）。
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

// 换班（票 09）：多一个「跟谁换」，状态多一档「等对方」。
const SWAP = {
  id: 9,
  employee_id: 3,
  kind: 'swap',
  start_date: '2026-09-25',
  end_date: '2026-09-25',
  status: 'pending_peer',
  note: null,
  peer_employee_id: 4,
  peer_name: '李四',
  decided_at: null,
}

describe('申请状态翻成人话（票 08、09）', () => {
  it('五个状态各说各的，谁也不跟谁混', () => {
    expect(statusText(REQUEST_STATUS.pendingPeer)).toBe('等对方同意')
    expect(statusText(REQUEST_STATUS.pendingManager)).toBe('等店长批')
    expect(statusText(REQUEST_STATUS.approved)).toBe('批了')
    expect(statusText(REQUEST_STATUS.rejected)).toBe('驳回了')
    expect(statusText(REQUEST_STATUS.cancelled)).toBe('已撤回')
    // 五条话两两不同：把 rejected 写成「等店长批」是这类页面上最糟的错。
    const said = [
      'pending_peer',
      'pending_manager',
      'approved',
      'rejected',
      'cancelled',
    ].map(statusText)
    expect(new Set(said).size).toBe(5)
  })

  it('语气跟着状态走，「等店长批」不是「批了」，两档「等」同一种语气', () => {
    expect(statusTone('pending_peer')).toBe('wait')
    expect(statusTone('pending_manager')).toBe('wait')
    expect(statusTone('approved')).toBe('ok')
    expect(statusTone('rejected')).toBe('no')
    expect(statusTone('cancelled')).toBe('off')
    expect(statusTone('pending_manager')).not.toBe(statusTone('approved'))
  })

  it('认不出的状态原样回，不显示成空白', () => {
    expect(statusText('weird_state')).toBe('weird_state')
    expect(statusText('')).toBe('')
    expect(statusText(null)).toBe('')
    expect(statusTone('weird_state')).toBe('off')
    expect(statusTone(null)).toBe('off')
  })

  it('「等对方」与「等店长」都还撤得回，批完/驳完/撤过的都撤不动', () => {
    expect(canCancel(PENDING)).toBe(true)
    expect(canCancel(SWAP)).toBe(true)
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

  it('换班那条走自己的五个说法，且点名卡在谁那里（票 09 验收 7）', () => {
    expect(requestLine(SWAP)).toBe('换班 · 9/25 周五 · 等 李四 同意')
    expect(requestLine({ ...SWAP, status: 'pending_manager' })).toBe(
      '换班 · 9/25 周五 · 李四 同意了 · 等店长批',
    )
    expect(requestLine({ ...SWAP, status: 'approved' })).toBe('换班 · 9/25 周五 · 换成了')
    expect(requestLine({ ...SWAP, status: 'rejected' })).toBe('换班 · 9/25 周五 · 李四 没同意')
    expect(requestLine({ ...SWAP, status: 'cancelled' })).toBe('换班 · 9/25 周五 · 已撤回')
    // 名字拿不到时说「对方」，不编一个名字出来。
    expect(swapStatusText({ status: 'pending_peer' })).toBe('等 对方 同意')
    expect(swapStatusText({ status: 'weird_state' })).toBe('weird_state')
  })
})

describe('别人问我换班的那张卡（票 09 验收 2）', () => {
  const CARD = {
    id: 9,
    employee_name: '张三',
    business_date: '2026-09-25',
    their_scheduled: true,
    their_shift_name: '白班',
    my_scheduled: true,
    my_shift_name: '夜班',
  }

  it('先说他要拿什么换，再说我那天是什么', () => {
    expect(incomingLine(CARD)).toBe('张三 想跟你换 9/25 周五 · 他那天 白班 · 你那天 夜班')
  })

  it('我那天本来就休、或班次没了，都说得出来', () => {
    expect(incomingLine({ ...CARD, my_scheduled: false, my_shift_name: null })).toBe(
      '张三 想跟你换 9/25 周五 · 他那天 白班 · 你那天休',
    )
    expect(incomingLine({ ...CARD, my_shift_name: null })).toBe(
      '张三 想跟你换 9/25 周五 · 他那天 白班 · 你那天 班次已调整',
    )
    expect(incomingLine(null)).toBe('')
  })
})

describe('店长待办里换班那句「批了会怎样」（票 09 验收 4/5）', () => {
  const DAY = {
    business_date: '2026-09-25',
    past: false,
    scheduled: true,
    current_shift_name: '白班',
    peer_scheduled: true,
    peer_shift_name: '夜班',
  }

  it('两个人都有班：把对调写成一句看得懂的话', () => {
    expect(swapPreviewLine(DAY, '张三', '李四')).toBe(
      '9/25 白班 ⇄ 夜班：批了 张三 上夜班、李四 上白班',
    )
  })

  it('对方那天本来就休：他接走这个班，提的人那天空出来', () => {
    expect(swapPreviewLine({ ...DAY, peer_scheduled: false }, '张三', '李四')).toBe(
      '9/25 李四 那天休：批了 李四 接白班，张三 那天没班',
    )
  })

  it('提的人那天本来就没班：他接走对方那个班', () => {
    expect(swapPreviewLine({ ...DAY, current_shift_name: null }, '张三', '李四')).toBe(
      '9/25 张三 那天没班：批了 张三 接夜班，李四 那天没班',
    )
  })

  it('两个人那天都没有班：明说批了也不改什么', () => {
    expect(
      swapPreviewLine({ ...DAY, current_shift_name: null, peer_scheduled: false }, '张三', '李四'),
    ).toBe('9/25 两个人那天都没有班：批了也不改什么')
  })

  it('过去的日子说明白「批了也不改历史」', () => {
    expect(swapPreviewLine({ ...DAY, past: true }, '张三', '李四')).toBe(
      '9/25 已经过去：批了也不改历史',
    )
    expect(swapPreviewLine(null, '张三', '李四')).toBe('')
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

describe('换班批完的回执（票 09）', () => {
  it('对调了就写对调，不写成请假', () => {
    expect(swapApproveReceipt({ applied_days: ['2026-09-25'], skipped_days: [] })).toBe(
      '批了：9/25 两个人的班对调了',
    )
    expect(swapApproveReceipt({ applied_days: ['2026-09-25'], skipped_days: [] })).not.toContain(
      '请假',
    )
  })

  it('「批了」不等于「改了」：那天已经过去就说清楚', () => {
    expect(swapApproveReceipt({ applied_days: [], skipped_days: ['2026-09-24'] })).toBe(
      '批了，但这天已经过去：排班没动（9/24）',
    )
    expect(swapApproveReceipt(null)).toBe('批了，但这天已经过去：排班没动')
  })
})
