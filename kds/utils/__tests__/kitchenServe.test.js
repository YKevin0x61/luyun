import { describe, expect, it } from 'vitest'
import {
  applyServeSelection,
  confirmBasketServe,
  confirmCardServe,
  confirmTablePickServe,
  emptyServeSelection
} from '../kitchenServe.js'

function makeOrder(overrides = {}) {
  const order = {
    id: '1',
    dish_name: '虾饺',
    dish_status: '待出餐',
    quantity: 1,
    order_time: '2026-07-23T10:00:00.000Z',
    work_enter_time: '2026-07-23T10:00:00.000Z',
    table_number: 'A1',
    station: 'changfen',
    business_flow_id: 'flow-1',
    is_pending_kitchen_work: true,
    ...overrides
  }
  if (order.work_enter_time == null) {
    order.work_enter_time = order.fired_at || order.order_time
  }
  return order
}

const meta = {
  station: 'changfen',
  operatorId: 'chef_changfen',
  readyTime: '2026-07-23T12:00:00.000Z'
}

describe('confirmCardServe', () => {
  it('sends one complete-cooking request for the 将出预览 lines, prints after success, then pulls once', async () => {
    const fen = makeOrder({
      id: 'fen',
      dish_name: '肠粉',
      table_number: '8',
      business_flow_id: 'flow-fen',
      work_enter_time: '2026-07-23T10:00:00.000Z'
    })
    const bun = makeOrder({
      id: 'bun',
      dish_name: '叉烧包',
      table_number: '3',
      business_flow_id: 'flow-bun',
      work_enter_time: '2026-07-23T10:01:00.000Z'
    })

    let selection = emptyServeSelection()
    selection = applyServeSelection(selection, { type: 'increase', chunkId: '肠粉', max: 1 })
    selection = applyServeSelection(selection, { type: 'increase', chunkId: '叉烧包', max: 1 })

    const calls = []
    const outcome = await confirmCardServe({
      selection,
      pendingOrders: [fen, bun],
      chunkOrders: {
        肠粉: { dishName: '肠粉', orders: [fen] },
        叉烧包: { dishName: '叉烧包', orders: [bun] }
      },
      meta,
      completeCooking: async (body) => {
        calls.push(['complete', body.orders.map((line) => line.order_id)])
        return { success: true }
      },
      enqueuePrint: (job) => {
        calls.push(['print', job.order.id, job.dishName])
      },
      pull: async () => {
        calls.push(['pull'])
      }
    })

    expect(outcome.submitted).toBe(true)
    expect(outcome.processed).toBe(2)
    expect(outcome.selection).toEqual(emptyServeSelection())
    expect(outcome.conflictMarks).toEqual([])
    expect(outcome.error).toBeNull()
    expect(calls).toEqual([
      ['complete', ['fen', 'bun']],
      ['print', 'fen', '肠粉'],
      ['print', 'bun', '叉烧包'],
      ['pull']
    ])
  })

  it('keeps 卡上出餐 counts and marks conflict lines on 409, without printing', async () => {
    const a = makeOrder({ id: 'a', table_number: '1', business_flow_id: 'flow-a' })
    const b = makeOrder({ id: 'b', table_number: '2', business_flow_id: 'flow-b' })
    let selection = emptyServeSelection()
    selection = applyServeSelection(selection, { type: 'increase', chunkId: '虾饺', max: 2 })
    selection = applyServeSelection(selection, { type: 'increase', chunkId: '虾饺', max: 2 })

    const error = new Error('HTTP 409: 出餐确认冲突')
    error.response = {
      data: {
        detail: {
          message: '出餐确认冲突',
          conflicts: [{ order_id: 'b', reason: '退菜' }]
        }
      }
    }
    const calls = []
    const outcome = await confirmCardServe({
      selection,
      pendingOrders: [a, b],
      chunkOrders: { 虾饺: { dishName: '虾饺', orders: [a, b] } },
      meta,
      completeCooking: async () => {
        calls.push('complete')
        throw error
      },
      enqueuePrint: () => {
        calls.push('print')
      },
      pull: async () => {
        calls.push('pull')
      }
    })

    expect(outcome.submitted).toBe(false)
    expect(outcome.processed).toBe(0)
    expect(outcome.selection.cardCounts).toEqual({ 虾饺: 2 })
    expect(outcome.conflictMarks).toEqual(['b'])
    expect(outcome.error).toBe(error)
    expect(calls).toEqual(['complete', 'pull'])
  })

  it('does not hit the server when 出餐选中 is empty', async () => {
    const calls = []
    const outcome = await confirmCardServe({
      selection: emptyServeSelection(),
      pendingOrders: [makeOrder()],
      chunkOrders: { 虾饺: { dishName: '虾饺', orders: [makeOrder()] } },
      meta,
      completeCooking: async () => {
        calls.push('complete')
      },
      enqueuePrint: () => {
        calls.push('print')
      },
      pull: async () => {
        calls.push('pull')
      }
    })
    expect(outcome.submitted).toBe(false)
    expect(outcome.processed).toBe(0)
    expect(outcome.selection).toEqual(emptyServeSelection())
    expect(calls).toEqual([])
  })

  it('puts 加急份 on the request before older non-rush FIFO', async () => {
    const early = makeOrder({
      id: '1',
      work_enter_time: '2026-07-23T01:00:00.000Z',
      table_number: '1'
    })
    const rushed = makeOrder({
      id: '2',
      work_enter_time: '2026-07-23T03:00:00.000Z',
      table_number: '2',
      is_rushed: true
    })
    let selection = emptyServeSelection()
    selection = applyServeSelection(selection, { type: 'increase', chunkId: '虾饺', max: 2 })

    let body
    await confirmCardServe({
      selection,
      pendingOrders: [early, rushed],
      chunkOrders: { 虾饺: { dishName: '虾饺', orders: [early, rushed] } },
      meta,
      completeCooking: async (request) => {
        body = request
        return { success: true }
      }
    })
    expect(body.orders.map((line) => line.order_id)).toEqual(['2'])
  })

  it('does not card-serve while 选桌出餐 is open', async () => {
    const order = makeOrder()
    let selection = emptyServeSelection()
    selection = applyServeSelection(selection, { type: 'openTablePick', chunkId: '虾饺' })
    selection = applyServeSelection(selection, { type: 'toggleOrderLine', orderId: '1' })
    const calls = []
    const outcome = await confirmCardServe({
      selection,
      pendingOrders: [order],
      chunkOrders: { 虾饺: { dishName: '虾饺', orders: [order] } },
      meta,
      completeCooking: async () => {
        calls.push('complete')
      }
    })
    expect(outcome.submitted).toBe(false)
    expect(outcome.selection.tablePick.selectedOrderIds).toEqual(['1'])
    expect(calls).toEqual([])
  })
})

describe('confirmTablePickServe', () => {
  it('confirms the checked 订单行 without FIFO fill, then clears 选桌出餐', async () => {
    const a = makeOrder({ id: 'a', table_number: '1', business_flow_id: 'flow-a' })
    const b = makeOrder({
      id: 'b',
      table_number: '2',
      business_flow_id: 'flow-b',
      work_enter_time: '2026-07-23T09:00:00.000Z'
    })
    const c = makeOrder({
      id: 'c',
      table_number: '3',
      business_flow_id: 'flow-c',
      work_enter_time: '2026-07-23T11:00:00.000Z'
    })
    let selection = emptyServeSelection()
    selection = applyServeSelection(selection, { type: 'openTablePick', chunkId: '虾饺' })
    selection = applyServeSelection(selection, { type: 'toggleOrderLine', orderId: 'b' })
    selection = applyServeSelection(selection, { type: 'toggleOrderLine', orderId: 'c' })

    let body
    const outcome = await confirmTablePickServe({
      selection,
      chunkOrders: { 虾饺: { dishName: '虾饺', orders: [a, b, c] } },
      meta,
      completeCooking: async (request) => {
        body = request
        return { success: true }
      }
    })

    expect(outcome.submitted).toBe(true)
    expect(outcome.processed).toBe(2)
    expect(outcome.selection).toEqual(emptyServeSelection())
    expect(body.orders.map((line) => line.order_id)).toEqual(['b', 'c'])
    expect(body.orders.map((line) => line.order_id)).not.toContain('a')
  })
})

describe('confirmBasketServe', () => {
  it('confirms the checked 蒸笼 ids without FIFO fill', async () => {
    const dumpling = makeOrder({
      id: 'd1',
      dish_name: '虾饺',
      table_number: '5',
      business_flow_id: 'flow-d1'
    })
    const bun = makeOrder({
      id: 'b1',
      dish_name: '叉烧包',
      table_number: '4',
      business_flow_id: 'flow-b1'
    })
    const dumpling2 = makeOrder({
      id: 'd2',
      dish_name: '虾饺',
      table_number: '6',
      business_flow_id: 'flow-d2'
    })

    let body
    const outcome = await confirmBasketServe({
      selectedOrderIds: ['b1', 'd2'],
      cages: [dumpling, bun, dumpling2],
      meta,
      completeCooking: async (request) => {
        body = request
        return { success: true }
      }
    })

    expect(outcome.submitted).toBe(true)
    expect(outcome.processed).toBe(2)
    expect(body.orders.map((line) => line.order_id)).toEqual(['b1', 'd2'])
    expect(body.orders.map((line) => line.order_id)).not.toContain('d1')
  })
})
