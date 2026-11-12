import { describe, expect, it } from 'vitest'
import { REVIEW_CLOCK_FALLBACK, expectedSnapshotSlots } from '../reviewDay.js'

/**
 * 复盘日的 JS 薄对应（ADR 0140）。
 *
 * 预期整点槽跟 `context.runtime.open/close` 走，不再硬编码 07:00–21:00：
 * 缺省 07:00–21:30 与 08:00–22:00 都是 15 个槽，所以「按哪个时段算」必须靠
 * 「今天已过几个整点」这类会随 open 移动的断言来钉，光看总数分不出来。
 */
describe('expectedSnapshotSlots', () => {
  it('过去的一天：按 07:00–21:30 算 15 个整点槽', () => {
    expect(expectedSnapshotSlots({ runtime: { open: '07:00', close: '21:30' } })).toBe(15)
  })

  it('今天 10 点（07:00 开门）：只算已过去的 4 个整点', () => {
    const runtime = { open: '07:00', close: '21:30' }
    expect(expectedSnapshotSlots({ runtime, isToday: true, nowHour: 10 })).toBe(4)
  })

  it('营业时段 08:00–22:00：仍然是 15 个槽，且今天 10 点只算 3 个', () => {
    const runtime = { open: '08:00', close: '22:00' }
    expect(expectedSnapshotSlots({ runtime })).toBe(15)
    // 按旧的 07:00 起算会得 4；这里必须是 3，才说明槽位真的跟 runtime 走了。
    expect(expectedSnapshotSlots({ runtime, isToday: true, nowHour: 10 })).toBe(3)
  })

  it('runtime 缺失或坏值：兜底 07:00–21:30', () => {
    expect(expectedSnapshotSlots()).toBe(15)
    expect(expectedSnapshotSlots({ runtime: null })).toBe(15)
    expect(expectedSnapshotSlots({ runtime: {} })).toBe(15)
    expect(expectedSnapshotSlots({ runtime: { open: '25:00', close: 'bad' } })).toBe(15)
  })

  it('开门非整点（07:30）：首槽 08:00，共 14 个；今天 10 点只算 3 个', () => {
    const runtime = { open: '07:30', close: '21:30' }
    expect(expectedSnapshotSlots({ runtime })).toBe(14)
    expect(expectedSnapshotSlots({ runtime, isToday: true, nowHour: 10 })).toBe(3)
  })

  it('打烊 21:00（营业到 21:00 整）：floor(21) - 7 + 1 = 15', () => {
    expect(expectedSnapshotSlots({ runtime: { open: '07:00', close: '21:00' } })).toBe(15)
  })

  it('打烊不晚于开门（跨零点 / 坏配置）：兜底 07:00–21:30 = 15', () => {
    expect(expectedSnapshotSlots({ runtime: { open: '21:00', close: '02:00' } })).toBe(15)
    expect(expectedSnapshotSlots({ runtime: { open: '08:00', close: '08:00' } })).toBe(15)
  })

  it('今天还没开门（nowHour 在 open 之前）：至少算 1 个槽', () => {
    const runtime = { open: '08:00', close: '22:00' }
    expect(expectedSnapshotSlots({ runtime, isToday: true, nowHour: 6 })).toBe(1)
  })

  it('今天 nowHour 缺失 / 非数字：按 1 个槽算，不误报覆盖', () => {
    const runtime = { open: '07:00', close: '21:30' }
    expect(expectedSnapshotSlots({ runtime, isToday: true })).toBe(1)
    expect(expectedSnapshotSlots({ runtime, isToday: true, nowHour: 'nope' })).toBe(1)
  })

  it('主口径兜底常量与后端 REVIEW_CLOCK 同值', () => {
    expect(REVIEW_CLOCK_FALLBACK).toBe('07:00')
  })
})
