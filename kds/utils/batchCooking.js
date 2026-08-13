/**
 * Batch cooking plan — decide target orders up front, then one completeCooking call per selection key.
 * With chunkOrders, each key is a chunk (same-dish siblings stay isolated).
 * Keeps kitchen.vue free of serial re-query / race-on-same-order loops.
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
    if (allocations.length === 0) continue

    const completeQuantity = allocations.reduce((sum, item) => sum + item.serveQuantity, 0)
    plan.push({
      dishName,
      completeQuantity,
      orders: allocations.map((item) => item.order),
      allocations
    })
  }

  return plan
}

export default { planBatchCookingCalls }
