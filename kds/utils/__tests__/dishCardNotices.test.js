import { describe, expect, it } from 'vitest'
import { planBatchCookingCalls, servePreviewOrderIds } from '../batchCooking.js'
import { composeKitchenDishCardsWithNotices, isDishCardCancelNotice } from '../dishCardNotices.js'

function pending(overrides = {}) {
  return {
    id: 'p1',
    dish_name: '虾饺',
    dish_status: '待出餐',
    quantity: 1,
    served_quantity: 0,
    order_time: '2026-08-18T10:00:00.000Z',
    table_number: '8',
    station: 'changfen',
    ...overrides
  }
}

function notice(overrides = {}) {
  return {
    id: 'n1',
    business_flow_id: 'flow-n1',
    dish_name: '虾饺',
    dish_status: '已取消',
    status: '退菜',
    quantity: 0,
    order_time: '2026-08-18T09:50:00.000Z',
    table_number: '3',
    station: 'changfen',
    ...overrides
  }
}

describe('isDishCardCancelNotice', () => {
  it('is true for unacked never-loaded 已取消 and false after ack or 抽走', () => {
    expect(isDishCardCancelNotice(notice())).toBe(true)
    expect(isDishCardCancelNotice(notice(), ['flow-n1'])).toBe(false)
    expect(
      isDishCardCancelNotice(notice({ placement: { steamer_id: '1', port_index: 1 } }))
    ).toBe(false)
    expect(isDishCardCancelNotice(notice({ loaded_at: '2026-08-18T11:00:00+08:00' }))).toBe(false)
    expect(isDishCardCancelNotice(pending())).toBe(false)
  })
})

describe('composeKitchenDishCardsWithNotices', () => {
  it('pins 退示 on the earliest card of that dish and does not consume the cap', () => {
    const logicalDishes = [
      {
        dishName: '虾饺',
        station: 'changfen',
        orders: [
          pending({ id: 'a', order_time: '2026-08-18T10:00:00.000Z' }),
          pending({ id: 'b', order_time: '2026-08-18T10:01:00.000Z' }),
          pending({ id: 'c', order_time: '2026-08-18T10:02:00.000Z' })
        ]
      }
    ]
    const { cards } = composeKitchenDishCardsWithNotices({
      logicalDishes,
      noticeOrders: [notice()],
      cap: 2,
      orderGapMinutes: 0
    })

    expect(cards.map((card) => card.totalQuantity)).toEqual([2, 1])
    expect(cards[0].noticeOrders.map((row) => row.id)).toEqual(['n1'])
    expect(cards[0].orders.map((row) => row.id)).toEqual(['a', 'b'])
    expect(cards.slice(1).every((card) => (card.noticeOrders || []).length === 0)).toBe(true)
    expect(cards[0].orders.every((row) => row.dish_status === '待出餐')).toBe(true)
  })

  it('shows two 退示 and one serveable 份 on the same dish card', () => {
    const { cards } = composeKitchenDishCardsWithNotices({
      logicalDishes: [
        {
          dishName: '虾饺',
          station: 'changfen',
          orders: [pending({ id: 'keep' })]
        }
      ],
      noticeOrders: [
        notice({ id: 'n1', business_flow_id: 'flow-n1' }),
        notice({ id: 'n2', business_flow_id: 'flow-n2' })
      ],
      cap: 0
    })
    expect(cards).toHaveLength(1)
    expect(cards[0].totalQuantity).toBe(1)
    expect(cards[0].orders.map((row) => row.id)).toEqual(['keep'])
    expect(cards[0].noticeOrders.map((row) => row.id)).toEqual(['n1', 'n2'])
  })

  it('keeps a notice-only dish as a card with 0 serveable 份', () => {
    const { cards } = composeKitchenDishCardsWithNotices({
      logicalDishes: [],
      noticeOrders: [notice()],
      cap: 10,
      orderGapMinutes: 0
    })

    expect(cards).toHaveLength(1)
    expect(cards[0].dishName).toBe('虾饺')
    expect(cards[0].totalQuantity).toBe(0)
    expect(cards[0].orders).toEqual([])
    expect(cards[0].noticeOrders.map((row) => row.id)).toEqual(['n1'])
    expect(cards[0].oldestTimestamp).toBe(Date.parse('2026-08-18T09:50:00.000Z'))
  })

  it('hides acked 退示 and keeps unacked ones after a simulated refresh', () => {
    const logicalDishes = [
      {
        dishName: '虾饺',
        station: 'changfen',
        orders: [pending({ id: 'a' })]
      }
    ]
    const notices = [
      notice({ id: 'n1', business_flow_id: 'flow-n1' }),
      notice({ id: 'n2', business_flow_id: 'flow-n2', table_number: '5' })
    ]

    const before = composeKitchenDishCardsWithNotices({
      logicalDishes,
      noticeOrders: notices,
      acknowledgedCancelIds: [],
      cap: 0
    })
    expect(before.cards[0].noticeOrders.map((row) => row.business_flow_id)).toEqual([
      'flow-n1',
      'flow-n2'
    ])

    const afterAck = composeKitchenDishCardsWithNotices({
      logicalDishes,
      noticeOrders: notices,
      acknowledgedCancelIds: ['flow-n1', 'flow-n2'],
      cap: 0
    })
    expect(afterAck.cards[0].noticeOrders).toEqual([])
    expect(afterAck.cards[0].totalQuantity).toBe(1)

    const afterReload = composeKitchenDishCardsWithNotices({
      logicalDishes,
      noticeOrders: notices,
      acknowledgedCancelIds: ['flow-n1'],
      cap: 0
    })
    expect(afterReload.cards[0].noticeOrders.map((row) => row.business_flow_id)).toEqual(['flow-n2'])
  })

  it('does not put notice ids into 将出 or complete-cooking allocations', () => {
    const serveable = pending({ id: 'a', order_time: '2026-08-18T10:01:00.000Z' })
    const cancelled = notice({
      id: 'n1',
      order_time: '2026-08-18T09:00:00.000Z',
      quantity: 0
    })
    const { cards } = composeKitchenDishCardsWithNotices({
      logicalDishes: [{ dishName: '虾饺', orders: [serveable] }],
      noticeOrders: [cancelled],
      cap: 0
    })
    const card = cards[0]
    expect(servePreviewOrderIds([...card.orders, ...card.noticeOrders], 1)).toEqual(['a'])

    const plan = planBatchCookingCalls({
      selectedQuantities: { [card.chunkId]: 1 },
      pendingOrders: [...card.orders, ...card.noticeOrders],
      chunkOrders: {
        [card.chunkId]: { dishName: card.dishName, orders: card.orders }
      }
    })
    expect(plan[0].orders.map((row) => row.id)).toEqual(['a'])
    expect(plan[0].allocations.every((item) => item.order.dish_status !== '已取消')).toBe(true)
  })

  it('pins 退示 onto the matching 菜名+备注 card, not another remark of the same dish', () => {
    const { cards } = composeKitchenDishCardsWithNotices({
      logicalDishes: [
        {
          dishName: '艇仔粥',
          notes: '',
          station: 'changfen',
          orders: [pending({ id: 'p1', dish_name: '艇仔粥', notes: '' })]
        },
        {
          dishName: '艇仔粥',
          notes: '免葱',
          station: 'changfen',
          orders: [pending({ id: 'o1', dish_name: '艇仔粥', notes: '免葱' })]
        }
      ],
      noticeOrders: [
        notice({ id: 'n1', dish_name: '艇仔粥', notes: '免葱' })
      ],
      cap: 0
    })
    const onion = cards.find((card) => card.notes === '免葱')
    const plain = cards.find((card) => card.notes === '')
    expect(onion.noticeOrders.map((row) => row.id)).toEqual(['n1'])
    expect(plain.noticeOrders).toEqual([])
    expect(onion.dishName).toBe('艇仔粥')
    expect(onion.notes).toBe('免葱')
  })

  it('keeps a notice-only 外卖平台: 退示 on the empty-notes card, not a platform card', () => {
    const { cards } = composeKitchenDishCardsWithNotices({
      logicalDishes: [],
      noticeOrders: [
        notice({
          id: 'n1',
          dish_name: '艇仔粥',
          notes: '外卖平台:美团|来源:美团1'
        })
      ],
      cap: 0
    })
    expect(cards).toHaveLength(1)
    expect(cards[0].dishName).toBe('艇仔粥')
    expect(cards[0].notes).toBe('')
    expect(cards[0].noticeOrders.map((row) => row.id)).toEqual(['n1'])
  })
})
