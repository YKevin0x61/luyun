/**
 * Batch cooking plan — decide target orders up front, then merge one completeCooking call per dish.
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
 * Plan merged completeCooking calls from selected portion counts.
 *
 * @param {object} params
 * @param {Record<string, number>} params.selectedQuantities dishName → selected portions
 * @param {Array<object>} params.pendingOrders caller-filtered: current station, pending, today, non-refund
 * @returns {Array<{
 *   dishName: string,
 *   completeQuantity: number,
 *   orders: object[],
 *   allocations: Array<{ order: object, serveQuantity: number }>
 * }>}
 */
export function planBatchCookingCalls({ selectedQuantities, pendingOrders }) {
  if (!selectedQuantities || !Array.isArray(pendingOrders)) {
    return []
  }

  const plan = []

  for (const [dishName, selectedQuantity] of Object.entries(selectedQuantities)) {
    const target = Number(selectedQuantity) || 0
    if (target <= 0) continue

    const dishOrders = pendingOrders
      .filter((order) => order && order.dish_name === dishName && availableQuantity(order) > 0)
      .sort((a, b) => new Date(a.order_time) - new Date(b.order_time))

    const allocations = []
    let remaining = target

    for (const order of dishOrders) {
      if (remaining <= 0) break
      const take = Math.min(availableQuantity(order), remaining)
      if (take <= 0) continue
      allocations.push({ order, serveQuantity: take })
      remaining -= take
    }

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
