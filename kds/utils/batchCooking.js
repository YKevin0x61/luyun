/**
 * Batch cooking plan — decide target 订单行 up front (per 菜卡 / 选桌 / 蒸笼).
 * With chunkOrders, each key is a chunk (same-dish siblings stay isolated).
 * Confirm flattens the plan into one complete-cooking request (see serveConfirm).
 */

/**
 * @param {object} order
 * @returns {number}
 */
function availableQuantity(order) {
  const quantity = order?.quantity || 1
  const served = order?.servedQuantity || order?.served_quantity || 0
  return Math.max(0, quantity - served)
}

/**
 * @param {object[]} orders
 * @param {number} target
 * @returns {Array<{ order: object, serveQuantity: number }>}
 */
function allocateFifo(orders, target) {
  const pool = [...orders]
    .filter((order) => order && availableQuantity(order) > 0)
    .sort((a, b) => new Date(a.order_time) - new Date(b.order_time))

  const allocations = []
  let remaining = target

  for (const order of pool) {
    if (remaining <= 0) break
    const take = Math.min(availableQuantity(order), remaining)
    if (take <= 0) continue
    allocations.push({ order, serveQuantity: take })
    remaining -= take
  }

  return allocations
}

export function orderLineId(order) {
  return String(order?.id ?? order?._id ?? '')
}

/**
 * Group FIFO allocations into 将出预览 copy: `8桌×2、3桌` (no ×1).
 *
 * @param {Array<{ order: object, serveQuantity: number }>} [allocations]
 * @returns {string}
 */
export function formatServePreview(allocations) {
  if (!Array.isArray(allocations) || allocations.length === 0) return ''

  const groups = []
  for (const item of allocations) {
    const n = Number(item?.serveQuantity) || 0
    if (n <= 0) continue
    const table = String(item?.order?.table_number ?? '')
    const prev = groups.find((group) => group.table === table)
    if (prev) prev.n += n
    else groups.push({ table, n })
  }

  return groups.map((group) => (group.n > 1 ? `${group.table}桌×${group.n}` : `${group.table}桌`)).join('、')
}

/**
 * FIFO 将出预览 for a card’s selected count. Empty / 0 → ''.
 *
 * @param {object[]} orders
 * @param {number} selectedQuantity
 * @returns {string}
 */
export function servePreviewText(orders, selectedQuantity) {
  const qty = Number(selectedQuantity) || 0
  if (qty <= 0) return ''
  return formatServePreview(allocateFifo(orders || [], qty))
}

/**
 * FIFO 将出 订单行 ids for highlighting chips on the card. Empty / 0 → [].
 *
 * @param {object[]} orders
 * @param {number} selectedQuantity
 * @returns {string[]}
 */
export function servePreviewOrderIds(orders, selectedQuantity) {
  const qty = Number(selectedQuantity) || 0
  if (qty <= 0) return []
  return allocateFifo(orders || [], qty)
    .map((item) => orderLineId(item.order))
    .filter(Boolean)
}

function cookingCallFromAllocations(dishName, allocations) {
  if (!allocations.length) return null
  const completeQuantity = allocations.reduce((sum, item) => sum + item.serveQuantity, 0)
  return {
    dishName,
    completeQuantity,
    orders: allocations.map((item) => item.order),
    allocations
  }
}

/**
 * Plan 选桌出餐 from an explicit set of 订单行 ids in one chunk.
 * No FIFO fill; ids outside the chunk are ignored.
 *
 * @param {object} params
 * @param {string[]} params.selectedOrderIds
 * @param {string} params.chunkId
 * @param {Record<string, { dishName: string, orders: object[] }>} params.chunkOrders
 * @returns {Array<{
 *   dishName: string,
 *   completeQuantity: number,
 *   orders: object[],
 *   allocations: Array<{ order: object, serveQuantity: number }>
 * }>}
 */
export function planTablePickCookingCalls({ selectedOrderIds, chunkId, chunkOrders }) {
  if (!Array.isArray(selectedOrderIds) || selectedOrderIds.length === 0) return []
  if (!chunkId || !chunkOrders || typeof chunkOrders !== 'object') return []

  const chunk = chunkOrders[chunkId]
  if (!chunk) return []

  const wanted = new Set(selectedOrderIds.map((id) => String(id)))
  const allocations = []
  for (const order of Array.isArray(chunk.orders) ? chunk.orders : []) {
    const id = orderLineId(order)
    if (!id || !wanted.has(id)) continue
    const take = availableQuantity(order)
    if (take <= 0) continue
    allocations.push({ order, serveQuantity: take })
  }

  const call = cookingCallFromAllocations(chunk.dishName || chunkId, allocations)
  return call ? [call] : []
}

/**
 * Plan 笼上出餐 from an explicit set of 在蒸 订单行 ids.
 * No FIFO fill; ids not present in `cages` are ignored. Mixed dishes
 * stay grouped per 菜名 in the plan; confirm flattens them into one request.
 *
 * @param {object} params
 * @param {string[]} params.selectedOrderIds
 * @param {object[]} params.cages steaming cages (or any order-line list)
 * @returns {Array<{
 *   dishName: string,
 *   completeQuantity: number,
 *   orders: object[],
 *   allocations: Array<{ order: object, serveQuantity: number }>
 * }>}
 */
export function planBasketServeCookingCalls({ selectedOrderIds, cages }) {
  if (!Array.isArray(selectedOrderIds) || selectedOrderIds.length === 0) return []
  if (!Array.isArray(cages)) return []

  const wanted = new Set(selectedOrderIds.map((id) => String(id)))
  const byDish = new Map()

  for (const cage of cages) {
    const id = orderLineId(cage)
    if (!id || !wanted.has(id)) continue
    const take = availableQuantity(cage)
    if (take <= 0) continue
    const dishName = cage.dish_name || ''
    if (!byDish.has(dishName)) byDish.set(dishName, [])
    byDish.get(dishName).push({ order: cage, serveQuantity: take })
  }

  const plan = []
  for (const [dishName, allocations] of byDish) {
    const call = cookingCallFromAllocations(dishName, allocations)
    if (call) plan.push(call)
  }
  return plan
}

/**
 * Plan merged completeCooking calls from selected portion counts.
 *
 * When `chunkOrders` is provided, each selected key is a chunkId and allocation
 * uses only that chunk’s orders (same-dish sibling chunks cannot steal).
 * Without `chunkOrders`, keys are dish names and the pool is `pendingOrders`.
 *
 * @param {object} params
 * @param {Record<string, number>} params.selectedQuantities chunkId or dishName → selected portions
 * @param {Array<object>} params.pendingOrders caller-filtered: current station, pending, today, non-refund
 * @param {Record<string, { dishName: string, orders: object[] }>} [params.chunkOrders]
 * @returns {Array<{
 *   dishName: string,
 *   completeQuantity: number,
 *   orders: object[],
 *   allocations: Array<{ order: object, serveQuantity: number }>
 * }>}
 */
export function planBatchCookingCalls({ selectedQuantities, pendingOrders, chunkOrders }) {
  if (!selectedQuantities || !Array.isArray(pendingOrders)) {
    return []
  }

  const plan = []
  const scoped = chunkOrders != null && typeof chunkOrders === 'object'

  for (const [key, selectedQuantity] of Object.entries(selectedQuantities)) {
    const target = Number(selectedQuantity) || 0
    if (target <= 0) continue

    let dishName
    let sourceOrders
    if (scoped) {
      const chunk = chunkOrders[key]
      if (!chunk) continue
      dishName = chunk.dishName || key
      sourceOrders = Array.isArray(chunk.orders) ? chunk.orders : []
    } else {
      dishName = key
      sourceOrders = pendingOrders.filter(
        (order) => order && order.dish_name === key
      )
    }

    const allocations = allocateFifo(sourceOrders, target)
    const call = cookingCallFromAllocations(dishName, allocations)
    if (call) plan.push(call)
  }

  return plan
}

export default { planBatchCookingCalls, formatServePreview, servePreviewText, servePreviewOrderIds, planTablePickCookingCalls, planBasketServeCookingCalls, orderLineId }
