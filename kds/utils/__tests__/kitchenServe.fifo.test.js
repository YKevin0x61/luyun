import { describe, expect, it } from 'vitest'
import {
  applyServeSelection,
  confirmBasketServe,
  confirmCardServe,
  confirmTablePickServe,
  emptyServeSelection,
  servePreviewOrderIds
} from '../kitchenServe.js'

function makeOrder(overrides = {}) {
  const order = {
    id: '1',
    dish_name: '虾饺',
    dish_status: '待出餐',
    quantity: 1,
    order_time: '2026-07-23T10:00:00.000Z',
    table_number: 'A1',
    station: 'shulong',
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

function selectionFromCounts(cardCounts) {
  let selection = emptyServeSelection()
  for (const [chunkId, n] of Object.entries(cardCounts)) {
    const count = Number(n) || 0
    for (let i = 0; i < count; i++) {
      selection = applyServeSelection(selection, { type: 'increase', chunkId, max: count })
    }
  }
  return selection
}

async function cardRequest(cardCounts, pendingOrders, chunkOrders) {
  let body = null
  const outcome = await confirmCardServe({
    selection: selectionFromCounts(cardCounts),
    pendingOrders,
    chunkOrders,
    meta,
    completeCooking: async (request) => {
      body = request
      return { success: true }
    }
  })
  return { outcome, body, ids: body?.orders?.map((line) => line.order_id) || [] }
}

function tableSelection(chunkId, selectedOrderIds) {
  let selection = applyServeSelection(emptyServeSelection(), { type: 'openTablePick', chunkId })
  for (const orderId of selectedOrderIds) {
    selection = applyServeSelection(selection, { type: 'toggleOrderLine', orderId })
  }
  return selection
}

async function tableRequest(chunkId, selectedOrderIds, chunkOrders) {
  let body = null
  const outcome = await confirmTablePickServe({
    selection: tableSelection(chunkId, selectedOrderIds),
    chunkOrders,
    meta,
    completeCooking: async (request) => {
      body = request
      return { success: true }
    }
  })
  return { outcome, body, ids: body?.orders?.map((line) => line.order_id) || [] }
}

async function basketRequest(selectedOrderIds, cages) {
  let body = null
  const outcome = await confirmBasketServe({
    selectedOrderIds,
    cages,
    meta,
    completeCooking: async (request) => {
      body = request
      return { success: true }
    }
  })
  return { outcome, body, ids: body?.orders?.map((line) => line.order_id) || [] }
}

describe('卡上出餐 FIFO', () => {
  it('serves earliest 进入待出餐工作时刻 lines first', async () => {
    const o1 = makeOrder({ id: '1', order_time: '2026-07-23T01:00:00.000Z', table_number: '1' })
    const o2 = makeOrder({ id: '2', order_time: '2026-07-23T02:00:00.000Z', table_number: '2' })
    const o3 = makeOrder({ id: '3', order_time: '2026-07-23T03:00:00.000Z', table_number: '3' })
    const { ids, body } = await cardRequest({ 虾饺: 2 }, [o3, o1, o2])
    expect(ids).toEqual(['1', '2'])
    expect(body.complete_quantity).toBe(2)
  })

  it('allocates a late 叫起 after earlier never-held FIFO', async () => {
    const fresh = makeOrder({
      id: '1',
      order_time: '2026-07-23T02:00:00.000Z',
      table_number: '1'
    })
    const firedLate = makeOrder({
      id: '2',
      order_time: '2026-07-23T01:00:00.000Z',
      fired_at: '2026-07-23T03:00:00.000Z',
      table_number: '2'
    })
    const { ids } = await cardRequest({ 虾饺: 1 }, [firedLate, fresh])
    expect(ids).toEqual(['1'])
  })

  it('takes multiple portions from an earlier multi-qty order before later ones', async () => {
    const o1 = makeOrder({ id: '1', quantity: 2, order_time: '2026-07-23T01:00:00.000Z' })
    const o2 = makeOrder({ id: '2', quantity: 2, order_time: '2026-07-23T02:00:00.000Z' })
    const { body } = await cardRequest({ 虾饺: 3 }, [o2, o1])
    expect(body.orders).toEqual([
      expect.objectContaining({ order_id: '1', complete_quantity: 2, original_quantity: 2 }),
      expect.objectContaining({ order_id: '2', complete_quantity: 1, original_quantity: 2 })
    ])
  })

  it('sends one request per confirm covering every selected dish', async () => {
    const shrimp = makeOrder({ id: '1', dish_name: '虾饺' })
    const bun = makeOrder({
      id: '2',
      dish_name: '叉烧包',
      order_time: '2026-07-23T01:30:00.000Z'
    })
    const { ids, body } = await cardRequest({ 虾饺: 1, 叉烧包: 1, 烧卖: 0 }, [shrimp, bun])
    expect(ids).toEqual(['1', '2'])
    expect(body.complete_quantity).toBe(2)
  })

  it('caps at available pending portions when selection exceeds stock', async () => {
    const o1 = makeOrder({ id: '1', quantity: 1 })
    const { body } = await cardRequest({ 虾饺: 5 }, [o1])
    expect(body.complete_quantity).toBe(1)
    expect(body.orders).toEqual([
      expect.objectContaining({ order_id: '1', complete_quantity: 1 })
    ])
  })

  it('does not hit the server when nothing can be fulfilled', async () => {
    const emptyStock = await cardRequest({ 虾饺: 2 }, [])
    expect(emptyStock.outcome.submitted).toBe(false)
    expect(emptyStock.body).toBeNull()

    const emptySel = await cardRequest({}, [makeOrder()])
    expect(emptySel.outcome.submitted).toBe(false)
    expect(emptySel.body).toBeNull()
  })

  it('allocates a chunk’s 出餐 only against that chunk’s orders', async () => {
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
    const { body } = await cardRequest(
      { '虾饺::later': 3 },
      [earlier, later],
      {
        '虾饺::earlier': { dishName: '虾饺', orders: [earlier] },
        '虾饺::later': { dishName: '虾饺', orders: [later] }
      }
    )
    expect(body.orders).toEqual([
      expect.objectContaining({ order_id: 'b', complete_quantity: 3, original_quantity: 8 })
    ])
  })

  it('does not let two chunks of the same dish cross-allocate', async () => {
    const a = makeOrder({
      id: 'a',
      quantity: 6,
      order_time: '2026-07-23T01:00:00.000Z',
      table_number: '1',
      business_flow_id: 'flow-a'
    })
    const b = makeOrder({
      id: 'b',
      quantity: 6,
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
    const bInEarlier = { ...b, quantity: 4, served_quantity: 0, servedQuantity: 0 }
    const bInLater = { ...b, quantity: 2, served_quantity: 0, servedQuantity: 0 }
    const { body } = await cardRequest(
      { '虾饺::earlier': 4, '虾饺::later': 4 },
      [a, b, c],
      {
        '虾饺::earlier': { dishName: '虾饺', orders: [a, bInEarlier] },
        '虾饺::later': { dishName: '虾饺', orders: [bInLater, c] }
      }
    )
    expect(body.complete_quantity).toBe(8)
    expect(body.orders).toEqual([
      expect.objectContaining({ order_id: 'a', complete_quantity: 4, original_quantity: 6 }),
      expect.objectContaining({ order_id: 'b', complete_quantity: 2, original_quantity: 2 }),
      expect.objectContaining({ order_id: 'c', complete_quantity: 2, original_quantity: 6 })
    ])
  })

  it('does not fall back to other same-dish orders when the selected chunk id is gone', async () => {
    const earlier = makeOrder({
      id: 'a',
      quantity: 10,
      order_time: '2026-07-23T01:00:00.000Z'
    })
    const { outcome, body } = await cardRequest(
      { '虾饺::gone': 3 },
      [earlier],
      { '虾饺::earlier': { dishName: '虾饺', orders: [earlier] } }
    )
    expect(outcome.submitted).toBe(false)
    expect(body).toBeNull()
  })

  it('does not let two same-name different-notes cards steal each other’s 份', async () => {
    const onion = makeOrder({
      id: 'o1',
      dish_name: '艇仔粥',
      notes: '免葱',
      quantity: 2,
      order_time: '2026-07-23T01:00:00.000Z',
      table_number: '1'
    })
    const plain = makeOrder({
      id: 'p1',
      dish_name: '艇仔粥',
      notes: '',
      quantity: 2,
      order_time: '2026-07-23T01:01:00.000Z',
      table_number: '2'
    })
    const { ids } = await cardRequest(
      { 'onion-card': 1, 'plain-card': 1 },
      [onion, plain],
      {
        'onion-card': { dishName: '艇仔粥', notes: '免葱', orders: [onion] },
        'plain-card': { dishName: '艇仔粥', notes: '', orders: [plain] }
      }
    )
    expect(ids).toEqual(['o1', 'p1'])
  })

  it('does not treat notes containing 催 as 加急 when is_rushed is false', async () => {
    const early = makeOrder({
      id: 'a',
      notes: '催一下',
      is_rushed: false,
      order_time: '2026-07-23T01:00:00.000Z',
      table_number: '1'
    })
    const later = makeOrder({
      id: 'b',
      notes: '催一下',
      is_rushed: false,
      order_time: '2026-07-23T02:00:00.000Z',
      table_number: '2'
    })
    const { ids } = await cardRequest({ 虾饺: 1 }, [later, early])
    expect(ids).toEqual(['a'])
    expect(servePreviewOrderIds([later, early], 1)).toEqual(['a'])
  })

  it('flattens several 菜卡 into one complete-cooking body', async () => {
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
    const { body } = await cardRequest(
      { '虾饺::earlier': 4, '虾饺::later': 4, 叉烧包: 1 },
      [a, bInLater, c, bun],
      {
        '虾饺::earlier': { dishName: '虾饺', orders: [a] },
        '虾饺::later': { dishName: '虾饺', orders: [bInLater, c] },
        叉烧包: { dishName: '叉烧包', orders: [bun] }
      }
    )
    expect(body).toEqual({
      dish_name: '虾饺',
      station: 'changfen',
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
      operator_id: 'chef_changfen',
      ready_time: '2026-07-23T12:00:00.000Z'
    })
  })
})

describe('将出预览', () => {
  it('lists FIFO 订单行 ids for the selected 份, earliest first', () => {
    const earlier = makeOrder({ id: 'a', table_number: '5', order_time: '2026-07-23T01:00:00.000Z' })
    const middle = makeOrder({ id: 'b', table_number: '6', order_time: '2026-07-23T01:01:00.000Z' })
    const later = makeOrder({ id: 'c', table_number: '14', order_time: '2026-07-23T01:02:00.000Z' })
    expect(servePreviewOrderIds([later, middle, earlier], 2)).toEqual(['a', 'b'])
    expect(servePreviewOrderIds([later, middle, earlier], 0)).toEqual([])
  })

  it('lists 加急 订单行 before older non-rush', () => {
    const early = makeOrder({ id: 'a', table_number: '5', order_time: '2026-07-23T01:00:00.000Z' })
    const rushedLate = makeOrder({
      id: 'b',
      table_number: '14',
      order_time: '2026-07-23T01:02:00.000Z',
      is_rushed: true
    })
    expect(servePreviewOrderIds([early, rushedLate], 1)).toEqual(['b'])
  })

  it('does not treat 已取消 退示 (quantity 0) as FIFO 将出', async () => {
    const cancelled = makeOrder({
      id: 'n1',
      dish_status: '已取消',
      status: '退菜',
      quantity: 0,
      table_number: '3',
      order_time: '2026-07-23T00:00:00.000Z'
    })
    const pending = makeOrder({
      id: 'a',
      table_number: '8',
      order_time: '2026-07-23T01:00:00.000Z'
    })
    expect(servePreviewOrderIds([cancelled, pending], 1)).toEqual(['a'])
    const { ids } = await cardRequest({ 虾饺: 1 }, [cancelled, pending])
    expect(ids).toEqual(['a'])
  })
})

describe('选桌出餐', () => {
  it('confirms checked 订单行 in that chunk only, not FIFO of the card', async () => {
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
    const sibling = makeOrder({
      id: 'c',
      table_number: '3',
      order_time: '2026-07-23T03:00:00.000Z'
    })
    const { ids, body } = await tableRequest('虾饺::later', ['b', 'c'], {
      '虾饺::later': { dishName: '虾饺', orders: [later] },
      '虾饺::earlier': { dishName: '虾饺', orders: [earlier, sibling] }
    })
    expect(ids).toEqual(['b'])
    expect(body.complete_quantity).toBe(1)
  })

  it('does not hit the server for an empty pick', async () => {
    const order = makeOrder({ id: 'a' })
    const { outcome, body } = await tableRequest('虾饺', [], {
      虾饺: { dishName: '虾饺', orders: [order] }
    })
    expect(outcome.submitted).toBe(false)
    expect(body).toBeNull()
  })

  it('ignores 已取消 退示 ids even if they were passed in the pick', async () => {
    const cancelled = makeOrder({
      id: 'n1',
      dish_status: '已取消',
      status: '退菜',
      quantity: 0
    })
    const pending = makeOrder({ id: 'a', table_number: '8' })
    const { ids } = await tableRequest('虾饺', ['n1', 'a'], {
      虾饺: { dishName: '虾饺', orders: [cancelled, pending] }
    })
    expect(ids).toEqual(['a'])
  })

  it('still serves chef-picked non-rush when a later 加急 exists', async () => {
    const early = makeOrder({
      id: 'a',
      table_number: '1',
      order_time: '2026-07-23T01:00:00.000Z'
    })
    const rushed = makeOrder({
      id: 'b',
      table_number: '2',
      order_time: '2026-07-23T02:00:00.000Z',
      is_rushed: true
    })
    const { ids } = await tableRequest('虾饺', ['a'], {
      虾饺: { dishName: '虾饺', orders: [early, rushed] }
    })
    expect(ids).toEqual(['a'])
  })

  it('takes the leftover quantity on a selected 订单行 (no FIFO fill)', async () => {
    const leftover = makeOrder({ id: 'a', quantity: 2, table_number: '8' })
    const later = makeOrder({
      id: 'b',
      quantity: 1,
      table_number: '3',
      order_time: '2026-07-23T02:00:00.000Z'
    })
    const { body } = await tableRequest('虾饺', ['a'], {
      虾饺: { dishName: '虾饺', orders: [leftover, later] }
    })
    expect(body.orders).toEqual([
      expect.objectContaining({ order_id: 'a', complete_quantity: 2, original_quantity: 2 })
    ])
  })
})

describe('笼上出餐', () => {
  it('confirms explicit steaming ids, not FIFO of the same dish', async () => {
    const earlier = makeOrder({
      id: 'early',
      table_number: '1',
      order_time: '2026-08-14T10:00:00+08:00'
    })
    const later = makeOrder({
      id: 'late',
      table_number: '2',
      order_time: '2026-08-14T10:10:00+08:00'
    })
    const { ids } = await basketRequest(['late'], [earlier, later])
    expect(ids).toEqual(['late'])
  })

  it('does not hit the server when confirm has no selected cages', async () => {
    const { outcome, body } = await basketRequest([], [makeOrder({ id: 's1' })])
    expect(outcome.submitted).toBe(false)
    expect(body).toBeNull()
  })

  it('flattens mixed dishes into one request of the checked 蒸笼', async () => {
    const dumpling = makeOrder({ id: 'd1', dish_name: '虾饺', table_number: '3' })
    const bun = makeOrder({ id: 'b1', dish_name: '叉烧包', table_number: '4' })
    const dumpling2 = makeOrder({ id: 'd2', dish_name: '虾饺', table_number: '5' })
    const { ids } = await basketRequest(['b1', 'd2'], [dumpling, bun, dumpling2])
    expect(ids).toEqual(['b1', 'd2'])
  })

  it('confirms selected awaiting ids, not the whole 待上笼组', async () => {
    const earlier = makeOrder({
      id: 'a1',
      table_number: '1',
      order_time: '2026-08-14T10:00:00+08:00',
      placement: null
    })
    const later = makeOrder({
      id: 'a2',
      table_number: '2',
      order_time: '2026-08-14T10:10:00+08:00',
      placement: null
    })
    const { ids } = await basketRequest(['a2'], [earlier, later])
    expect(ids).toEqual(['a2'])
  })

  it('ignores selected ids that are not in the cages list', async () => {
    const steaming = makeOrder({ id: 's1', table_number: '9' })
    const { ids } = await basketRequest(['a1', steaming.id], [steaming])
    expect(ids).toEqual(['s1'])
  })
})
