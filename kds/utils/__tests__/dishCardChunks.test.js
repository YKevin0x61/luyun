import { describe, expect, it } from 'vitest'
import { composeKitchenDishCards, dishSplitKnobsChanged, reconcileDishChunks, sortKitchenDishCardsByOldest } from '../dishCardChunks.js'
import { isPendingKitchenWork } from '../pendingKitchenWork.js'

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

describe('composeKitchenDishCards 等叫', () => {
  it('does not put 等叫 on 菜卡 when given 待出餐工作', () => {
    const pending = [
      makeOrder({ id: 'work' }),
      makeOrder({ id: 'held', is_hold: true })
    ].filter(isPendingKitchenWork)
    const { cards } = composeKitchenDishCards({
      logicalDishes: [{ dishName: '虾饺', orders: pending }],
      cap: 0
    })
    expect(cards).toHaveLength(1)
    expect(chunkOrderIds(cards[0])).toEqual(['work'])
  })
})

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

  it('FIFO-fills by 进入待出餐工作时刻 so a late 叫起 does not cut in front', () => {
    const orders = [
      makeOrder({
        id: 'fired',
        quantity: 6,
        order_time: '2026-07-23T09:00:00.000Z',
        fired_at: '2026-07-23T11:00:00.000Z'
      }),
      makeOrder({ id: 'fresh', quantity: 6, order_time: '2026-07-23T10:00:00.000Z' })
    ]

    const chunks = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: orders,
      cap: 10,
      previousChunks: []
    })

    expect(chunks.map((chunk) => chunk.totalQuantity)).toEqual([10, 2])
    expect(chunkPortions(chunks[0])).toEqual([
      { id: 'fresh', quantity: 6 },
      { id: 'fired', quantity: 4 }
    ])
    expect(chunkPortions(chunks[1])).toEqual([{ id: 'fired', quantity: 2 }])
    expect(chunks[0].oldestTimestamp).toBe(Date.parse('2026-07-23T10:00:00.000Z'))
    expect(chunks[1].oldestTimestamp).toBe(Date.parse('2026-07-23T11:00:00.000Z'))
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

  it('opens a new chunk when the tail is full, even if an earlier chunk has room', () => {
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

    expect(second).toHaveLength(3)
    expect(second[0].chunkId).toBe(first[0].chunkId)
    expect(second[1].chunkId).toBe(first[1].chunkId)
    expect(chunkPortions(second[0])).toEqual([{ id: 'a', quantity: 5 }])
    expect(chunkPortions(second[1])).toEqual([{ id: 'b', quantity: 10 }])
    expect(chunkPortions(second[2])).toEqual([{ id: 'c', quantity: 3 }])
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
  it('flattens chunks without keeping same-dish cards adjacent; sort is by each chunk’s oldest order', () => {
    const shrimp = [
      makeOrder({ id: 's1', dish_name: '虾饺', quantity: 10, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 's2', dish_name: '虾饺', quantity: 8, order_time: '2026-07-23T11:00:00.000Z' })
    ]
    const bun = [
      makeOrder({ id: 'b1', dish_name: '叉烧包', quantity: 3, order_time: '2026-07-23T10:30:00.000Z' })
    ]
    const logicalDishes = [
      { dishName: '虾饺', station: 'shulong', orders: shrimp, totalQuantity: 18 },
      { dishName: '叉烧包', station: 'shulong', orders: bun, totalQuantity: 3 }
    ]

    const { cards, previousByDish } = composeKitchenDishCards({
      logicalDishes,
      cap: 10,
      previousByDish: {}
    })
    const sorted = sortKitchenDishCardsByOldest(cards)

    expect(sorted.map((card) => card.dishName)).toEqual(['虾饺', '叉烧包', '虾饺'])
    expect(sorted.map((card) => card.totalQuantity)).toEqual([10, 3, 8])
    expect(chunkOrderIds(sorted[0])).toEqual(['s1'])
    expect(chunkOrderIds(sorted[1])).toEqual(['b1'])
    expect(chunkOrderIds(sorted[2])).toEqual(['s2'])

    const again = composeKitchenDishCards({
      logicalDishes,
      cap: 10,
      previousByDish
    })
    expect(sortKitchenDishCardsByOldest(again.cards).map((card) => card.chunkId)).toEqual(
      sorted.map((card) => card.chunkId)
    )
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

describe('reconcileDishChunks 下单间隔', () => {
  it('T≥1 splits when FIFO-adjacent 下单时间 gap is exactly T minutes', () => {
    const chunks = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: [
        makeOrder({ id: 'a', quantity: 4, order_time: '2026-07-23T10:00:00.000Z' }),
        makeOrder({ id: 'b', quantity: 3, order_time: '2026-07-23T10:10:00.000Z' })
      ],
      cap: 0,
      orderGapMinutes: 10,
      previousChunks: []
    })

    expect(chunks).toHaveLength(2)
    expect(chunks.map((chunk) => chunk.dishName)).toEqual(['虾饺', '虾饺'])
    expect(chunkPortions(chunks[0])).toEqual([{ id: 'a', quantity: 4 }])
    expect(chunkPortions(chunks[1])).toEqual([{ id: 'b', quantity: 3 }])
    expect(chunks[0].chunkId).not.toBe(chunks[1].chunkId)
  })

  it('菜卡份数上限 still slices inside one 浪潮', () => {
    const chunks = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: [
        makeOrder({ id: 'a', quantity: 12, order_time: '2026-07-23T10:00:00.000Z' }),
        makeOrder({ id: 'b', quantity: 3, order_time: '2026-07-23T10:08:00.000Z' })
      ],
      cap: 10,
      orderGapMinutes: 10,
      previousChunks: []
    })

    expect(chunks.map((chunk) => chunk.totalQuantity)).toEqual([10, 5])
    expect(chunkPortions(chunks[0])).toEqual([{ id: 'a', quantity: 10 }])
    expect(chunkPortions(chunks[1])).toEqual([
      { id: 'a', quantity: 2 },
      { id: 'b', quantity: 3 }
    ])
  })

  it('T=10 keeps 10:00 / 10:08 / 10:16 on one 浪潮 card (gaps under T)', () => {
    const chunks = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: [
        makeOrder({ id: 'a', quantity: 2, order_time: '2026-07-23T10:00:00.000Z' }),
        makeOrder({ id: 'b', quantity: 2, order_time: '2026-07-23T10:08:00.000Z' }),
        makeOrder({ id: 'c', quantity: 2, order_time: '2026-07-23T10:16:00.000Z' })
      ],
      cap: 0,
      orderGapMinutes: 10,
      previousChunks: []
    })

    expect(chunks).toHaveLength(1)
    expect(chunkPortions(chunks[0])).toEqual([
      { id: 'a', quantity: 2 },
      { id: 'b', quantity: 2 },
      { id: 'c', quantity: 2 }
    ])
    expect(chunks[0].totalQuantity).toBe(6)
  })

  it('N and T together: later 浪潮 does not share a card even when under the portion cap', () => {
    const chunks = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: [
        makeOrder({ id: 'a', quantity: 5, order_time: '2026-07-23T10:00:00.000Z' }),
        makeOrder({ id: 'b', quantity: 5, order_time: '2026-07-23T10:08:00.000Z' }),
        makeOrder({ id: 'c', quantity: 5, order_time: '2026-07-23T10:20:00.000Z' })
      ],
      cap: 10,
      orderGapMinutes: 10,
      previousChunks: []
    })

    expect(chunks.map((chunk) => chunk.totalQuantity)).toEqual([10, 5])
    expect(chunkPortions(chunks[0])).toEqual([
      { id: 'a', quantity: 5 },
      { id: 'b', quantity: 5 }
    ])
    expect(chunkPortions(chunks[1])).toEqual([{ id: 'c', quantity: 5 }])
  })

  it('does not fill an earlier 浪潮 portion hole with a later 浪潮 arrival', () => {
    const initial = [
      makeOrder({ id: 'a', quantity: 10, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 'c', quantity: 5, order_time: '2026-07-23T10:28:00.000Z' })
    ]
    const first = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: initial,
      cap: 10,
      orderGapMinutes: 10,
      previousChunks: []
    })
    expect(first.map((chunk) => chunk.totalQuantity)).toEqual([10, 5])

    const afterHoleAndArrival = [
      makeOrder({ id: 'a', quantity: 10, served_quantity: 3, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 'c', quantity: 5, order_time: '2026-07-23T10:28:00.000Z' }),
      makeOrder({ id: 'd', quantity: 3, order_time: '2026-07-23T10:30:00.000Z' })
    ]
    const second = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: afterHoleAndArrival,
      cap: 10,
      orderGapMinutes: 10,
      previousChunks: first
    })

    expect(second[0].chunkId).toBe(first[0].chunkId)
    expect(second[1].chunkId).toBe(first[1].chunkId)
    expect(chunkPortions(second[0])).toEqual([{ id: 'a', quantity: 7 }])
    expect(chunkPortions(second[1])).toEqual([
      { id: 'c', quantity: 5 },
      { id: 'd', quantity: 3 }
    ])
  })

  it('backfill 10:06 joins the early 浪潮 instead of the 10:28 card', () => {
    const initial = [
      makeOrder({ id: 'a', quantity: 4, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 'b', quantity: 4, order_time: '2026-07-23T10:08:00.000Z' }),
      makeOrder({ id: 'c', quantity: 4, order_time: '2026-07-23T10:16:00.000Z' }),
      makeOrder({ id: 'd', quantity: 2, order_time: '2026-07-23T10:28:00.000Z' })
    ]
    const first = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: initial,
      cap: 0,
      orderGapMinutes: 10,
      previousChunks: []
    })
    expect(first).toHaveLength(2)
    expect(chunkOrderIds(first[0])).toEqual(['a', 'b', 'c'])
    expect(chunkOrderIds(first[1])).toEqual(['d'])

    const withBackfill = [
      ...initial,
      makeOrder({ id: 'e', quantity: 3, order_time: '2026-07-23T10:06:00.000Z' })
    ]
    const second = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: withBackfill,
      cap: 0,
      orderGapMinutes: 10,
      previousChunks: first
    })

    expect(second[0].chunkId).toBe(first[0].chunkId)
    expect(second[1].chunkId).toBe(first[1].chunkId)
    expect(chunkPortions(second[0])).toEqual([
      { id: 'a', quantity: 4 },
      { id: 'b', quantity: 4 },
      { id: 'c', quantity: 4 },
      { id: 'e', quantity: 3 }
    ])
    expect(chunkPortions(second[1])).toEqual([{ id: 'd', quantity: 2 }])
  })

  it('opens a new card in the early 浪潮 when that 浪潮 is already at N', () => {
    const initial = [
      makeOrder({ id: 'a', quantity: 10, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 'b', quantity: 10, order_time: '2026-07-23T10:08:00.000Z' }),
      makeOrder({ id: 'c', quantity: 5, order_time: '2026-07-23T10:28:00.000Z' })
    ]
    const first = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: initial,
      cap: 10,
      orderGapMinutes: 10,
      previousChunks: []
    })
    expect(first.map((chunk) => chunk.totalQuantity)).toEqual([10, 10, 5])

    const withBackfill = [
      ...initial,
      makeOrder({ id: 'e', quantity: 3, order_time: '2026-07-23T10:06:00.000Z' })
    ]
    const second = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: withBackfill,
      cap: 10,
      orderGapMinutes: 10,
      previousChunks: first
    })

    expect(second).toHaveLength(4)
    expect(second[0].chunkId).toBe(first[0].chunkId)
    expect(second[1].chunkId).toBe(first[1].chunkId)
    expect(second[2].chunkId).not.toBe(first[0].chunkId)
    expect(second[2].chunkId).not.toBe(first[1].chunkId)
    expect(second[3].chunkId).toBe(first[2].chunkId)
    expect(chunkPortions(second[0])).toEqual([{ id: 'a', quantity: 10 }])
    expect(chunkPortions(second[1])).toEqual([{ id: 'b', quantity: 10 }])
    expect(chunkPortions(second[2])).toEqual([{ id: 'e', quantity: 3 }])
    expect(chunkPortions(second[3])).toEqual([{ id: 'c', quantity: 5 }])
  })

  it('omitted orderGapMinutes matches T=0 portion-cap behavior', () => {
    const orders = [
      makeOrder({ id: 'a', quantity: 10, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 'b', quantity: 5, order_time: '2026-07-23T11:00:00.000Z' })
    ]
    const withZero = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: orders,
      cap: 10,
      orderGapMinutes: 0,
      previousChunks: []
    })
    const omitted = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: orders,
      cap: 10,
      previousChunks: []
    })
    expect(omitted.map((chunk) => chunk.totalQuantity)).toEqual(withZero.map((chunk) => chunk.totalQuantity))
    expect(chunkPortions(omitted[0])).toEqual(chunkPortions(withZero[0]))
    expect(chunkPortions(omitted[1])).toEqual(chunkPortions(withZero[1]))
  })
})

describe('composeKitchenDishCards 下单间隔', () => {
  it('passes T through so same-dish 浪潮 cards interleave by each card’s oldest order', () => {
    const shrimp = [
      makeOrder({ id: 's1', dish_name: '虾饺', quantity: 4, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({ id: 's2', dish_name: '虾饺', quantity: 4, order_time: '2026-07-23T10:20:00.000Z' })
    ]
    const bun = [
      makeOrder({ id: 'b1', dish_name: '叉烧包', quantity: 2, order_time: '2026-07-23T10:10:00.000Z' })
    ]
    const { cards } = composeKitchenDishCards({
      logicalDishes: [
        { dishName: '虾饺', station: 'shulong', orders: shrimp, totalQuantity: 8 },
        { dishName: '叉烧包', station: 'shulong', orders: bun, totalQuantity: 2 }
      ],
      cap: 0,
      orderGapMinutes: 10,
      previousByDish: {}
    })
    const sorted = sortKitchenDishCardsByOldest(cards)

    expect(sorted.map((card) => card.dishName)).toEqual(['虾饺', '叉烧包', '虾饺'])
    expect(chunkOrderIds(sorted[0])).toEqual(['s1'])
    expect(chunkOrderIds(sorted[1])).toEqual(['b1'])
    expect(chunkOrderIds(sorted[2])).toEqual(['s2'])
  })

  it('splits 浪潮 by 进入待出餐工作时刻 so a late 叫起 does not join an old wave', () => {
    const orders = [
      makeOrder({ id: 'old', quantity: 1, order_time: '2026-07-23T10:00:00.000Z' }),
      makeOrder({
        id: 'fired',
        quantity: 1,
        order_time: '2026-07-23T10:00:00.000Z',
        fired_at: '2026-07-23T10:20:00.000Z'
      })
    ]
    const chunks = reconcileDishChunks({
      dishName: '虾饺',
      pendingOrders: orders,
      cap: 0,
      orderGapMinutes: 10,
      previousChunks: []
    })
    expect(chunks).toHaveLength(2)
    expect(chunkOrderIds(chunks[0])).toEqual(['old'])
    expect(chunkOrderIds(chunks[1])).toEqual(['fired'])
  })
})

describe('dishSplitKnobsChanged', () => {
  it('is false on first run and when both knobs are unchanged', () => {
    expect(dishSplitKnobsChanged(null, 10, 5)).toBe(false)
    expect(dishSplitKnobsChanged({ cap: 10, orderGapMinutes: 5 }, 10, 5)).toBe(false)
  })

  it('is true when 菜卡份数上限 or 下单间隔 changes', () => {
    expect(dishSplitKnobsChanged({ cap: 10, orderGapMinutes: 5 }, 8, 5)).toBe(true)
    expect(dishSplitKnobsChanged({ cap: 10, orderGapMinutes: 5 }, 10, 0)).toBe(true)
  })
})

