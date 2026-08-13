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

  it('allocates a chunk’s 出餐 only against that chunk’s orders, not earlier siblings of the same dish', () => {
    const earlier = makeOrder({
      id: 'a',
      quantity: 10,
      order_time: '2026-07-23T01:00:00.000Z',
      table_number: '1'
    })
    const later = makeOrder({
      id: 'b',
      quantity: 8,
      order_time: '2026-07-23T02:00:00.000Z',
      table_number: '2'
    })

    const plan = planBatchCookingCalls({
      selectedQuantities: { '虾饺::later': 3 },
      pendingOrders: [earlier, later],
      chunkOrders: {
        '虾饺::earlier': { dishName: '虾饺', orders: [earlier] },
        '虾饺::later': { dishName: '虾饺', orders: [later] }
      }
    })

    expect(plan).toEqual([
      {
        dishName: '虾饺',
        completeQuantity: 3,
        orders: [later],
        allocations: [{ order: later, serveQuantity: 3 }]
      }
    ])
  })

  it('does not let two chunks of the same dish cross-allocate when both have selections', () => {
    const a = makeOrder({
      id: 'a',
      quantity: 6,
      order_time: '2026-07-23T01:00:00.000Z',
      table_number: '1'
    })
    const b = makeOrder({
      id: 'b',
      quantity: 6,
      order_time: '2026-07-23T02:00:00.000Z',
      table_number: '2'
    })
    const c = makeOrder({
      id: 'c',
      quantity: 6,
      order_time: '2026-07-23T03:00:00.000Z',
      table_number: '3'
    })
    const bInEarlier = { ...b, quantity: 4, served_quantity: 0, servedQuantity: 0 }
    const bInLater = { ...b, quantity: 2, served_quantity: 0, servedQuantity: 0 }

    const plan = planBatchCookingCalls({
      selectedQuantities: { '虾饺::earlier': 4, '虾饺::later': 4 },
      pendingOrders: [a, b, c],
      chunkOrders: {
        '虾饺::earlier': { dishName: '虾饺', orders: [a, bInEarlier] },
        '虾饺::later': { dishName: '虾饺', orders: [bInLater, c] }
      }
    })

    expect(plan).toHaveLength(2)
    expect(plan[0]).toEqual({
      dishName: '虾饺',
      completeQuantity: 4,
      orders: [a],
      allocations: [{ order: a, serveQuantity: 4 }]
    })
    expect(plan[1]).toEqual({
      dishName: '虾饺',
      completeQuantity: 4,
      orders: [bInLater, c],
      allocations: [
        { order: bInLater, serveQuantity: 2 },
        { order: c, serveQuantity: 2 }
      ]
    })
  })

  it('does not fall back to other same-dish orders when the selected chunk id is gone', () => {
    const earlier = makeOrder({
      id: 'a',
      quantity: 10,
      order_time: '2026-07-23T01:00:00.000Z'
    })

    expect(
      planBatchCookingCalls({
        selectedQuantities: { '虾饺::gone': 3 },
        pendingOrders: [earlier],
        chunkOrders: {
          '虾饺::earlier': { dishName: '虾饺', orders: [earlier] }
        }
      })
    ).toEqual([])
  })

  it('N=0-style chunk ids (dish name) still take earliest orders in that card', () => {
    const o1 = makeOrder({ id: '1', order_time: '2026-07-23T01:00:00.000Z', table_number: '1' })
    const o2 = makeOrder({ id: '2', order_time: '2026-07-23T02:00:00.000Z', table_number: '2' })
    const o3 = makeOrder({ id: '3', order_time: '2026-07-23T03:00:00.000Z', table_number: '3' })

    const plan = planBatchCookingCalls({
      selectedQuantities: { 虾饺: 2 },
      pendingOrders: [o3, o1, o2],
      chunkOrders: {
        虾饺: { dishName: '虾饺', orders: [o3, o1, o2] }
      }
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
})
