import { describe, expect, it } from 'vitest'
import {
  compareRushThenFifo,
  isPendingKitchenWork,
  workEnterTimeMs
} from '../pendingKitchenWork.js'

describe('pendingKitchenWork', () => {
  it('treats 等叫 as not 待出餐工作', () => {
    expect(
      isPendingKitchenWork({ dish_status: '待出餐', is_hold: true, quantity: 1 })
    ).toBe(false)
    expect(isPendingKitchenWork({ dish_status: '待出餐', quantity: 1 })).toBe(true)
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
})
