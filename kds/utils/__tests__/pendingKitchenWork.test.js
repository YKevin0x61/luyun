import { describe, expect, it } from 'vitest'
import {
  compareRushThenFifo,
  isPendingKitchenWork,
  isRushed,
  workEnterTimeMs
} from '../pendingKitchenWork.js'

describe('pendingKitchenWork', () => {
  it('treats 等叫 as not 待出餐工作', () => {
    expect(
      isPendingKitchenWork({ dish_status: '待出餐', is_hold: true, quantity: 1 })
    ).toBe(false)
    expect(isPendingKitchenWork({ dish_status: '待出餐', quantity: 1 })).toBe(true)
  })

  it('uses 下单时间 as 进入待出餐工作时刻 when never 叫起', () => {
    const order = { order_time: '2026-08-18T10:00:00+08:00' }
    expect(workEnterTimeMs(order)).toBe(Date.parse('2026-08-18T10:00:00+08:00'))
  })

  it('uses 叫起时刻 as 进入待出餐工作时刻', () => {
    const order = {
      order_time: '2026-08-18T10:00:00+08:00',
      fired_at: '2026-08-18T10:20:00+08:00'
    }
    expect(workEnterTimeMs(order)).toBe(new Date(order.fired_at).getTime())
  })

  it('sorts 加急 before non-rush, then by work-enter time', () => {
    const early = { id: 'a', order_time: '2026-08-18T10:00:00+08:00', is_rushed: false }
    const rushedLate = { id: 'b', order_time: '2026-08-18T10:30:00+08:00', is_rushed: true }
    expect([early, rushedLate].sort(compareRushThenFifo).map((row) => row.id)).toEqual(['b', 'a'])
  })

  it('does not treat notes containing 催 as 加急 without is_rushed', () => {
    expect(isRushed({ notes: '催菜', is_rushed: false })).toBe(false)
    expect(isRushed({ notes: '催一下', is_rushed: 0 })).toBe(false)
    const early = { id: 'a', order_time: '2026-08-18T10:00:00+08:00', notes: '催菜', is_rushed: false }
    const later = { id: 'b', order_time: '2026-08-18T10:30:00+08:00', notes: '催菜', is_rushed: false }
    expect([later, early].sort(compareRushThenFifo).map((row) => row.id)).toEqual(['a', 'b'])
  })
})
