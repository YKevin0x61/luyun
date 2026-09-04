import { describe, expect, it } from 'vitest'
import {
  applyServeSelection,
  confirmBasketServe,
  confirmCardServe,
  confirmTablePickServe,
  emptyServeSelection,
  hasMarkedOrderLine,
  hubShouldPull,
  kitchenShouldPull,
  kitchenShouldRedrawWork,
  nextConflictMarks,
  orderLineIsMarked,
  serveConfirmErrorMessage
} from '../kitchenServe.js'
import { toggleSteamerSelection } from '../steamerConsole.js'

function makeOrder(overrides = {}) {
  const order = {
    id: '1',
    dish_name: '虾饺',
    dish_status: '待出餐',
    quantity: 1,
    order_time: '2026-07-23T10:00:00.000Z',
    table_number: 'A1',
    station: 'shulong',
    business_flow_id: 'flow-1',
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

describe('kitchenShouldRedrawWork', () => {
  it('skips redraw of 待出餐工作 while 提交中', () => {
    expect(kitchenShouldRedrawWork({ submitting: true })).toBe(false)
    expect(kitchenShouldRedrawWork({ submitting: false })).toBe(true)
  })
})

describe('kitchenShouldPull', () => {
  it('holds pull while 提交中 or steamer 出餐 is in flight', () => {
    expect(kitchenShouldPull({
      submitting: true,
      steamerLoading: false,
      lockedStation: 'changfen',
      scope: { station: 'changfen' }
    })).toBe(false)
    expect(kitchenShouldPull({
      submitting: false,
      steamerLoading: true,
      lockedStation: 'shulong',
      scope: { station: 'shulong' }
    })).toBe(false)
    expect(kitchenShouldPull({
      submitting: true,
      steamerLoading: false,
      lockedStation: 'changfen',
      scope: { reconcile: true }
    })).toBe(false)
  })

  it('allows one pull after the confirm settles, including timeout and 60s reconcile', () => {
    expect(kitchenShouldPull({
      submitting: false,
      steamerLoading: false,
      lockedStation: 'changfen',
      scope: { station: 'changfen' }
    })).toBe(true)
    expect(kitchenShouldPull({
      submitting: false,
      steamerLoading: false,
      lockedStation: 'changfen',
      scope: { reconcile: true }
    })).toBe(true)
  })

  it('does not pull kitchen work when the nudge is scoped to another 档口', () => {
    expect(kitchenShouldPull({
      submitting: false,
      steamerLoading: false,
      lockedStation: 'changfen',
      scope: { station: 'xibing' }
    })).toBe(false)
    expect(kitchenShouldPull({
      submitting: false,
      steamerLoading: false,
      lockedStation: 'changfen',
      scope: { station: 'changfen' }
    })).toBe(true)
  })
})

describe('hubShouldPull', () => {
  it('does not pull today\'s full order list on a non-reconcile orders nudge', () => {
    expect(hubShouldPull({ scope: { station: 'changfen' } })).toBe(false)
    expect(hubShouldPull({ scope: {} })).toBe(false)
    expect(hubShouldPull({})).toBe(false)
  })

  it('allows the 60s reconcile to pull', () => {
    expect(hubShouldPull({ scope: { reconcile: true } })).toBe(true)
    expect(hubShouldPull({ scope: { station: 'changfen', reconcile: true } })).toBe(true)
  })
})

describe('conflict marks', () => {
  it('reads every conflict order_id from a 409 detail object', () => {
    expect(nextConflictMarks([], {
      type: 'reject',
      error: {
        message: '出餐确认冲突',
        conflicts: [
          { order_id: 'a', reason: '退菜' },
          { order_id: 'c', reason: '已出餐' }
        ]
      }
    })).toEqual(['a', 'c'])
  })

  it('reads conflicts from a request error that wraps the 409 body', () => {
    const error = new Error('HTTP 409: 出餐确认冲突')
    error.statusCode = 409
    error.response = {
      data: {
        detail: {
          message: '出餐确认冲突',
          conflicts: [{ order_id: 'missing', reason: '不存在' }]
        }
      }
    }
    expect(nextConflictMarks([], { type: 'reject', error })).toEqual(['missing'])
  })

  it('returns no line ids for timeout or disconnect', () => {
    expect(nextConflictMarks(['stale'], {
      type: 'reject',
      error: new Error('请求超时，请检查网络连接')
    })).toEqual([])
    expect(nextConflictMarks(['stale'], {
      type: 'reject',
      error: new Error('网络连接失败，请检查网络设置')
    })).toEqual([])
  })

  it('uses the 409 message, not a stringified detail object', () => {
    const error = new Error('HTTP 409: [object Object]')
    error.response = {
      data: { detail: { message: '出餐确认冲突', conflicts: [{ order_id: 'a', reason: '退菜' }] } }
    }
    expect(serveConfirmErrorMessage(error)).toBe('出餐确认冲突')
    expect(serveConfirmErrorMessage(new Error('请求超时，请检查网络连接'))).toBe('请求超时，请检查网络连接')
  })

  it('marks 等叫 lines from an in-flight 409, distinct from 退菜', () => {
    const error = new Error('HTTP 409: 出餐确认冲突')
    error.response = {
      data: {
        detail: {
          message: '出餐确认冲突',
          conflicts: [
            { order_id: 'held-a', reason: '等叫' },
            { order_id: 'held-b', reason: '等叫' }
          ]
        }
      }
    }
    expect(nextConflictMarks(['stale'], { type: 'reject', error })).toEqual(['held-a', 'held-b'])
    expect(error.response.data.detail.conflicts.map((item) => item.reason)).toEqual(['等叫', '等叫'])
    expect(error.response.data.detail.conflicts.map((item) => item.reason)).not.toContain('退菜')
  })

  it('clears previous marks when the chef changes selection or starts another confirm', () => {
    expect(nextConflictMarks(['a', 'c'], { type: 'selectionChange' })).toEqual([])
    expect(nextConflictMarks(['a', 'c'], { type: 'confirmStart' })).toEqual([])
  })

  it('marks the conflicting 订单行 and the 菜卡 that contains it', () => {
    const marks = ['c']
    const earlier = makeOrder({ id: 'a', table_number: '1' })
    const later = makeOrder({ id: 'c', table_number: '3' })
    expect(orderLineIsMarked(marks, later)).toBe(true)
    expect(orderLineIsMarked(marks, earlier)).toBe(false)
    expect(hasMarkedOrderLine(marks, [earlier, later])).toBe(true)
    expect(hasMarkedOrderLine(marks, [earlier])).toBe(false)
  })

  it('keeps 选桌出餐 rows after a 409 so dropping the marked line retries the rest', async () => {
    const a = makeOrder({ id: 'a', table_number: '1' })
    const b = makeOrder({ id: 'b', table_number: '2' })
    const c = makeOrder({ id: 'c', table_number: '3' })
    let selection = emptyServeSelection()
    selection = applyServeSelection(selection, { type: 'openTablePick', chunkId: '虾饺' })
    selection = applyServeSelection(selection, { type: 'toggleOrderLine', orderId: 'a' })
    selection = applyServeSelection(selection, { type: 'toggleOrderLine', orderId: 'b' })
    selection = applyServeSelection(selection, { type: 'toggleOrderLine', orderId: 'c' })

    const error = { conflicts: [{ order_id: 'b', reason: '退菜' }] }
    const failed = await confirmTablePickServe({
      selection,
      chunkOrders: { 虾饺: { dishName: '虾饺', orders: [a, b, c] } },
      meta,
      completeCooking: async () => {
        throw error
      }
    })
    expect(failed.selection.tablePick.selectedOrderIds).toEqual(['a', 'b', 'c'])
    expect(failed.conflictMarks).toEqual(['b'])

    selection = applyServeSelection(failed.selection, { type: 'toggleOrderLine', orderId: 'b' })
    const marks = nextConflictMarks(failed.conflictMarks, { type: 'selectionChange' })
    expect(marks).toEqual([])
    expect(selection.tablePick.selectedOrderIds).toEqual(['a', 'c'])

    let body
    await confirmTablePickServe({
      selection,
      chunkOrders: { 虾饺: { dishName: '虾饺', orders: [a, b, c] } },
      meta,
      completeCooking: async (request) => {
        body = request
        return { success: true }
      }
    })
    expect(body.orders.map((line) => line.order_id)).toEqual(['a', 'c'])
  })

  it('keeps 卡上出餐 counts after a 409 so other 菜卡 need not be re-tapped', async () => {
    let selection = emptyServeSelection()
    selection = applyServeSelection(selection, { type: 'increase', chunkId: '虾饺', max: 2 })
    selection = applyServeSelection(selection, { type: 'increase', chunkId: '虾饺', max: 2 })
    selection = applyServeSelection(selection, { type: 'increase', chunkId: '叉烧包', max: 1 })
    const shrimp = makeOrder({ id: 'a', dish_name: '虾饺' })
    const bun = makeOrder({ id: 'b', dish_name: '叉烧包' })
    const failed = await confirmCardServe({
      selection,
      pendingOrders: [shrimp, bun],
      chunkOrders: {
        虾饺: { dishName: '虾饺', orders: [shrimp] },
        叉烧包: { dishName: '叉烧包', orders: [bun] }
      },
      meta,
      completeCooking: async () => {
        throw new Error('请求超时，请检查网络连接')
      }
    })
    expect(failed.selection.cardCounts).toEqual({ 虾饺: 2, 叉烧包: 1 })
    selection = applyServeSelection(failed.selection, { type: 'decrease', chunkId: '虾饺' })
    expect(selection.cardCounts).toEqual({ 虾饺: 1, 叉烧包: 1 })
  })

  it('keeps 笼上出餐 ids after a 409 so dropping the marked 蒸笼 retries the rest', async () => {
    const dumpling = makeOrder({
      id: 'd2',
      dish_name: '虾饺',
      table_number: '5',
      business_flow_id: 'flow-d2'
    })
    const bun = makeOrder({
      id: 'b1',
      dish_name: '叉烧包',
      table_number: '4',
      business_flow_id: 'flow-b1'
    })
    const failed = await confirmBasketServe({
      selectedOrderIds: ['b1', 'd2'],
      cages: [dumpling, bun],
      meta,
      completeCooking: async () => {
        const error = { conflicts: [{ order_id: 'b1', reason: '退菜' }] }
        throw error
      }
    })
    expect(failed.conflictMarks).toEqual(['b1'])
    expect(orderLineIsMarked(failed.conflictMarks, bun)).toBe(true)

    const selectedOrderIds = toggleSteamerSelection(['b1', 'd2'], 'b1')
    const marks = nextConflictMarks(failed.conflictMarks, { type: 'selectionChange' })
    expect(marks).toEqual([])
    expect(selectedOrderIds).toEqual(['d2'])

    let body
    await confirmBasketServe({
      selectedOrderIds,
      cages: [dumpling, bun],
      meta,
      completeCooking: async (request) => {
        body = request
        return { success: true }
      }
    })
    expect(body.orders.map((line) => line.order_id)).toEqual(['d2'])
  })

  it('keeps selection on timeout and still leaves no line marks', async () => {
    let selection = emptyServeSelection()
    selection = applyServeSelection(selection, { type: 'increase', chunkId: '虾饺', max: 2 })
    const failed = await confirmCardServe({
      selection,
      pendingOrders: [makeOrder()],
      chunkOrders: { 虾饺: { dishName: '虾饺', orders: [makeOrder()] } },
      meta,
      completeCooking: async () => {
        throw new Error('请求超时，请检查网络连接')
      }
    })
    expect(failed.selection.cardCounts).toEqual({ 虾饺: 1 })
    expect(failed.conflictMarks).toEqual([])
  })

  it('does not hit the server when 等叫 emptied the remaining 出餐选中', async () => {
    let selection = emptyServeSelection()
    selection = applyServeSelection(selection, { type: 'increase', chunkId: '虾饺', max: 2 })
    selection = applyServeSelection(selection, { type: 'increase', chunkId: '虾饺', max: 2 })
    selection = applyServeSelection(selection, {
      type: 'syncLiveWork',
      liveOrderIds: [],
      chunkMax: {}
    })
    expect(selection.cardCounts).toEqual({})
    const card = await confirmCardServe({
      selection,
      pendingOrders: [makeOrder({ id: 'a', is_hold: true })],
      chunkOrders: { 虾饺: { dishName: '虾饺', orders: [] } },
      meta,
      completeCooking: async () => {
        throw new Error('should not run')
      }
    })
    expect(card.submitted).toBe(false)

    selection = applyServeSelection(emptyServeSelection(), { type: 'openTablePick', chunkId: '虾饺' })
    selection = applyServeSelection(selection, { type: 'toggleOrderLine', orderId: 'a' })
    selection = applyServeSelection(selection, {
      type: 'syncLiveWork',
      liveOrderIds: [],
      chunkMax: {}
    })
    expect(selection.tablePick).toBeNull()
    const table = await confirmTablePickServe({
      selection,
      chunkOrders: { 虾饺: { dishName: '虾饺', orders: [makeOrder({ id: 'a', is_hold: true })] } },
      meta,
      completeCooking: async () => {
        throw new Error('should not run')
      }
    })
    expect(table.submitted).toBe(false)
  })
})
