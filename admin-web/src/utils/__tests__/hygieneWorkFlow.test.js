import { describe, expect, it } from 'vitest'
import {
  buildWorkQueue,
  clockDueAt,
  dailyPrimaryAction,
  dailyProgress,
  deadlineText,
  deadlineUrgency,
  deepPrimaryAction,
  fixPrimaryAction,
  formatStamp,
  groupByZone,
  nextAfterRemove,
  nextDeepShootRow,
  nextFixWorkRow,
  nextShootRow,
  openRows,
  queueGroups,
  shiftClock,
  statusTone,
  tabWorkCount,
  workJumps,
} from '../hygieneWorkFlow.js'

describe('hygieneWorkFlow', () => {
  const anbanTodo = {
    item_id: 1, zone_id: 10, zone_name: '案板', shift: '白班', status: '待拍', item_name: '案板台面',
  }
  const anbanPending = {
    item_id: 2, zone_id: 10, zone_name: '案板', shift: '白班', status: '待验收', item_name: '案板地面',
  }
  const xianTodo = {
    item_id: 3, zone_id: 11, zone_name: '馅档', shift: '白班', status: '待拍', item_name: '馅盆',
  }
  const xianPassed = {
    item_id: 4, zone_id: 11, zone_name: '馅档', shift: '白班', status: '已通过', item_name: '馅柜',
  }

  it('progress and open rows hide passed work from the working list', () => {
    const items = [anbanTodo, anbanPending, xianTodo, xianPassed]
    expect(dailyProgress(items)).toEqual({
      total: 4, passed: 1, pending: 1, todo: 2, remaining: 3,
    })
    expect(openRows(items).map((row) => row.item_id)).toEqual([1, 2, 3])
    expect(groupByZone(openRows(items)).map((zone) => zone.name)).toEqual(['案板', '馅档'])
    // 待办页只列日常：专项、整改不进待办角标，各自 tab 单独算（见下面两行）。
    expect(tabWorkCount('inbox', {
      inbox: items,
      deepInbox: [{ status: '待拍' }],
      fixInbox: [{ id: 8 }],
    })).toBe(3)
    expect(tabWorkCount('deep', { deepInbox: [{ status: '已通过' }, { status: '待拍' }] })).toBe(1)
    expect(tabWorkCount('fix', { fixInbox: [{ id: 8 }, { id: 9 }] })).toBe(2)
    expect(tabWorkCount('boards', { inbox: items })).toBe(0)
  })

  it('next shoot stays in the same zone, then the next unfinished zone', () => {
    const items = [anbanTodo, anbanPending, xianTodo, xianPassed]
    expect(nextShootRow(items, anbanTodo)).toEqual(xianTodo)
    expect(nextShootRow([anbanPending, xianPassed], anbanTodo)).toBeNull()
    expect(nextDeepShootRow(
      [{ item_id: 1, status: '待拍' }, { item_id: 2, status: '待拍' }, { item_id: 3, status: '待验收' }],
      { item_id: 1 },
    )).toEqual({ item_id: 2, status: '待拍' })
  })

  it('fix work prefers 待回拍; managers can continue into 待验收', () => {
    const waiting = { id: 2, status: '待回拍' }
    const pending = { id: 3, status: '待验收' }
    expect(nextFixWorkRow([waiting, pending], { id: 1 }, { isManager: false })).toEqual(waiting)
    expect(nextFixWorkRow([pending], { id: 1 }, { isManager: false })).toBeNull()
    expect(nextFixWorkRow([pending], { id: 1 }, { isManager: true })).toEqual(pending)
  })

  it('primary actions: pending review first, passed has none', () => {
    expect(dailyPrimaryAction(anbanTodo)).toBe('shoot')
    expect(dailyPrimaryAction(anbanPending)).toBe('review')
    expect(dailyPrimaryAction(xianPassed)).toBe('none')
    expect(deepPrimaryAction({ status: '待拍' })).toBe('shoot')
    expect(fixPrimaryAction({ status: '待回拍' }, { isManager: true })).toBe('reshoot')
    expect(fixPrimaryAction({ status: '待验收' }, { isManager: true })).toBe('review')
    expect(fixPrimaryAction({ status: '待验收' }, { isManager: false })).toBe('reshoot')
  })

  it('deadline urgency and shift clocks stay local to the picked 班次', () => {
    const now = Date.parse('2026-09-13T12:00:00+08:00')
    expect(deadlineUrgency('2026-09-13T11:59:00+08:00', now)).toBe('overdue')
    expect(deadlineUrgency('2026-09-13T12:20:00+08:00', now)).toBe('soon')
    expect(deadlineUrgency('2026-09-13T18:00:00+08:00', now)).toBe('ok')
    expect(deadlineUrgency('', now)).toBe('none')
    expect(shiftClock('白班', { day_hhmm: '15:00', night_hhmm: '21:30' })).toBe('15:00')
    expect(shiftClock('夜班', { day_hhmm: '15:00', night_hhmm: '21:30' })).toBe('21:30')
    expect(shiftClock(null, { day_hhmm: '15:00' })).toBe('')
    expect(formatStamp('2026-09-13T15:00:00+08:00')).toBe('2026-09-13 15:00')
    expect(statusTone('待验收')).toBe('pending')
    expect(workJumps({ deepRemaining: 2, fixRemaining: 1 })).toEqual([
      { tab: 'deep', label: '专项还有 2 项' },
      { tab: 'fix', label: '整改还有 1 张' },
    ])
  })

  it('builds one prioritized work queue across daily, deep-clean, and fix tickets', () => {
    const now = Date.parse('2026-09-13T14:50:00+08:00')
    const queue = buildWorkQueue({
      now,
      shiftDue: '15:00',
      deepDue: '21:30',
      isManager: true,
      inbox: [
        {
          ...anbanTodo,
          business_date: '2026-09-13',
          item_name: '案板台面',
        },
        {
          ...anbanPending,
          business_date: '2026-09-13',
          item_name: '案板地面',
        },
      ],
      deepInbox: [{
        item_id: 30,
        item_name: '冰箱除霜',
        business_date: '2026-09-13',
        status: '待拍',
      }],
      fixInbox: [{
        id: 40,
        zone_name: '馅档',
        ticket_type: '卫生',
        body_text: '地面有油渍',
        deadline: '2026-09-13T14:40:00+08:00',
        status: '待回拍',
      }],
    })

    expect(queue.map((task) => task.kind)).toEqual(['fix', 'daily', 'deep', 'daily'])
    expect(queue[0].bucket).toBe('overdue')
    expect(queue[1].dueText).toBe('还剩 10 分钟')
    expect(queue[3].bucket).toBe('waiting')
    expect(queue[3].dueText).toBe('已交，等验收')
    expect(queueGroups(queue).map((group) => group.id)).toEqual([
      'overdue', 'soon', 'todo', 'waiting',
    ])
  })

  it('turns configured clocks and deadlines into staff-readable text', () => {
    expect(clockDueAt('15:00', '2026-09-13')).toBe('2026-09-13T15:00:00+08:00')
    expect(clockDueAt('05:00', '2026-09-13')).toBe('2026-09-14T05:00:00+08:00')
    expect(clockDueAt('', '2026-09-13')).toBe('')
    const now = Date.parse('2026-09-13T14:50:00+08:00')
    expect(deadlineText('2026-09-13T15:00:00+08:00', now)).toBe('还剩 10 分钟')
    expect(deadlineText('2026-09-13T14:20:00+08:00', now)).toBe('已超时 30 分钟')
    expect(deadlineText('2026-09-13T19:00:00+08:00', now)).toBe('还剩 5 小时')
    expect(deadlineText('2026-09-15T15:00:00+08:00', now)).toBe('09-15 15:00 前')
    expect(deadlineText('', now)).toBe('')
  })

  it('admin queue keeps the next row after the current one is removed', () => {
    const items = [
      { item_id: 1, shift: '白班' },
      { item_id: 2, shift: '白班' },
      { item_id: 3, shift: '夜班' },
    ]
    expect(nextAfterRemove(items, items[1], (row) => `${row.item_id}-${row.shift}`)).toEqual(items[2])
    expect(nextAfterRemove(items, items[2], (row) => `${row.item_id}-${row.shift}`)).toEqual(items[1])
    expect(nextAfterRemove([items[0]], items[0], (row) => `${row.item_id}-${row.shift}`)).toBeNull()
  })
})
