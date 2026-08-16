import { describe, expect, it } from 'vitest'
import { planBasketServeCookingCalls, planBatchCookingCalls, planTablePickCookingCalls } from '../batchCooking.js'
import { buildCompleteCookingRequest, conflictOrderIdsFromReject, hasMarkedOrderLine, kitchenShouldPull, nextConflictMarks, orderLineIsMarked, runServeConfirm, serveConfirmErrorMessage } from '../serveConfirm.js'
import { applyServeSelection, emptyServeSelection, serveSelectionAfterConfirm } from '../serveSelection.js'
import { toggleSteamerSelection } from '../steamerConsole.js'

function makeOrder(overrides = {}) {
  return {
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
}

describe('buildCompleteCookingRequest', () => {
  it('flattens several 菜卡 into one complete-cooking body that keeps each card’s 将出预览 lines', () => {
    const a = makeOrder({
      id: 'a',
      quantity: 6,
      order_time: '2026-07-23T01:00:00.000Z',
      table_number: '1',
      business_flow_id: 'flow-a'
    })
    const bInLater = makeOrder({
      id: 'b',
      quantity: 2,
      order_time: '2026-07-23T02:00:00.000Z',
      table_number: '2',
      business_flow_id: 'flow-b'
    })
    const c = makeOrder({
      id: 'c',
      quantity: 6,
      order_time: '2026-07-23T03:00:00.000Z',
      table_number: '3',
      business_flow_id: 'flow-c'
    })
    const bun = makeOrder({
      id: 'bun',
      dish_name: '叉烧包',
      table_number: '4',
      business_flow_id: 'flow-bun'
    })

    const plan = planBatchCookingCalls({
      selectedQuantities: { '虾饺::earlier': 4, '虾饺::later': 4, 叉烧包: 1 },
      pendingOrders: [a, bInLater, c, bun],
      chunkOrders: {
        '虾饺::earlier': { dishName: '虾饺', orders: [a] },
        '虾饺::later': { dishName: '虾饺', orders: [bInLater, c] },
        叉烧包: { dishName: '叉烧包', orders: [bun] }
      }
    })

    expect(
      buildCompleteCookingRequest(plan, {
        station: 'shulong',
        operatorId: 'chef_shulong',
        notes: '熟笼档制作完成',
        readyTime: '2026-07-23T12:00:00.000Z'
      })
    ).toEqual({
      dish_name: '虾饺',
      station: 'shulong',
      complete_quantity: 9,
      orders: [
        {
          order_id: 'a',
          business_flow_id: 'flow-a',
          table_number: '1',
          complete_quantity: 4,
          original_quantity: 6
        },
        {
          order_id: 'b',
          business_flow_id: 'flow-b',
          table_number: '2',
          complete_quantity: 2,
          original_quantity: 2
        },
        {
          order_id: 'c',
          business_flow_id: 'flow-c',
          table_number: '3',
          complete_quantity: 2,
          original_quantity: 6
        },
        {
          order_id: 'bun',
          business_flow_id: 'flow-bun',
          table_number: '4',
          complete_quantity: 1,
          original_quantity: 1
        }
      ],
      operator_id: 'chef_shulong',
      ready_time: '2026-07-23T12:00:00.000Z'
    })
  })

  it('uses the checked 选桌出餐 lines as the one request, not FIFO of the card', () => {
    const earlier = makeOrder({
      id: 'a',
      table_number: '1',
      order_time: '2026-07-23T01:00:00.000Z',
      business_flow_id: 'flow-a'
    })
    const later = makeOrder({
      id: 'b',
      table_number: '2',
      order_time: '2026-07-23T02:00:00.000Z',
      business_flow_id: 'flow-b'
    })
    const plan = planTablePickCookingCalls({
      selectedOrderIds: ['b'],
      chunkId: '虾饺',
      chunkOrders: { 虾饺: { dishName: '虾饺', orders: [earlier, later] } }
    })

    const request = buildCompleteCookingRequest(plan, {
      station: 'changfen',
      operatorId: 'chef_changfen',
      readyTime: '2026-07-23T12:00:00.000Z'
    })

    expect(request.complete_quantity).toBe(1)
    expect(request.orders).toEqual([
      {
        order_id: 'b',
        business_flow_id: 'flow-b',
        table_number: '2',
        complete_quantity: 1,
        original_quantity: 1
      }
    ])
  })

  it('flattens mixed-菜名 笼上出餐 into one request of the checked 蒸笼', () => {
    const dumpling = makeOrder({
      id: 'd1',
      dish_name: '虾饺',
      table_number: '3',
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
      table_number: '5',
      business_flow_id: 'flow-d2'
    })
    const plan = planBasketServeCookingCalls({
      selectedOrderIds: ['b1', 'd2'],
      cages: [dumpling, bun, dumpling2]
    })

    const request = buildCompleteCookingRequest(plan, {
      station: 'shulong',
      operatorId: 'chef_shulong',
      readyTime: '2026-07-23T12:00:00.000Z'
    })

    expect(request.complete_quantity).toBe(2)
    expect(request.orders.map((line) => line.order_id)).toEqual(['b1', 'd2'])
    expect(request.orders).not.toEqual(
      expect.arrayContaining([expect.objectContaining({ order_id: 'd1' })])
    )
  })

  it('returns null for an empty plan so confirm does not hit the server', () => {
    expect(
      buildCompleteCookingRequest([], {
        station: 'changfen',
        readyTime: '2026-07-23T12:00:00.000Z'
      })
    ).toBeNull()
    expect(
      buildCompleteCookingRequest(
        planBatchCookingCalls({
          selectedQuantities: { 虾饺: 0 },
          pendingOrders: [makeOrder()]
        }),
        { station: 'changfen', readyTime: '2026-07-23T12:00:00.000Z' }
      )
    ).toBeNull()
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

describe('conflictOrderIdsFromReject', () => {
  it('reads every conflict order_id from a 409 detail object', () => {
    expect(conflictOrderIdsFromReject({
      message: '出餐确认冲突',
      conflicts: [
        { order_id: 'a', reason: '退菜' },
        { order_id: 'c', reason: '已出餐' }
      ]
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
    expect(conflictOrderIdsFromReject(error)).toEqual(['missing'])
  })

  it('returns no line ids for timeout or disconnect', () => {
    expect(conflictOrderIdsFromReject(new Error('请求超时，请检查网络连接'))).toEqual([])
    expect(conflictOrderIdsFromReject(new Error('网络连接失败，请检查网络设置'))).toEqual([])
  })

  it('uses the 409 message, not a stringified detail object', () => {
    const error = new Error('HTTP 409: [object Object]')
    error.response = {
      data: { detail: { message: '出餐确认冲突', conflicts: [{ order_id: 'a', reason: '退菜' }] } }
    }
    expect(serveConfirmErrorMessage(error)).toBe('出餐确认冲突')
    expect(serveConfirmErrorMessage(new Error('请求超时，请检查网络连接'))).toBe('请求超时，请检查网络连接')
  })
})

describe('nextConflictMarks', () => {
  it('marks every conflict order_id from a 409 reject', () => {
    const error = new Error('HTTP 409: 出餐确认冲突')
    error.response = {
      data: {
        detail: {
          message: '出餐确认冲突',
          conflicts: [
            { order_id: 'a', reason: '退菜' },
            { order_id: 'c', reason: '已出餐' }
          ]
        }
      }
    }
    expect(nextConflictMarks(['stale'], { type: 'reject', error })).toEqual(['a', 'c'])
  })

  it('marks no lines on timeout or disconnect', () => {
    expect(nextConflictMarks(['stale'], {
      type: 'reject',
      error: new Error('请求超时，请检查网络连接')
    })).toEqual([])
    expect(nextConflictMarks(['stale'], {
      type: 'reject',
      error: new Error('网络连接失败，请检查网络设置')
    })).toEqual([])
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

  it('keeps 选桌出餐 rows after a 409 so dropping the marked line retries the rest', () => {
    const a = makeOrder({ id: 'a', table_number: '1' })
    const b = makeOrder({ id: 'b', table_number: '2' })
    const c = makeOrder({ id: 'c', table_number: '3' })
    let selection = emptyServeSelection()
    selection = applyServeSelection(selection, { type: 'openTablePick', chunkId: '虾饺' })
    selection = applyServeSelection(selection, { type: 'toggleOrderLine', orderId: 'a' })
    selection = applyServeSelection(selection, { type: 'toggleOrderLine', orderId: 'b' })
    selection = applyServeSelection(selection, { type: 'toggleOrderLine', orderId: 'c' })

    let marks = nextConflictMarks([], {
      type: 'reject',
      error: { conflicts: [{ order_id: 'b', reason: '退菜' }] }
    })
    selection = serveSelectionAfterConfirm(selection, false)
    expect(selection.tablePick.selectedOrderIds).toEqual(['a', 'b', 'c'])
    expect(marks).toEqual(['b'])

    selection = applyServeSelection(selection, { type: 'toggleOrderLine', orderId: 'b' })
    marks = nextConflictMarks(marks, { type: 'selectionChange' })
    expect(marks).toEqual([])
    expect(selection.tablePick.selectedOrderIds).toEqual(['a', 'c'])

    const plan = planTablePickCookingCalls({
      selectedOrderIds: selection.tablePick.selectedOrderIds,
      chunkId: '虾饺',
      chunkOrders: { 虾饺: { dishName: '虾饺', orders: [a, b, c] } }
    })
    expect(plan[0].allocations.map(({ order }) => order.id)).toEqual(['a', 'c'])
  })

  it('keeps 卡上出餐 counts after a 409 so other 菜卡 need not be re-tapped', () => {
    let selection = emptyServeSelection()
    selection = applyServeSelection(selection, { type: 'increase', chunkId: '虾饺', max: 2 })
    selection = applyServeSelection(selection, { type: 'increase', chunkId: '虾饺', max: 2 })
    selection = applyServeSelection(selection, { type: 'increase', chunkId: '叉烧包', max: 1 })
    selection = serveSelectionAfterConfirm(selection, false)
    expect(selection.cardCounts).toEqual({ 虾饺: 2, 叉烧包: 1 })

    selection = applyServeSelection(selection, { type: 'decrease', chunkId: '虾饺' })
    expect(selection.cardCounts).toEqual({ 虾饺: 1, 叉烧包: 1 })
  })

  it('keeps 笼上出餐 ids after a 409 so dropping the marked 蒸笼 retries the rest', () => {
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
    let selectedOrderIds = ['b1', 'd2']
    let marks = nextConflictMarks([], {
      type: 'reject',
      error: { conflicts: [{ order_id: 'b1', reason: '退菜' }] }
    })
    expect(selectedOrderIds).toEqual(['b1', 'd2'])
    expect(marks).toEqual(['b1'])
    expect(orderLineIsMarked(marks, bun)).toBe(true)

    selectedOrderIds = toggleSteamerSelection(selectedOrderIds, 'b1')
    marks = nextConflictMarks(marks, { type: 'selectionChange' })
    expect(marks).toEqual([])
    expect(selectedOrderIds).toEqual(['d2'])

    const plan = planBasketServeCookingCalls({
      selectedOrderIds,
      cages: [dumpling, bun]
    })
    expect(plan.map((item) => item.allocations.map(({ order }) => order.id))).toEqual([['d2']])
  })

  it('keeps selection on timeout and still leaves no line marks', () => {
    let selection = emptyServeSelection()
    selection = applyServeSelection(selection, { type: 'increase', chunkId: '虾饺', max: 2 })
    selection = serveSelectionAfterConfirm(selection, false)
    expect(selection.cardCounts).toEqual({ 虾饺: 1 })
    expect(nextConflictMarks(['stale'], {
      type: 'reject',
      error: new Error('请求超时，请检查网络连接')
    })).toEqual([])
  })
})

describe('runServeConfirm', () => {
  const meta = {
    station: 'changfen',
    operatorId: 'chef_changfen',
    readyTime: '2026-07-23T12:00:00.000Z'
  }

  function twoCardPlan() {
    const fen = makeOrder({
      id: 'fen',
      dish_name: '肠粉',
      table_number: '8',
      business_flow_id: 'flow-fen'
    })
    const bun = makeOrder({
      id: 'bun',
      dish_name: '叉烧包',
      table_number: '3',
      business_flow_id: 'flow-bun'
    })
    return planBatchCookingCalls({
      selectedQuantities: { 肠粉: 1, 叉烧包: 1 },
      pendingOrders: [fen, bun],
      chunkOrders: {
        肠粉: { dishName: '肠粉', orders: [fen] },
        叉烧包: { dishName: '叉烧包', orders: [bun] }
      }
    })
  }

  it('sends one complete-cooking request and prints only after it succeeds, then pulls once', async () => {
    const calls = []
    const plan = twoCardPlan()
    const result = await runServeConfirm({
      plan,
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

    expect(result.submitted).toBe(true)
    expect(result.processed).toBe(2)
    expect(calls).toEqual([
      ['complete', ['fen', 'bun']],
      ['print', 'fen', '肠粉'],
      ['print', 'bun', '叉烧包'],
      ['pull']
    ])
  })

  it('does not print when the confirm fails, then still pulls once', async () => {
    const calls = []
    const error = new Error('请求超时，请检查网络连接')
    await expect(
      runServeConfirm({
        plan: twoCardPlan(),
        meta,
        completeCooking: async () => {
          calls.push(['complete'])
          throw error
        },
        enqueuePrint: () => {
          calls.push(['print'])
        },
        pull: async () => {
          calls.push(['pull'])
        }
      })
    ).rejects.toBe(error)
    expect(calls).toEqual([['complete'], ['pull']])
  })

  it('still treats the confirm as success when the settle pull throws', async () => {
    const result = await runServeConfirm({
      plan: twoCardPlan(),
      meta,
      completeCooking: async () => ({ success: true }),
      enqueuePrint: () => {},
      pull: async () => {
        throw new Error('刷新失败')
      }
    })
    expect(result.submitted).toBe(true)
    expect(result.processed).toBe(2)
  })

  it('does not hit the server or pull when the plan is empty', async () => {
    const calls = []
    const result = await runServeConfirm({
      plan: [],
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
    expect(result).toEqual({ submitted: false, processed: 0, request: null })
    expect(calls).toEqual([])
  })
})



