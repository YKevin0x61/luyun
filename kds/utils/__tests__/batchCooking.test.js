import { describe, expect, it } from 'vitest'
import { planBatchCookingCalls } from '../batchCooking.js'

function makeOrder(overrides = {}) {
  return {
    id: '1',
    dish_name: '虾饺',
    dish_status: '待出餐',
    quantity: 1,
    order_time: '2026-07-23T10:00:00.000Z',
    table_number: 'A1',
    station: 'shulong',
    ...overrides
  }
}

describe('planBatchCookingCalls', () => {
  it('merges selected portions of one dish into a single call using earliest orders', () => {
    const o1 = makeOrder({ id: '1', order_time: '2026-07-23T01:00:00.000Z', table_number: '1' })
    const o2 = makeOrder({ id: '2', order_time: '2026-07-23T02:00:00.000Z', table_number: '2' })
    const o3 = makeOrder({ id: '3', order_time: '2026-07-23T03:00:00.000Z', table_number: '3' })

    const plan = planBatchCookingCalls({
      selectedQuantities: { 虾饺: 2 },
      pendingOrders: [o3, o1, o2]
    })

    expect(plan).toEqual([
      {
        dishName: '虾饺',
        completeQuantity: 2,
        orders: [o1, o2],
        allocations: [
          { order: o1, serveQuantity: 1 },
          { order: o2, serveQuantity: 1 }
        ]
      }
    ])
  })

  it('takes multiple portions from an earlier multi-qty order before later ones', () => {
    const o1 = makeOrder({ id: '1', quantity: 2, order_time: '2026-07-23T01:00:00.000Z' })
    const o2 = makeOrder({ id: '2', quantity: 2, order_time: '2026-07-23T02:00:00.000Z' })

    const plan = planBatchCookingCalls({
      selectedQuantities: { 虾饺: 3 },
      pendingOrders: [o2, o1]
    })

    expect(plan).toEqual([
      {
        dishName: '虾饺',
        completeQuantity: 3,
        orders: [o1, o2],
        allocations: [
          { order: o1, serveQuantity: 2 },
          { order: o2, serveQuantity: 1 }
        ]
      }
    ])
  })

  it('returns a call per selected dish and skips zero/empty selection', () => {
    const shrimp = makeOrder({ id: '1', dish_name: '虾饺' })
    const bun = makeOrder({ id: '2', dish_name: '叉烧包', order_time: '2026-07-23T01:30:00.000Z' })

    const plan = planBatchCookingCalls({
      selectedQuantities: { 虾饺: 1, 叉烧包: 1, 烧卖: 0 },
      pendingOrders: [shrimp, bun]
    })

    expect(plan).toHaveLength(2)
    expect(plan.map((item) => item.dishName)).toEqual(['虾饺', '叉烧包'])
    expect(plan[0].completeQuantity).toBe(1)
    expect(plan[1].completeQuantity).toBe(1)
  })

  it('caps at available pending portions when selection exceeds stock', () => {
    const o1 = makeOrder({ id: '1', quantity: 1 })

    const plan = planBatchCookingCalls({
      selectedQuantities: { 虾饺: 5 },
      pendingOrders: [o1]
    })

    expect(plan).toEqual([
      {
        dishName: '虾饺',
        completeQuantity: 1,
        orders: [o1],
        allocations: [{ order: o1, serveQuantity: 1 }]
      }
    ])
  })

  it('returns empty plan when nothing can be fulfilled', () => {
    expect(
      planBatchCookingCalls({
        selectedQuantities: { 虾饺: 2 },
        pendingOrders: []
      })
    ).toEqual([])

    expect(
      planBatchCookingCalls({
        selectedQuantities: {},
        pendingOrders: [makeOrder()]
      })
    ).toEqual([])
  })
})
