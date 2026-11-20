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
  isPendingReview,
  nextAfterRemove,
  nextDeepShootRow,
  nextFixWorkRow,
  nextShootRow,
  openRows,
  queueActionLabel,
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
  // 服务端判据（`services/hygiene/judge.py` 给每行挂的 `review`）：页面只读它，不再自己算。
  const JUDGE_OK = { allowed: true, reason: '', action: 'review' }
  const JUDGE_DENIED = { allowed: false, reason: 'forbidden', action: 'view' }

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

  it('fix work prefers 待回拍; a judgeable 待验收 can be continued into', () => {
    const waiting = { id: 2, status: '待回拍' }
    const pendingDenied = { id: 3, status: '待验收', review: JUDGE_DENIED }
    const pendingAllowed = { id: 4, status: '待验收', review: JUDGE_OK }
    expect(nextFixWorkRow([waiting, pendingDenied], { id: 1 })).toEqual(waiting)
    // 判不了的单不自动跳（会落进一屏没有决定按钮的面板）。
    expect(nextFixWorkRow([pendingDenied], { id: 1 })).toBeNull()
    expect(nextFixWorkRow([pendingAllowed], { id: 1 })).toEqual(pendingAllowed)
  })
  it('primary actions: pending review first, passed has none', () => {
    expect(dailyPrimaryAction(anbanTodo)).toBe('shoot')
    expect(dailyPrimaryAction({ ...anbanPending, review: JUDGE_OK })).toBe('review')
    expect(dailyPrimaryAction(xianPassed)).toBe('none')
    expect(deepPrimaryAction({ status: '待拍' })).toBe('shoot')
    expect(fixPrimaryAction({ status: '待回拍' })).toBe('reshoot')
    expect(fixPrimaryAction({ status: '待验收', review: JUDGE_OK })).toBe('review')
    expect(fixPrimaryAction({ status: '待验收', review: JUDGE_DENIED })).toBe('reshoot')
    // 没有 `review` 的行按"判不了"处理（老缓存、拼错的行都不该凭空多出一颗能点的验收）。
    expect(dailyPrimaryAction(anbanPending)).toBe('view')
  })
  it('待验收那一档只读服务端结论；队列文案与行内按钮同源', () => {
    // 2026-10-05 审查 F-04：判据原来有两份（行内按钮一份、队列文案一份），一分叉普通员工
    // 就看到一个既没有决定按钮、也没有说明的空壳面板。现在两份都只是 `review` 的读取方。
    expect(dailyPrimaryAction({ ...anbanPending, review: JUDGE_OK })).toBe('review')
    expect(dailyPrimaryAction({ ...anbanPending, review: JUDGE_DENIED })).toBe('view')
    expect(deepPrimaryAction({ status: '待验收', review: JUDGE_OK })).toBe('review')
    expect(deepPrimaryAction({ status: '待验收', review: JUDGE_DENIED })).toBe('view')
    // 待拍 / 已通过那一档不随判据变：谁都要拍，通过了就是没有动作。
    expect(dailyPrimaryAction(anbanTodo)).toBe('shoot')
    expect(deepPrimaryAction({ status: '待拍' })).toBe('shoot')
    expect(dailyPrimaryAction(xianPassed)).toBe('none')
  })
  it('「重拍」问的是另一件事：交了没有，跟谁能验收无关', () => {
    // ADR 0071：待验收期间再交一张会替换上一张，交这张的人（普通员工也算）永远能重拍。
    expect(isPendingReview({ status: '待验收' })).toBe(true)
    expect(isPendingReview({ status: '待拍' })).toBe(false)
    expect(isPendingReview({ status: '已通过' })).toBe(false)
    expect(isPendingReview(null)).toBe(false)
  })

  it('buildWorkQueue 的动作文案跟着同一份服务端结论走（判不了是「查看」，判得了是「验收」）', () => {
    const rows = {
      inbox: [{ ...anbanPending, business_date: '2026-09-13', review: JUDGE_DENIED }],
      deepInbox: [{ item_id: 30, item_name: '冰箱除霜', business_date: '2026-09-13', status: '待验收', review: JUDGE_DENIED }],
      shiftDue: '15:00',
      deepDue: '21:30',
    }
    const deniedQueue = buildWorkQueue(rows)
    expect(deniedQueue.map((task) => task.primaryLabel).sort()).toEqual(['查看', '查看'])
    const allowedQueue = buildWorkQueue({
      ...rows,
      inbox: [{ ...anbanPending, business_date: '2026-09-13', review: JUDGE_OK }],
      deepInbox: [{ item_id: 30, item_name: '冰箱除霜', business_date: '2026-09-13', status: '待验收', review: JUDGE_OK }],
    })
    expect(allowedQueue.map((task) => task.primaryLabel).sort()).toEqual(['验收', '验收'])
    // 文案表与判据对得上：`queueActionLabel` 是唯一的查表口。
    expect(queueActionLabel('daily', 'shoot')).toBe('拍摄')
    expect(queueActionLabel('deep', 'shoot')).toBe('拍前后')
    expect(queueActionLabel('fix', 'reshoot')).toBe('回拍')
    expect(queueActionLabel('fix', 'review')).toBe('验收')
    expect(queueActionLabel('deep', 'view')).toBe('查看')
    expect(queueActionLabel('daily', 'none')).toBe('')
    expect(queueActionLabel('nope', 'review')).toBe('')
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
          review: JUDGE_OK,
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
