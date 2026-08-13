import { describe, expect, it } from 'vitest'
import { composeKitchenDishCards, reconcileDishChunks } from '../dishCardChunks.js'

function makeOrder(overrides = {}) {
  return {
    id: '1',
    dish_name: '虾饺',
    dish_status: '待出餐',
    quantity: 1,
    served_quantity: 0,
    order_time: '2026-07-23T10:00:00.000Z',
    table_number: 'A1',
    station: 'shulong',
    ...overrides
  }
}

function chunkOrderIds(chunk) {
  return chunk.orders.map((order) => String(order.id))
}

function chunkPortions(chunk) {
  return chunk.orders.map((order) => ({
    id: String(order.id),
    quantity: order.quantity
  }))
}

describe('reconcileDishChunks', () => {
  it('N=0 keeps one card with every pending order (no split)', () => {
    const orders = [
      makeOrder({ id: 'c', quantity: 8, order_time: '2026-07-23T12:00:00.000Z' }),
      makeOrder({ id: 'a', quantity: 10, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 'b', quantity: 7, order_time: '2026-07-23T11:00:00.000Z' })
    ]

    const chunks = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: orders,
      cap: 0,
      previousChunks: []
    })

    expect(chunks).toHaveLength(1)
    expect(chunks[0].dishName).toBe('虾饺')
    expect(chunks[0].totalQuantity).toBe(25)
    expect(chunkOrderIds(chunks[0])).toEqual(['a', 'b', 'c'])
  })

  it('N=0 keeps raw order quantity (does not subtract served)', () => {
    const chunks = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: [
        makeOrder({ id: 'a', quantity: 10, served_quantity: 4, order_time: '2026-07-23T10:00:00.000Z' })
      ],
      cap: 0,
      previousChunks: []
    })

    expect(chunks).toHaveLength(1)
    expect(chunks[0].totalQuantity).toBe(10)
    expect(chunks[0].orders[0].quantity).toBe(10)
    expect(chunks[0].orders[0].served_quantity).toBe(4)
  })

  it('FIFO-fills chunks of at most N, splitting an order that straddles the cap', () => {
    const orders = [
      makeOrder({ id: 'a', quantity: 6, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 'b', quantity: 6, order_time: '2026-07-23T11:00:00.000Z' }),
      makeOrder({ id: 'c', quantity: 6, order_time: '2026-07-23T12:00:00.000Z' })
    ]

    const chunks = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: orders,
      cap: 10,
      previousChunks: []
    })

    expect(chunks.map((chunk) => chunk.dishName)).toEqual(['虾饺', '虾饺'])
    expect(chunks.map((chunk) => chunk.totalQuantity)).toEqual([10, 8])
    expect(chunkPortions(chunks[0])).toEqual([
      { id: 'a', quantity: 6 },
      { id: 'b', quantity: 4 }
    ])
    expect(chunkPortions(chunks[1])).toEqual([
      { id: 'b', quantity: 2 },
      { id: 'c', quantity: 6 }
    ])
    expect(chunks[0].oldestTimestamp).toBe(Date.parse('2026-07-23T10:00:00.000Z'))
    expect(chunks[1].oldestTimestamp).toBe(Date.parse('2026-07-23T11:00:00.000Z'))
  })

  it('keeps remaining members on their chunk when an earlier chunk shrinks (no pack-left)', () => {
    const initial = [
      makeOrder({ id: 'a', quantity: 10, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 'b', quantity: 5, order_time: '2026-07-23T11:00:00.000Z' })
    ]
    const first = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: initial,
      cap: 10,
      previousChunks: []
    })
    expect(first.map((chunk) => chunk.totalQuantity)).toEqual([10, 5])

    const afterShrink = [
      makeOrder({ id: 'a', quantity: 10, served_quantity: 5, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 'b', quantity: 5, order_time: '2026-07-23T11:00:00.000Z' })
    ]
    const second = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: afterShrink,
      cap: 10,
      previousChunks: first
    })

    expect(second).toHaveLength(2)
    expect(second.map((chunk) => chunk.chunkId)).toEqual(first.map((chunk) => chunk.chunkId))
    expect(chunkPortions(second[0])).toEqual([{ id: 'a', quantity: 5 }])
    expect(chunkPortions(second[1])).toEqual([{ id: 'b', quantity: 5 }])
  })

  it('appends a new order onto the last underfilled chunk instead of packing left', () => {
    const initial = [
      makeOrder({ id: 'a', quantity: 10, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 'b', quantity: 5, order_time: '2026-07-23T11:00:00.000Z' })
    ]
    const first = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: initial,
      cap: 10,
      previousChunks: []
    })
    const afterShrinkAndArrival = [
      makeOrder({ id: 'a', quantity: 5, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 'b', quantity: 5, order_time: '2026-07-23T11:00:00.000Z' }),
      makeOrder({ id: 'c', quantity: 3, order_time: '2026-07-23T12:00:00.000Z' })
    ]
    const second = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: afterShrinkAndArrival,
      cap: 10,
      previousChunks: first
    })

    expect(second).toHaveLength(2)
    expect(second[0].chunkId).toBe(first[0].chunkId)
    expect(second[1].chunkId).toBe(first[1].chunkId)
    expect(chunkPortions(second[0])).toEqual([{ id: 'a', quantity: 5 }])
    expect(chunkPortions(second[1])).toEqual([
      { id: 'b', quantity: 5 },
      { id: 'c', quantity: 3 }
    ])
  })

  it('drops a chunk as soon as it has no remaining portions and keeps the later chunk id', () => {
    const initial = [
      makeOrder({ id: 'a', quantity: 10, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 'b', quantity: 5, order_time: '2026-07-23T11:00:00.000Z' })
    ]
    const first = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: initial,
      cap: 10,
      previousChunks: []
    })
    const afterDrop = [
      makeOrder({ id: 'b', quantity: 5, order_time: '2026-07-23T11:00:00.000Z' })
    ]
    const second = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: afterDrop,
      cap: 10,
      previousChunks: first
    })

    expect(second).toHaveLength(1)
    expect(second[0].chunkId).toBe(first[1].chunkId)
    expect(chunkPortions(second[0])).toEqual([{ id: 'b', quantity: 5 }])
  })

  it('opens a new chunk when the last chunk is already full', () => {
    const initial = [
      makeOrder({ id: 'a', quantity: 10, order_time: '2026-07-23T10:00:00.000Z' })
    ]
    const first = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: initial,
      cap: 10,
      previousChunks: []
    })
    const withArrival = [
      makeOrder({ id: 'a', quantity: 10, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 'b', quantity: 4, order_time: '2026-07-23T11:00:00.000Z' })
    ]
    const second = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: withArrival,
      cap: 10,
      previousChunks: first
    })

    expect(second).toHaveLength(2)
    expect(second[0].chunkId).toBe(first[0].chunkId)
    expect(second[0].chunkId).not.toBe(second[1].chunkId)
    expect(chunkPortions(second[0])).toEqual([{ id: 'a', quantity: 10 }])
    expect(chunkPortions(second[1])).toEqual([{ id: 'b', quantity: 4 }])
  })

  it('fills the last under-N chunk when a later chunk is already full', () => {
    const initial = [
      makeOrder({ id: 'a', quantity: 10, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 'b', quantity: 10, order_time: '2026-07-23T11:00:00.000Z' })
    ]
    const first = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: initial,
      cap: 10,
      previousChunks: []
    })
    const afterShrinkAndArrival = [
      makeOrder({ id: 'a', quantity: 5, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 'b', quantity: 10, order_time: '2026-07-23T11:00:00.000Z' }),
      makeOrder({ id: 'c', quantity: 3, order_time: '2026-07-23T12:00:00.000Z' })
    ]
    const second = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: afterShrinkAndArrival,
      cap: 10,
      previousChunks: first
    })

    expect(second).toHaveLength(2)
    expect(second[0].chunkId).toBe(first[0].chunkId)
    expect(second[1].chunkId).toBe(first[1].chunkId)
    expect(chunkPortions(second[0])).toEqual([
      { id: 'a', quantity: 5 },
      { id: 'c', quantity: 3 }
    ])
    expect(chunkPortions(second[1])).toEqual([{ id: 'b', quantity: 10 }])
  })

  it.each([
    {
      name: 'total portions below N stay on one card',
      cap: 10,
      quantities: [3, 4],
      totals: [7]
    },
    {
      name: 'exact N boundary makes full chunks and no empty extra card',
      cap: 10,
      quantities: [10, 10],
      totals: [10, 10]
    },
    {
      name: 'N=1 puts each portion on its own card',
      cap: 1,
      quantities: [1, 1, 1],
      totals: [1, 1, 1]
    }
  ])('$name', ({ cap, quantities, totals }) => {
    const orders = quantities.map((quantity, index) =>
      makeOrder({
        id: String.fromCharCode(97 + index),
        quantity,
        order_time: `2026-07-23T1${index}:00:00.000Z`
      })
    )
    const chunks = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: orders,
      cap,
      previousChunks: []
    })
    expect(chunks.map((chunk) => chunk.totalQuantity)).toEqual(totals)
    expect(chunks.every((chunk) => chunk.totalQuantity > 0)).toBe(true)
    expect(chunks.every((chunk) => chunk.dishName === '虾饺')).toBe(true)
  })
})

describe('composeKitchenDishCards', () => {
  it('emits same-dish chunks adjacent in FIFO order after the caller’s logical-dish sort', () => {
    const shrimp = [
      makeOrder({ id: 's1', dish_name: '虾饺', quantity: 10, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 's2', dish_name: '虾饺', quantity: 8, order_time: '2026-07-23T11:00:00.000Z' })
    ]
    const bun = [
      makeOrder({ id: 'b1', dish_name: '叉烧包', quantity: 3, order_time: '2026-07-23T09:00:00.000Z' })
    ]
    const logicalDishes = [
      { dishName: '叉烧包', station: 'shulong', orders: bun, totalQuantity: 3 },
      { dishName: '虾饺', station: 'shulong', orders: shrimp, totalQuantity: 18 }
    ]

    const { cards, previousByDish } = composeKitchenDishCards({
      logicalDishes,
      cap: 10,
      previousByDish: {}
    })

    expect(cards.map((card) => card.dishName)).toEqual(['叉烧包', '虾饺', '虾饺'])
    expect(cards.map((card) => card.totalQuantity)).toEqual([3, 10, 8])
    expect(cards[1].chunkId).not.toBe(cards[2].chunkId)
    expect(chunkOrderIds(cards[1])).toEqual(['s1'])
    expect(chunkOrderIds(cards[2])).toEqual(['s2'])

    const again = composeKitchenDishCards({
      logicalDishes,
      cap: 10,
      previousByDish
    })
    expect(again.cards.map((card) => card.chunkId)).toEqual(cards.map((card) => card.chunkId))
  })

  it('N=0 emits one card per logical dish using the dish name as chunk id', () => {
    const { cards } = composeKitchenDishCards({
      logicalDishes: [
        {
          dishName: '虾饺',
          station: 'shulong',
          orders: [
            makeOrder({ id: 'a', quantity: 12, order_time: '2026-07-23T10:00:00.000Z' })
          ],
          totalQuantity: 12
        }
      ],
      cap: 0,
      previousByDish: {}
    })

    expect(cards).toHaveLength(1)
    expect(cards[0].chunkId).toBe('虾饺')
    expect(cards[0].dishName).toBe('虾饺')
    expect(cards[0].totalQuantity).toBe(12)
  })
})

